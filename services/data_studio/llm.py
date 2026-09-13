# -*- coding: utf-8 -*-
"""Synchronous LLM text helper for Data Studio (mirrors extensions/research/llm.py).

Kept here so the service layer never sends full row data to the model — callers pass
schema/profile text only. Resolves the active profile + general model exactly like the
rest of LOMA so token accounting and thinking settings stay consistent.
"""
from __future__ import annotations

import re
from typing import Callable


def _profile_and_model() -> tuple[dict, str]:
    from pipeline.base.profile_pack import load_profile
    from services.model_router import resolve_general_model
    from services.session import state

    profile_id = (state.current_settings or {}).get("active_profile", "none")
    prof = load_profile(profile_id) or {}
    return prof, resolve_general_model(prof)


def chat_text(
    messages: list[dict[str, str]],
    *,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    """Run one non-streaming chat turn and return the assistant text."""
    from pipeline.capability_runtime.chat_runner import generate_text_sync
    from pipeline.direct.batch_budget import llm_extra_options, resolve_batch_budget
    from pipeline.state_machine import extension_processing

    prof, model = _profile_and_model()
    extra = llm_extra_options(resolve_batch_budget(prof, model))
    sink = None
    if log_fn:

        class _Sink:
            def log(self, msg: str) -> None:
                log_fn(msg)

        sink = _Sink()
    with extension_processing():
        return generate_text_sync(
            prof,
            model,
            messages,
            disable_thinking=True,
            sink=sink,
            extra_options=extra,
        )


def strip_json_fences(raw: str) -> str:
    """Extract a JSON object/array from a possibly fenced LLM reply."""
    text = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    return text
