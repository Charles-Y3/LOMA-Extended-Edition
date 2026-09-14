# -*- coding: utf-8 -*-
"""Ask task."""
from __future__ import annotations

from typing import Callable

from extensions.knowledge_vault.corpus.types import ReasoningMode, TaskKind
from extensions.knowledge_vault.retrieval.context_builder import (
    deep_select,
    expand_neighbor_chunks,
    fast_select,
)
from extensions.knowledge_vault.retrieval.date_lookup import (
    find_cover_page_dates,
    is_date_query,
)
from extensions.knowledge_vault.retrieval.engine import CorpusBackend, retrieve
from extensions.knowledge_vault.retrieval.framing import is_analysis_style_query
from extensions.knowledge_vault.retrieval.synthesize import synthesize
from extensions.knowledge_vault.settings import load_settings


def _date_lookup_answer(backend: CorpusBackend, query: str, *, branch: str) -> str | None:
    """Answer a date-shaped question straight from cover-page metadata, verbatim —
    never via the LLM. Cover pages stay fully excluded from normal retrieval/synthesis
    (a short, dense page can win keyword search on match ratio alone, and an LLM asked
    to write "a substantive answer in full sentences" from a near-empty passage tends
    to pad and hallucinate) — this is a separate, deterministic path for the one class
    of question that exclusion otherwise makes unanswerable. Falls through to normal
    retrieval (returns None) when nothing is found, so it can still correctly say
    "no relevant content" rather than fabricate."""
    if not is_date_query(query):
        return None
    matches = find_cover_page_dates(getattr(backend, "chunks", None) or [], branch=branch)
    if not matches:
        return None
    from extensions.knowledge_vault.ui.path_links import format_file_path_links

    lines = ["**Dates found on cover page(s):**", ""]
    for i, (chunk, dates) in enumerate(matches, start=1):
        lines.append(f"**[{i}] {chunk.source}** — {'; '.join(dates)}")
        path_block = format_file_path_links(chunk.file_path)
        if path_block:
            lines.append(path_block)
        lines.append("")
    return "\n".join(lines).strip()


def run_ask(
    backend: CorpusBackend,
    query: str,
    *,
    branch: str = "",
    mode: ReasoningMode = ReasoningMode.FAST,
    library_id: str | None = None,
    semantic_ready: bool = False,
    semantic_suggest: bool = False,
    on_chunk: Callable[[str], None] | None = None,
    locale: str = "",
    cutoff_override: float | None = None,
) -> str:
    date_answer = _date_lookup_answer(backend, query, branch=branch)
    if date_answer is not None:
        return date_answer
    hits = retrieve(
        backend,
        query,
        branch=branch,
        mode=mode,
        library_id=library_id,
        semantic_ready=semantic_ready,
        cutoff_override=cutoff_override,
    )
    if mode == ReasoningMode.FAST:
        selected = fast_select(hits)
    else:
        selected = deep_select(hits)
    selected = expand_neighbor_chunks(selected, backend.chunks, query)
    cfg = load_settings()
    task = TaskKind.ANALYZE if is_analysis_style_query(query) else TaskKind.ASK
    return synthesize(
        query,
        selected,
        task=task,
        citation_required=bool(cfg.get("citation_required")),
        mode=mode,
        semantic_suggest=semantic_suggest,
        model=str(cfg.get("answer_model") or ""),
        on_chunk=on_chunk,
        locale=locale,
    )
