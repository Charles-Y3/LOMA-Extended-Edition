# -*- coding: utf-8 -*-
"""Context window budget estimation for ingest."""
from __future__ import annotations


def context_char_budget(profile: dict | None) -> int:
    from services.context_selector import char_budget_from_profile

    return char_budget_from_profile(profile)


def estimate_prompt_overhead_chars(*, message_count: int = 4, query_chars: int = 0) -> int:
    """Reserve space for system, history, and query outside source body."""
    return 8_000 + min(message_count, 12) * 600 + query_chars
