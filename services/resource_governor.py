# -*- coding: utf-8 -*-
"""Resource governor — offload LLM VRAM before media tasks on constrained hardware."""
from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator

from services.resources import policies
from services.system.profiler import get_system_profile

logger = logging.getLogger(__name__)

# On a GPU too small to hold the LLM and a media model at once, media generation and
# LLM work must not run concurrently or they oversubscribe VRAM (WDDM then pages over
# PCIe and both crawl / OOM). A media task takes the GPU exclusively; chat waits for it.
# LLM tasks never hold a lock — so multiple LLM calls (e.g. Arena's parallel race) still
# run concurrently, and there's no risk of a nested-acquire deadlock.
_media_lock = threading.Lock()
_media_idle = threading.Event()
_media_idle.set()  # idle by default
_media_depth = threading.local()  # nested-acquire depth per thread — see acquire()

# Grace-window keepalive: after a media task finishes, the pipeline isn't unloaded
# immediately — a timer is scheduled instead, so a second media request arriving within
# GOVERNOR_MEDIA_KEEPALIVE_SECONDS reuses the still-loaded pipeline (skipping reload/
# re-parse entirely) rather than paying the full cold-load cost again. An LLM/chat
# request arriving during the window evicts the pipeline immediately instead of
# waiting — see acquire()'s LLM_TASKS branch — so this never delays chat responsiveness,
# only skips redundant reloads between back-to-back media requests.
_grace_lock = threading.Lock()
_grace_timer: threading.Timer | None = None


def _log(msg: str) -> None:
    logger.info(msg)
    print(f"[Governor] {msg}")
    try:
        from services.session import state

        state.add_log(msg)
    except Exception:
        pass


