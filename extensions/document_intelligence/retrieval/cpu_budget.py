# -*- coding: utf-8 -*-
"""CPU-only-aware context ceiling for Document Intelligence's Deep/Agentic modes.

The shared resolve_batch_budget()/compute_translate_budget() chain (used by formslator
and the direct pipeline too) sizes num_ctx from combined RAM+VRAM — a CPU-only machine
with a lot of RAM gets scaled into the same context tier as a GPU machine, even though
CPU token throughput doesn't scale with RAM the way memory capacity does. This module
adds a CPU-only-specific ceiling on top, kept local to document_intelligence so the
shared budget functions (and formslator/direct-pipeline translation, which already rely
on them) stay untouched.
"""
from __future__ import annotations

_CPU_ONLY_CTX_CAP = 8192
_VERY_SLOW_CTX_CAP = 4096
_VERY_SLOW_TOKENS_PER_SECOND = 3.0


def is_cpu_only() -> bool:
    from services.system.profiler import get_system_profile

    return get_system_profile().gpu_backend == "cpu"


def effective_ctx_ceiling_tokens(model: str, *, tokens_per_second: float | None = None) -> int:
    """Context ceiling in tokens, tightened for CPU-only hardware (and further for
    observed-very-slow throughput) regardless of how much RAM alone would otherwise
    justify."""
    from pipeline.direct.batch_budget import resolve_batch_budget

    budget = resolve_batch_budget(None, model)
    ceiling = int(budget.get("num_ctx_raw") or budget.get("num_ctx", 4096))
    if not is_cpu_only():
        return ceiling
    ceiling = min(ceiling, _CPU_ONLY_CTX_CAP)
    if tokens_per_second is not None and tokens_per_second < _VERY_SLOW_TOKENS_PER_SECOND:
        ceiling = min(ceiling, _VERY_SLOW_CTX_CAP)
    return ceiling


def tighten_for_cpu(cfg: dict) -> dict:
    """Scaled-down max_sources/retrieval_depth for CPU-only hardware — only applied to
    deep_extras and agentic passes (plain ask/analyse are unaffected)."""
    if not is_cpu_only():
        return cfg
    out = dict(cfg)
    out["max_sources"] = min(int(cfg.get("max_sources") or 8), 5)
    out["retrieval_depth"] = min(int(cfg.get("retrieval_depth") or 40), 25)
    return out
