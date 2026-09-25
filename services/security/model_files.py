# -*- coding: utf-8 -*-
"""Model supply-chain rules: safe weight formats only.

Pickle-based weights (.bin/.pt/.pth/.ckpt) can execute code when loaded. Rules:
  1. Refuse a repo whose weights are ONLY pickle (no .safetensors / .gguf / .onnx anywhere).
  2. Force torch to load any remaining pickle checkpoint with ``weights_only=True`` (no arbitrary
     objects) via TORCH_FORCE_WEIGHTS_ONLY_LOAD (set at startup by ``enforce_safe_torch_loading``).
Ollama/LM Studio models are handled by those runtimes (GGUF), not here.
"""
from __future__ import annotations

import os

SAFE_SUFFIXES = (".safetensors", ".gguf", ".onnx")
PICKLE_SUFFIXES = (".bin", ".pt", ".pth", ".ckpt", ".pkl", ".pickle")

_checked: dict[str, tuple[bool, str]] = {}


class UnsafeModelError(RuntimeError):
    pass


def enforce_safe_torch_loading() -> None:
    """Make every torch.load in this process refuse arbitrary pickled objects."""
    os.environ.setdefault("TORCH_FORCE_WEIGHTS_ONLY_LOAD", "1")


def classify_files(files: list[str]) -> tuple[bool, str]:
    """(safe, reason) for a repo file listing. Pure function (unit-tested)."""
    names = [f.lower() for f in files]
    has_safe = any(n.endswith(SAFE_SUFFIXES) for n in names)
    pickle = [f for f in files if f.lower().endswith(PICKLE_SUFFIXES)]
    if pickle and not has_safe:
        return False, "only pickle weights (" + ", ".join(pickle[:3]) + ")"
    return True, ""


def check_repo(repo_id: str, *, files: list[str] | None = None) -> tuple[bool, str]:
    """Check a Hugging Face repo id. Listing failures (offline) do not block: a repo LOMA already
    downloaded is re-checked from its local cache listing when possible, otherwise allowed."""
    repo_id = (repo_id or "").strip()
    if not repo_id or os.path.isdir(repo_id) or os.path.isfile(repo_id):
        return True, ""
    if files is None:
        if repo_id in _checked:
            return _checked[repo_id]
        try:
            from huggingface_hub import list_repo_files

            files = list_repo_files(repo_id)
        except Exception:
            return True, ""
    result = classify_files(files)
    _checked[repo_id] = result
    return result


def require_safe_repo(repo_id: str) -> None:
    from services.security.policy_gate import decide

    verdict = decide("download_model", repo_id=repo_id)
    ok, reason = verdict.allowed, verdict.reason
    if not ok:
        raise UnsafeModelError(f"Model '{repo_id}' was not loaded: {reason}. Only safetensors/GGUF/ONNX weights are allowed.")
