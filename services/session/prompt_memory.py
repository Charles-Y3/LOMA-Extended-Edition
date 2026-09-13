# -*- coding: utf-8 -*-
"""Remember the last text the user typed in a prompt (not model-facing wrappers)."""
from __future__ import annotations

from services.session import state

_EXCERPT_PREFIX = "Regarding the following excerpt from"


def last_typed_prompt() -> str:
    """Prompt text only — never the excerpt wrapper sent to the model."""
    return _prompt_from_stored(state.last_user_instruction)


def remember_typed_prompt(text: str) -> None:
    prompt = (text or "").strip()
    if prompt:
        state.last_user_instruction = prompt


def _prompt_from_stored(stored: str) -> str:
    stored = (stored or "").strip()
    if not stored:
        return ""
    if stored.startswith(_EXCERPT_PREFIX):
        parts = stored.split("\n\n")
        if len(parts) >= 3:
            return parts[-1].strip()
    return stored
