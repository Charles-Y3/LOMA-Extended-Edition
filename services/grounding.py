# -*- coding: utf-8 -*-
"""Grounding Resolver — the single front door for "does this answer need grounding in
real material, and where does that material come from?" (docs/PIPELINE_REFACTOR.md §2,
§0.5 #4).

LOMA Extended Edition is ONLINE-capable: grounded information comes from the user's
attached sources first, then — when the user has web grounding enabled and the query
needs current public-web facts — from a web search. So Extended's resolver answers:

  1. NEED — should the answer be grounded? YES if sources are attached, OR (no sources
     but web grounding is on and the query is a live-fact question). (needs_source_grounding)
  2. SELECT — attached sources are selected by the pipeline mechanism
     (pipeline.context_builder.build_context_bundle, retrieval trigger re-exported here);
     the web branch uses services/grounded_chat.py's real search machinery. The Context
     Governor (services.context_governor, at llm_bridge) then sizes/splits every call.

This is the Extended counterpart of Core's sources-only resolver: identical NEED entry
point, plus the web branch. A user-attached source always wins over a web search
(source_priority()); web only runs when nothing is attached and the user enabled it.
See SYNC_LEDGER.md §F2 — the web branch is the one real Core↔Extended difference here.
"""
from __future__ import annotations

# Re-export the retrieval trigger so callers have ONE grounding module to import.
from pipeline.context_builder import sources_need_retrieval  # noqa: F401


def needs_source_grounding(
    *, has_sources: bool, query: str = "", task_kind: str = "chat", settings: dict | None = None
) -> bool:
    """Extended (online) NEED gate. Attached sources always ground the answer. With no
    sources, fall through to the web branch: a live-fact query grounds via web search
    when the user has web grounding enabled (delegated to grounded_chat's existing gate,
    so the trigger policy stays in one place)."""
    if has_sources:
        return True
    try:
        from services.grounded_chat import should_use_grounded_chat

        return bool(should_use_grounded_chat(query, settings, has_attachments=False))
    except Exception:
        return False


def source_priority() -> tuple[str, ...]:
    """The order grounding material is drawn from. A user-attached source always wins;
    a web search only fills in when nothing is attached (and the user enabled it).
    Core stops at attached sources; Extended inserts 'web' before 'model_knowledge'."""
    return ("attached_sources", "web", "model_knowledge")
