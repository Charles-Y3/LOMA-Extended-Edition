# -*- coding: utf-8 -*-
"""Collect RAM, VRAM, GPU, CPU, and disk stats for setup recommendations and resource governor."""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

import psutil

logger = logging.getLogger(__name__)

_cached_profile: SystemProfile | None = None
_smi_probe_cache: tuple[str, float] | None = None

SYSTEM_RESERVE_GB = 1.5
GPU_WEIGHT = 0.85
SAFETY_FACTOR = 0.75
FREE_RAM_HEADROOM_GB = 2.0

# Apple Silicon has no separate VRAM pool — the GPU addresses the same unified memory
# as the CPU. Apple's own Metal guidance (recommendedMaxWorkingSetSize) leaves headroom
# for the OS and other apps rather than handing the GPU the entire pool; this fraction
# mirrors that same margin (also the convention used by llama.cpp/MLX on Apple Silicon).
MPS_USABLE_FRACTION = 0.75


def resolve_torch_device() -> str:
    """Best available torch device for model placement: 'cuda' > 'mps' > 'cpu'.
    Single source of truth so image/music/voice-clone services agree with each other
    (and with the hardware profile) on what backend is actually in use."""
    import os

    forced = os.environ.get("LOMA_TORCH_DEVICE", "").strip().lower()
    if forced in ("cpu", "cuda", "mps"):
        # Escape hatch / CI hook: GitHub's virtualized Mac GPU rejects 4 GiB Metal buffers
        # ("Invalid buffer size"), so the packaged-app tests force CPU there.
        return forced
    try:
        import torch
    except ImportError:
        return "cpu"
    try:
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    backends = getattr(torch, "backends", None)
    mps = getattr(backends, "mps", None) if backends else None
    try:
        if mps is not None and mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


@dataclass
class SystemProfile:
    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0
    cpu_cores: int = 0
    gpu_name: str = ""
    vram_gb: float = 0.0
    # cuda_available is strictly "torch can actually run on CUDA". A GPU can be
    # physically present (gpu_present) yet unusable because the installed torch is a
    # CPU-only build — those are different facts, so budgets/precision never assume a
    # GPU that torch can't touch. Both fields are CUDA-specific (used by the "GPU
    # present but unused — install CUDA torch" wizard prompt); they intentionally do
    # NOT cover Apple Silicon/MPS, which has no equivalent "install a different wheel"
    # fix — see gpu_backend for the backend-agnostic signal.
    cuda_available: bool = False
    gpu_present: bool = False
    gpu_cc_major: int = 0  # CUDA compute-capability major (Ampere=8+ has native bfloat16)
    # "cuda" | "mps" (Apple Silicon) | "cpu" — the backend torch will actually place
    # models on. Single source of truth for device selection and Mac-aware budgeting;
    # prefer this over cuda_available for anything that should also treat MPS as a GPU.
    gpu_backend: str = "cpu"
    disk_free_gb: float = 0.0
    platform: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_ram_gb": self.total_ram_gb,
            "available_ram_gb": self.available_ram_gb,
            "cpu_cores": self.cpu_cores,
            "gpu_name": self.gpu_name,
            "vram_gb": self.vram_gb,
            "cuda_available": self.cuda_available,
            "gpu_present": self.gpu_present,
            "gpu_cc_major": self.gpu_cc_major,
            "gpu_backend": self.gpu_backend,
            "disk_free_gb": self.disk_free_gb,
            "platform": self.platform,
        }

    @property
    def gpu_present_but_unused(self) -> bool:
        """A CUDA GPU exists but torch can't use it (CPU-only torch build installed).
        CUDA-specific by design — the wizard's fix-it action installs a CUDA wheel,
        which doesn't apply on macOS."""
        return self.gpu_present and not self.cuda_available

    @property
    def supports_bf16(self) -> bool:
        """Native bfloat16 needs Ampere (compute capability 8.0+); Turing/older use fp16."""
        return self.cuda_available and self.gpu_cc_major >= 8

    def fits_ram(self, min_ram_gb: float) -> bool:
        return self.total_ram_gb >= min_ram_gb

    def fits_vram(self, min_vram_gb: float) -> bool:
        if min_vram_gb <= 0:
            return True
        if self.gpu_backend == "cuda":
            return self.cuda_available and self.vram_gb >= min_vram_gb
        if self.gpu_backend == "mps":
            # Unified memory: no separate VRAM pool — gate on the usable share of
            # total RAM instead of a fabricated vram_gb figure.
            return self.total_ram_gb * MPS_USABLE_FRACTION >= min_vram_gb
        return False

    def fits_hardware(self, min_ram_gb: float, min_vram_gb: float = 0) -> bool:
        return self.fits_ram(min_ram_gb) and self.fits_vram(min_vram_gb)


def effective_memory_gb(profile: SystemProfile) -> float:
    """llmfit-style usable memory budget (available RAM + weighted VRAM minus reserve).
    On Apple Silicon, vram_gb is a display-only estimate carved out of the same unified
    pool as available_ram_gb — adding it here would double-count that memory, so only
    CUDA contributes a separate pool."""
    gpu_contrib = profile.vram_gb * GPU_WEIGHT if profile.gpu_backend == "cuda" else 0.0
    return max(0.0, profile.available_ram_gb + gpu_contrib - SYSTEM_RESERVE_GB)


