# -*- coding: utf-8 -*-
"""Hardware-aware batch sizing for long-document direct pipeline steps."""
from __future__ import annotations

from services.formslator.resource_budget import batch_sizing_for_ctx, compute_translate_budget


def _run_ctx_floor(profile: dict | None) -> int:
    if not isinstance(profile, dict):
        return 0
    return int(profile.get("_run_ctx_floor") or 0)


def bump_run_ctx_floor(profile: dict | None, num_ctx: int) -> int:
    """Ratchet this run's num_ctx floor upward and return the effective floor.

    Ollama has to reload a model whenever a call's num_ctx differs from what
    it's currently loaded at, so bouncing between tiers within one multi-step
    run (a small file, then a big merge, then another small file) pays a
    reload every time. Once anything in a run needs a bigger context window,
    every later call in that same run reuses it instead of dropping back
    down — a little extra memory instead of repeated reload latency. The
    floor lives on the profile dict, which is a fresh object per workflow
    invocation (see default_profile()), so it naturally resets each run.
    """
    if not isinstance(profile, dict):
        return num_ctx
    floor = max(_run_ctx_floor(profile), int(num_ctx))
    profile["_run_ctx_floor"] = floor
    return floor


def resolve_batch_budget(profile: dict | None, model: str = "") -> dict:
    """num_ctx / max_tokens / char budget from RAM+VRAM (capped for latency)."""
    budget = compute_translate_budget(model or "")
    model_cfg = (profile or {}).get("MODEL") or {}
    if isinstance(model_cfg, dict):
        prof_ctx = int(model_cfg.get("num_ctx") or 0)
        prof_max = int(model_cfg.get("max_tokens") or 0)
        if prof_ctx and 0 < prof_ctx < budget["num_ctx"]:
            # A smaller num_ctx here shrinks how many tokens actually fit in the
            # call — batch_char_budget (chunk size) must shrink with it, or
            # chunks get built for the old, larger ctx and overflow the new one.
            budget = {**budget, **batch_sizing_for_ctx(prof_ctx, raw_ctx=budget["num_ctx_raw"])}
        if prof_max and 0 < prof_max < budget["max_tokens"]:
            budget = {**budget, "max_tokens": prof_max}
    floor = min(_run_ctx_floor(profile), int(budget.get("num_ctx_raw") or budget["num_ctx"]))
    if floor > budget["num_ctx"]:
        budget = {**budget, **batch_sizing_for_ctx(floor, raw_ctx=budget["num_ctx_raw"])}
    return budget


def single_pass_char_limit(budget: dict) -> int:
    """Below this, one streaming LLM call is enough — no batching."""
    return int(budget.get("batch_char_budget", 4000) * 0.9)


def batch_chunk_chars(budget: dict) -> int:
    return int(budget.get("batch_char_budget", 4000))


def max_paragraphs_per_chunk(budget: dict) -> int:
    return int(budget.get("max_batch_paragraphs", 10))


def llm_extra_options(budget: dict) -> dict[str, int]:
    return {
        "num_ctx": int(budget.get("num_ctx", 4096)),
        "num_predict": int(budget.get("max_tokens", 2048)),
    }


_NUM_CTX_TIERS = (4096, 8192, 16384, 32768, 65536, 131072)
_NUM_PREDICT_TIERS = (2048, 4096, 8192, 16384, 32768, 65536)
_CHARS_PER_TOKEN = 3.5


def fit_budget_to_prompt(
    budget: dict, prompt_chars: int, expected_output_chars: int = 0, *, profile: dict | None = None
) -> dict:
    """Bump num_ctx (and num_predict, given expected_output_chars) to fit the workload,
    capped at what this machine's RAM+VRAM can actually support (num_ctx_raw).

    resolve_batch_budget() sizes both from a latency-friendly default, so a large
    prompt (e.g. a wide CSV) or a long expected deliverable (e.g. document_markdown's
    24000-char contract cap) can exceed it even on capable machines — num_ctx overflow
    raises the backend's hard context-overflow error, num_predict overflow silently
    truncates a single-shot writer/synthesizer output instead.

    Pass profile so this call's widening (if any) also raises the run-wide ctx
    floor (see bump_run_ctx_floor) — later steps in the same multi-step run
    then reuse this ctx instead of a smaller one, avoiding a model reload.
    """
    prompt_tokens = int(prompt_chars / _CHARS_PER_TOKEN)
    out_tokens = int(budget.get("max_tokens", 2048))
    if expected_output_chars > 0:
        out_tokens = max(out_tokens, int(expected_output_chars / _CHARS_PER_TOKEN) + 256)

    hw_ceiling = int(budget.get("num_ctx_raw") or budget.get("num_ctx", 4096))
    current_ctx = int(budget.get("num_ctx", 4096))
    needed_ctx = prompt_tokens + out_tokens + 256

    new_ctx = current_ctx
    if needed_ctx > current_ctx:
        new_ctx = next((tier for tier in _NUM_CTX_TIERS if tier >= needed_ctx), _NUM_CTX_TIERS[-1])
    new_ctx = min(new_ctx, max(hw_ceiling, current_ctx))

    if profile is not None:
        new_ctx = min(max(new_ctx, _run_ctx_floor(profile)), max(hw_ceiling, current_ctx))
        bump_run_ctx_floor(profile, new_ctx)

    new_out = int(budget.get("max_tokens", 2048))
    if expected_output_chars > 0:
        new_out = next((tier for tier in _NUM_PREDICT_TIERS if tier >= out_tokens), _NUM_PREDICT_TIERS[-1])
        room = new_ctx - prompt_tokens - 64
        if room > 0:
            new_out = min(new_out, room)

    return {**budget, "num_ctx": new_ctx, "max_tokens": new_out}


def output_exceeds_hardware(budget: dict, prompt_chars: int, expected_output_chars: int) -> bool:
    """True when prompt + expected output can't fit in one call even at this
    machine's max num_ctx — a single-shot completion would truncate no matter
    how high num_predict is tiered, so the caller should batch/section instead."""
    if expected_output_chars <= 0:
        return False
    hw_ceiling = int(budget.get("num_ctx_raw") or budget.get("num_ctx", 4096))
    prompt_tokens = int(prompt_chars / _CHARS_PER_TOKEN)
    out_tokens = int(expected_output_chars / _CHARS_PER_TOKEN) + 256
    return (prompt_tokens + out_tokens + 256) > hw_ceiling


def prompt_fits_hardware(budget: dict, prompt_chars: int, *, min_output_chars: int = 800) -> bool:
    """True when the prompt (plus a minimal response) fits at this machine's max
    num_ctx — used to decide whether a merge/combine call can run in one shot or
    needs to reduce its inputs in stages first."""
    hw_ceiling = int(budget.get("num_ctx_raw") or budget.get("num_ctx", 4096))
    prompt_tokens = int(prompt_chars / _CHARS_PER_TOKEN)
    out_tokens = int(min_output_chars / _CHARS_PER_TOKEN) + 256
    return (prompt_tokens + out_tokens + 256) <= hw_ceiling