class ResourceGovernor:
    _llm_unloaded_for_media = False
    _pending_llm_reload = False
    _warm_llm_models: list[str] = []

    @classmethod
    def is_aggressive(cls) -> bool:
        profile = get_system_profile()
        if profile.total_ram_gb < policies.TIGHT_RAM_GB:
            return True
        if profile.available_ram_gb < policies.MIN_AVAILABLE_RAM_GB:
            return True
        if profile.gpu_backend == "cuda" and profile.vram_gb < policies.TIGHT_VRAM_GB:
            return True
        if profile.gpu_backend == "cpu":
            return True
        return False

    @classmethod
    def should_offload_llm_for_media(cls) -> bool:
        return cls.is_aggressive()

    @classmethod
    def offload_llm(cls) -> list[str]:
        from services.providers.registry import get_active_provider

        provider = get_active_provider()
        _log(f"Offloading LLM from VRAM ({provider.label})…")
        unloaded = provider.unload_all()
        cls._llm_unloaded_for_media = True
        cls._warm_llm_models = list(unloaded)
        if unloaded:
            _log(f"Unloaded models: {', '.join(unloaded)}")
        else:
            _log("No loaded LLM models reported (daemon may already be idle).")
        cls._clear_torch_cache()
        if unloaded:
            # Ollama frees VRAM asynchronously after the unload call returns. Wait for it
            # to actually settle, else the media pipeline loads into still-occupied VRAM and
            # the driver pages over PCIe (defeating the whole point of offloading).
            cls._wait_for_vram_free()
        return unloaded

    @classmethod
    def _wait_for_vram_free(cls, timeout_s: float = 8.0) -> None:
        try:
            import time

            import torch

            if not torch.cuda.is_available():
                return
            deadline = time.time() + timeout_s
            last = -1.0
            stable = 0
            while time.time() < deadline:
                free_gb = torch.cuda.mem_get_info()[0] / (1024**3)
                # Consider VRAM settled once free stops climbing for two checks in a row.
                if last >= 0 and free_gb <= last + 0.05:
                    stable += 1
                    if stable >= 2:
                        _log(f"VRAM freed: {round(free_gb, 1)} GB available.")
                        return
                else:
                    stable = 0
                last = free_gb
                time.sleep(0.4)
        except Exception as exc:
            logger.debug("VRAM wait: %s", exc)

    @classmethod
    def _clear_torch_cache(cls) -> None:
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
                _log("PyTorch CUDA cache cleared.")
                return
            backends = getattr(torch, "backends", None)
            mps = getattr(backends, "mps", None) if backends else None
            if mps is not None and mps.is_available() and hasattr(torch, "mps"):
                torch.mps.empty_cache()
                _log("PyTorch MPS cache cleared.")
        except Exception as exc:
            logger.debug("torch cache clear: %s", exc)

    @classmethod
    def release_media(cls) -> None:
        try:
            from services.image_generation import pipeline_is_loaded, unload_pipeline
        except Exception:
            return
        if not pipeline_is_loaded():
            return
        _log("Releasing diffusion / media pipeline memory…")
        unload_pipeline()
        cls._clear_torch_cache()

    @classmethod
    def _cancel_grace_timer(cls) -> None:
        global _grace_timer
        with _grace_lock:
            if _grace_timer is not None:
                _grace_timer.cancel()
                _grace_timer = None

    @classmethod
    def _grace_release(cls) -> None:
        """Timer target: the keepalive window elapsed with no follow-up media request —
        do the deferred release now."""
        global _grace_timer
        with _grace_lock:
            _grace_timer = None
        cls.release_media()
        cls.restore_llm_hint()

    @classmethod
    def _schedule_grace_release(cls) -> None:
        global _grace_timer
        timer = threading.Timer(policies.MEDIA_KEEPALIVE_SECONDS, cls._grace_release)
        timer.daemon = True
        with _grace_lock:
            _grace_timer = timer
        timer.start()

    @classmethod
    def evict_warm_media(cls) -> None:
        """Force an immediate release of any pipeline left warm by the grace window —
        called before an LLM/chat task proceeds, so it never loads into VRAM the media
        pipeline is still quietly holding. Safe to call unconditionally: cancelling an
        absent timer and releasing an already-unloaded pipeline are both no-ops."""
        cls._cancel_grace_timer()
        cls.release_media()
        cls.restore_llm_hint()

    @classmethod
    def restore_llm_hint(cls) -> None:
        if cls._llm_unloaded_for_media:
            _log("LLM was unloaded for image generation — next chat will reload the model (may take a few seconds).")
            cls._pending_llm_reload = True
            cls._llm_unloaded_for_media = False

    @classmethod
    def _needs_gpu_serialization(cls) -> bool:
        """Serialize media vs LLM only on a GPU too small to hold both at once. Big GPUs
        run them concurrently; CPU-only machines don't oversubscribe VRAM, so no lock."""
        profile = get_system_profile()
        if profile.gpu_backend == "cuda":
            return profile.vram_gb < policies.TIGHT_VRAM_GB
        if profile.gpu_backend == "mps":
            # Unified memory: only serialize on genuinely memory-constrained Macs —
            # there's no separate VRAM pool to oversubscribe the way a small discrete
            # GPU has.
            return profile.total_ram_gb < policies.TIGHT_RAM_GB
        return False

    @classmethod
    @contextmanager
    def acquire(cls, task_type: str) -> Generator[None, None, None]:
        task = (task_type or "").lower()
        serialize = cls._needs_gpu_serialization()

        if task in policies.MEDIA_TASKS:
            # Reentrant on the current thread: a caller that wants several media calls
            # to share one loaded pipeline (e.g. a presentation's per-slide image loop)
            # wraps them all in one outer acquire("image_gen"); each inner generate_image()
            # call's own acquire() then sees depth > 0 and just yields — it neither
            # re-locks _media_lock (which isn't reentrant and would deadlock) nor
            # releases/reloads the pipeline until the outermost context exits.
            depth = getattr(_media_depth, "value", 0)
            if depth > 0:
                _media_depth.value = depth + 1
                try:
                    yield
                finally:
                    _media_depth.value = depth
                return
            _media_depth.value = 1
            # A pending grace-window release means a pipeline from an earlier media
            # request may still be warm — cancel that timer now (rather than letting it
            # fire mid-use) since this request is either about to reuse that same warm
            # pipeline directly or replace it.
            cls._cancel_grace_timer()
            try:
                if serialize:
                    _media_lock.acquire()  # exclusive among media tasks
                    _media_idle.clear()    # signal chat to hold off
                try:
                    profile = get_system_profile()
                    aggressive = cls.is_aggressive()
                    # Skip re-offloading if the LLM is already known offloaded from a
                    # still-warm previous media request (the grace timer we just
                    # cancelled) — nothing has reloaded it since, so there's nothing to
                    # do here, and skipping this avoids a redundant provider round-trip.
                    if (aggressive or cls.should_offload_llm_for_media()) and not cls._llm_unloaded_for_media:
                        cls.offload_llm()
                    _log(f"Acquired resources for {task} (RAM {profile.available_ram_gb} GB free).")
                    yield
                finally:
                    # Deferred, not immediate: see GOVERNOR_MEDIA_KEEPALIVE_SECONDS — a
                    # follow-up media request within the window reuses this pipeline
                    # (and cancels this timer above); an LLM/chat request evicts it
                    # immediately instead of waiting, via evict_warm_media() below.
                    cls._schedule_grace_release()
                    if serialize:
                        _media_idle.set()
                        _media_lock.release()
            finally:
                _media_depth.value = 0
        elif task in policies.LLM_TASKS:
            # Wait (bounded) for any in-progress media task to release the GPU first, so the
            # LLM doesn't reload into VRAM that's still holding a diffusion/TTS model.
            if serialize and not _media_idle.is_set():
                _log("GPU busy with media generation — chat resumes when it finishes…")
                _media_idle.wait(timeout=180)
            # A media pipeline may still be warm-but-idle from the grace window —
            # evict it now so this LLM task doesn't load into VRAM it's still holding.
            cls.evict_warm_media()
            if cls._pending_llm_reload:
                models = ", ".join(cls._warm_llm_models) if cls._warm_llm_models else "chat model"
                _log(f"Loading {models} into VRAM (was unloaded for image generation)…")
                cls._pending_llm_reload = False
            yield
        else:
            yield
