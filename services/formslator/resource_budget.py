# -*- coding: utf-8 -*-
"""Dynamic num_ctx / batch budget from combined system RAM + GPU VRAM."""
from __future__ import annotations

# High RAM does not mean larger ctx is faster — very large num_ctx slows Ollama a lot.
_MAX_NUM_CTX = 8192
_MAX_BATCH_CHARS = 14_000
_MAX_BATCH_PARAGRAPHS = 10


def _ram_gb() -> float:
    try:
        import psutil

        return float(psutil.virtual_memory().total) / (1024**3)
    except Exception:
        return 8.0


def _vram_gb() -> float:
    from services.system.profiler import gpu_memory_contribution_gb

    return gpu_memory_contribution_gb()


def batch_sizing_for_ctx(num_ctx: int, *, raw_ctx: int | None = None) -> dict:
    """Derive max_tokens/batch_char_budget for a given num_ctx.

    Shared by compute_translate_budget's hardware sizing and by any caller
    (e.g. resolve_batch_budget) that shrinks num_ctx afterward — the chunk
    size a batch is built at must always match the num_ctx that call will
    actually run with, or a chunk sized for a larger ctx overflows the
    smaller one at request time.
    """
    raw_ctx = raw_ctx if raw_ctx is not None else num_ctx
    max_tokens = min(max(2048, num_ctx // 4), 4096)
    prompt_reserve = int(num_ctx * 0.35)
    output_reserve = max_tokens
    batch_tokens = max(512, num_ctx - prompt_reserve - output_reserve - 256)
    raw_batch_chars = int(batch_tokens * 2.5)
    batch_char_budget = min(raw_batch_chars, _MAX_BATCH_CHARS)

    notes: list[str] = []
    if num_ctx < raw_ctx:
        notes.append(f"num_ctx capped {raw_ctx:,} → {num_ctx:,} for faster local inference")
    if batch_char_budget < raw_batch_chars:
        notes.append(f"batch char budget capped {raw_batch_chars:,} → {batch_char_budget:,}")
    notes.append(f"max {_MAX_BATCH_PARAGRAPHS} paragraphs per batch call")

    return {
        "num_ctx": num_ctx,
        "num_ctx_raw": raw_ctx,
        "max_tokens": max_tokens,
        "batch_char_budget": batch_char_budget,
        "batch_char_budget_raw": raw_batch_chars,
        "max_batch_paragraphs": _MAX_BATCH_PARAGRAPHS,
        "budget_notes": notes,
    }


def compute_translate_budget(model_name: str = "") -> dict:
    """
    Scale Ollama num_ctx and batch size from combined RAM+VRAM.
    Capped for inference speed (large num_ctx hurts latency per call).
    """
    del model_name
    ram = _ram_gb()
    vram = _vram_gb()
    combined = ram + vram

    if combined >= 48:
        raw_ctx = 65536
    elif combined >= 32:
        raw_ctx = 32768
    elif combined >= 16:
        raw_ctx = 16384
    elif combined >= 8:
        raw_ctx = 8192
    else:
        raw_ctx = 4096

    num_ctx = min(raw_ctx, _MAX_NUM_CTX)
    sizing = batch_sizing_for_ctx(num_ctx, raw_ctx=raw_ctx)

    return {
        "memory_gb": round(combined, 1),
        "ram_gb": round(ram, 1),
        "vram_gb": round(vram, 1),
        **sizing,
    }
