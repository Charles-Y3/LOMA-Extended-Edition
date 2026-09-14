# -*- coding: utf-8 -*-
"""Auto-detect whether a query wants a direct answer or multi-document synthesis.

Replaces the old explicit Ask/Analyze task picker — the single mode dropdown no longer
asks the user to choose framing, so synthesize()'s two system prompts (narrow-answer vs
broad-synthesis) are now selected from the query's own wording instead.
"""
from __future__ import annotations

def is_analysis_style_query(query: str, *, default_analysis: bool = False) -> bool:
    """True → use the broad multi-document synthesis prompt; False → direct-answer prompt.

    `default_analysis` lets a caller bias the no-keyword-match case either way (e.g.
    "deep" mode leans toward thoroughness by default, everything else leans direct)."""
    from pipeline.query_intent_i18n import matches

    q = (query or "").lower()
    if not q.strip():
        return default_analysis
    return matches(q, "analysis_style_query") or default_analysis
