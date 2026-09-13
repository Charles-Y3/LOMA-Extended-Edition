# -*- coding: utf-8 -*-
"""Execution mode identifiers (Direct / Plan flow)."""
from __future__ import annotations

VALID_EXECUTION_MODES = frozenset({"direct", "plan"})

# Legacy alias
VALID_CHAT_EXECUTION_MODES = VALID_EXECUTION_MODES

_LEGACY_TO_MODE = {
    "agentic": "plan",
}


def normalize_execution_mode(value: str | None) -> str:
    key = (value or "direct").strip().lower().replace("-", "_")
    if key in _LEGACY_TO_MODE:
        return _LEGACY_TO_MODE[key]
    if key in VALID_EXECUTION_MODES:
        return key
    return "direct"
