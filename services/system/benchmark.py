# -*- coding: utf-8 -*-
"""Live tokens/sec benchmark for a model on the current hardware.

Used to estimate Plan-mode wall-clock time. Deliberately measured live (a short
real generation) rather than read from persisted history, so it reflects
whatever hardware and model are active right now. Cached per model per
process — re-benchmarking on every single plan run would add latency for no
benefit within one session.
"""
from __future__ import annotations

import time

_cache: dict[str, float] = {}

_PROBE_PROMPT = "Reply with one word: ready."
_PROBE_NUM_PREDICT = 64


def benchmark_tokens_per_second(model: str, *, refresh: bool = False) -> float | None:
    """Measure this model's generation speed on this machine. None if unreachable."""
    model = (model or "").strip()
    if not model:
        return None
    if not refresh and model in _cache:
        return _cache[model]

    from services import llm_bridge

    start = time.time()
    try:
        response = llm_bridge.chat(
            model=model,
            messages=[{"role": "user", "content": _PROBE_PROMPT}],
            think=False,
            options={"num_predict": _PROBE_NUM_PREDICT, "temperature": 0.0},
        )
    except Exception:
        return None
    elapsed = max(time.time() - start, 0.001)

    eval_count = response.get("eval_count") if isinstance(response, dict) else None
    eval_duration_ns = response.get("eval_duration") if isinstance(response, dict) else None
    if eval_count and eval_duration_ns:
        tokens_per_second = eval_count / (eval_duration_ns / 1e9)
    else:
        content = ""
        if isinstance(response, dict):
            content = ((response.get("message") or {}).get("content")) or ""
        approx_tokens = max(len(content) / 4, 1)
        tokens_per_second = approx_tokens / elapsed

    tokens_per_second = max(tokens_per_second, 0.1)
    _cache[model] = tokens_per_second
    return tokens_per_second


def clear_benchmark_cache() -> None:
    _cache.clear()