def hardware_budget_gb(profile: SystemProfile) -> float:
    if profile.gpu_backend == "cuda":
        return profile.total_ram_gb + profile.vram_gb
    return profile.total_ram_gb


def gpu_memory_contribution_gb(profile: SystemProfile | None = None) -> float:
    """Standalone GPU-memory figure for ad hoc budgets outside the llmfit-style
    effective_memory_gb() path (e.g. formslator/arena context sizing). CUDA: the GPU's
    own VRAM pool. MPS/CPU: 0 — unified memory is already counted via RAM; adding it
    again here would double-count the same physical memory."""
    profile = profile or get_system_profile()
    return profile.vram_gb if profile.gpu_backend == "cuda" else 0.0


def hardware_tier(profile: SystemProfile) -> int:
    budget = hardware_budget_gb(profile)
    if budget <= 8:
        return 1
    if budget <= 16:
        return 2
    return 3


def _probe_torch_cuda() -> tuple[bool, str, float, int]:
    try:
        import torch

        if not torch.cuda.is_available():
            return False, "", 0.0, 0
        name = torch.cuda.get_device_name(0) if torch.cuda.device_count() else ""
        props = torch.cuda.get_device_properties(0)
        vram = round(props.total_memory / (1024**3), 1)
        cc_major = int(getattr(props, "major", 0) or 0)
        return True, name, vram, cc_major
    except Exception as exc:
        logger.debug("torch CUDA probe failed: %s", exc)
        return False, "", 0.0, 0


def _probe_nvidia_smi() -> tuple[str, float]:
    global _smi_probe_cache
    if _smi_probe_cache is not None:
        return _smi_probe_cache
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            _smi_probe_cache = ("", 0.0)
            return _smi_probe_cache
        line = result.stdout.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        name = parts[0] if parts else ""
        vram_mb = float(parts[1]) if len(parts) > 1 else 0.0
        _smi_probe_cache = (name, round(vram_mb / 1024, 1))
        return _smi_probe_cache
    except Exception as exc:
        logger.debug("nvidia-smi probe failed: %s", exc)
        _smi_probe_cache = ("", 0.0)
        return _smi_probe_cache


def _probe_torch_mps() -> bool:
    try:
        import torch

        backends = getattr(torch, "backends", None)
        mps = getattr(backends, "mps", None) if backends else None
        return bool(mps and mps.is_available())
    except Exception as exc:
        logger.debug("torch MPS probe failed: %s", exc)
        return False


def _probe_apple_chip_name() -> str:
    if sys.platform != "darwin":
        return ""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        return result.stdout.strip()
    except Exception as exc:
        logger.debug("Apple chip name probe failed: %s", exc)
        return ""


def collect_system_profile() -> SystemProfile:
    vm = psutil.virtual_memory()
    total_ram_gb = round(vm.total / (1024**3), 1)
    cuda_ok, gpu_name, vram_gb, cc_major = _probe_torch_cuda()
    gpu_present = cuda_ok
    gpu_backend = "cuda" if cuda_ok else "cpu"
    # torch can't use CUDA (or is a CPU-only build): still detect a physical NVIDIA GPU
    # via nvidia-smi so we can tell the user "GPU present but unused" and offer to fix it.
    if not cuda_ok:
        smi_name, smi_vram = _probe_nvidia_smi()
        if smi_name:
            gpu_name = smi_name
            vram_gb = smi_vram
            gpu_present = True

    if gpu_backend == "cpu" and _probe_torch_mps():
        gpu_backend = "mps"
        gpu_name = _probe_apple_chip_name() or "Apple Silicon (Metal)"
        # Display-only estimate — Apple Silicon has no separate VRAM pool to query;
        # see MPS_USABLE_FRACTION and fits_vram()/hardware_budget_gb() for why this
        # figure is never added on top of RAM elsewhere.
        vram_gb = round(total_ram_gb * MPS_USABLE_FRACTION, 1)

    disk_free = 0.0
    try:
        usage = shutil.disk_usage(".")
        disk_free = round(usage.free / (1024**3), 1)
    except Exception:
        pass

    return SystemProfile(
        total_ram_gb=total_ram_gb,
        available_ram_gb=round(vm.available / (1024**3), 1),
        cpu_cores=psutil.cpu_count(logical=True) or 0,
        gpu_name=gpu_name,
        vram_gb=vram_gb,
        cuda_available=cuda_ok,  # strictly torch-usable — no longer true just because a GPU exists
        gpu_present=gpu_present,
        gpu_cc_major=cc_major,
        gpu_backend=gpu_backend,
        disk_free_gb=disk_free,
        platform=sys.platform,
    )


def get_system_profile(*, refresh: bool = False) -> SystemProfile:
    global _cached_profile
    if _cached_profile is None or refresh:
        _cached_profile = collect_system_profile()
    return _cached_profile


def refresh_system_profile() -> SystemProfile:
    return get_system_profile(refresh=True)
