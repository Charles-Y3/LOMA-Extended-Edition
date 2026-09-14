# -*- coding: utf-8 -*-
"""Remember the last text the user typed in a prompt (not model-facing wrappers)."""
from __future__ import annotations

from services.session import state


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
    # Every actual caller of remember_typed_prompt() passes the plain typed
    # instruction, never the excerpt-wrapped query (see viewer_query.py,
    # highlight_runner.py, viewer_selection.py, preview_workspace.py,
    # handlers.py) — this branch only guards against a wrapper ever landing
    # here some other way. is_highlight_query() is the shared, locale-aware
    # detector (pipeline/direct/highlight_excerpt.py) — this used to be its
    # own hardcoded English-only literal ("Regarding the following excerpt
    # from"), which silently never matched a non-English UI's translated
    # chat.excerpt_prefix wording, same bug as highlight_excerpt.py had.
    from pipeline.direct.highlight_excerpt import is_highlight_query

    if is_highlight_query(stored):
        parts = stored.split("\n\n")
        if len(parts) >= 3:
            return parts[-1].strip()
    return stored
