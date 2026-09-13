# -*- coding: utf-8 -*-
"""Search task — no LLM."""
from __future__ import annotations

from extensions.document_intelligence.corpus.types import HitRecord, ReasoningMode
from extensions.document_intelligence.retrieval.engine import CorpusBackend, retrieve
from extensions.document_intelligence.retrieval.search_format import (
    excerpt_snippet,
    format_search_chat,
    highlight_terms,
)
from extensions.document_intelligence.settings import load_settings

__all__ = ["run_search", "format_search_chat"]


def run_search(
    backend: CorpusBackend,
    query: str,
    *,
    branch: str = "",
    library_id: str | None = None,
    semantic_ready: bool = False,
) -> list[dict]:
    hits = retrieve(
        backend,
        query,
        branch=branch,
        mode=ReasoningMode.FAST,
        library_id=library_id,
        semantic_ready=semantic_ready,
    )
    cfg = load_settings()
    display_n = int(cfg.get("results_display_count") or 20)
    significant = getattr(getattr(backend, "lexical", None), "term_is_significant", None)
    return [_format_search_row(h, query, significant=significant) for h in hits[:display_n]]


def _format_search_row(hit: HitRecord, query: str, *, significant=None) -> dict:
    ch = hit.chunk
    page = ch.page_number if ch.page_number is not None else "—"
    raw = excerpt_snippet(hit.snippet or ch.text, query)
    return {
        "file_name": ch.source,
        "path": ch.file_path,
        "page": page,
        "snippet": raw,
        "snippet_highlighted": highlight_terms(raw, query, significant=significant),
        "score": round(hit.score, 4),
        "vault_path": ch.vault_path,
    }
