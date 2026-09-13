# -*- coding: utf-8 -*-
"""Context selection helpers (mode inference and budgets)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SelectionMode = Literal["qa", "summarize", "translate", "general"]

_DEFAULT_CHAR_BUDGET = 48_000
_CHARS_PER_TOKEN_ESTIMATE = 4

# Hint words formerly here now live in pipeline/query_intent_i18n.py's CONCEPTS
# ("verb_summarize", "verb_translate") — one shared, multilingual source. See
# CLAUDE.md section 8.


@dataclass
class ContextSelectionResult:
    text: str
    was_truncated: bool
    total_source_chars: int
    selected_chunk_count: int
    total_chunk_count: int
    mode: SelectionMode


def infer_selection_mode(query: str) -> SelectionMode:
    from pipeline.query_intent_i18n import find_language_target, matches

    lower = (query or "").lower()
    if matches(lower, "verb_translate") or find_language_target(lower) is not None:
        return "translate"
    if matches(lower, "verb_summarize"):
        return "summarize"
    return "qa"


def char_budget_from_profile(profile: dict | None) -> int:
    """Estimate how many characters of source text may be injected into the prompt."""
    if not profile:
        return _DEFAULT_CHAR_BUDGET
    model = profile.get("MODEL") or {}
    explicit = model.get("context_char_budget")
    if isinstance(explicit, (int, float)) and explicit > 1000:
        return int(explicit)

    tokens = model.get("context_window_tokens") or model.get("num_ctx")
    if isinstance(tokens, (int, float)) and tokens > 512:
        return int(tokens * _CHARS_PER_TOKEN_ESTIMATE * 0.55)

    return _DEFAULT_CHAR_BUDGET
