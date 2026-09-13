# -*- coding: utf-8 -*-
"""LLM context budget for interactive narrative extensions (Narration, History Events)."""
from __future__ import annotations

from services.formslator.resource_budget import compute_translate_budget

# ~24 chapters × (scene ~900 tok + summary ~60 tok) + bible + prior scene + prompts
_NARRATION_NUM_CTX = 32768
_NARRATION_NUM_PREDICT = 4096

# Single encounter presentation + grading response
_HISTORY_EVENTS_NUM_CTX = 16384
_HISTORY_EVENTS_NUM_PREDICT = 3072


def _hardware_ctx_cap() -> int:
    raw = int(compute_translate_budget().get("num_ctx_raw") or 8192)
    return max(8192, min(raw, 65536))


def narrative_llm_options(*, extension: str = "narration") -> dict[str, int]:
    """Higher num_ctx / num_predict for long-running story continuity."""
    hw_cap = _hardware_ctx_cap()
    if extension == "history_events":
        num_ctx = min(_HISTORY_EVENTS_NUM_CTX, hw_cap)
        num_predict = min(_HISTORY_EVENTS_NUM_PREDICT, max(2048, num_ctx // 6))
    else:
        num_ctx = min(_NARRATION_NUM_CTX, hw_cap)
        num_predict = min(_NARRATION_NUM_PREDICT, max(2048, num_ctx // 6))
    return {"num_ctx": num_ctx, "num_predict": num_predict}


def trim_running_log(text: str, *, max_chars: int = 8000) -> str:
    """Keep the most recent bullet entries when the running log grows large."""
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    lines = [ln for ln in t.splitlines() if ln.strip()]
    kept: list[str] = []
    total = 0
    for ln in reversed(lines):
        if total + len(ln) + 1 > max_chars:
            break
        kept.append(ln)
        total += len(ln) + 1
    if not kept:
        return t[-max_chars:]
    return "\n".join(reversed(kept))
