# -*- coding: utf-8 -*-
"""Compose atomic retrieval services into workspace context (pipeline layer)."""
from __future__ import annotations

from services.context_selector import (
    ContextSelectionResult,
    SelectionMode,
    char_budget_from_profile,
    infer_selection_mode,
)
from services.document_chunker import chunk_sources
from services.text_search import rank_chunks
from services.types import SourceDocument, TextChunk


def select_workspace_context(
    sources: list[SourceDocument],
    query: str,
    *,
    profile: dict | None = None,
    char_budget: int | None = None,
    mode: SelectionMode | None = None,
) -> ContextSelectionResult:
    """
    Build the Workspace Context block for the LLM.

    Orchestrates document_chunker and text_search, then applies selection strategy.
    """
    budget = char_budget if char_budget is not None else char_budget_from_profile(profile)
    mode = mode or infer_selection_mode(query)

    full_parts: list[str] = []
    total_chars = 0
    for doc in sources:
        body = (doc.text or "").strip()
        if not body:
            continue
        total_chars += len(body)
        header = _source_header(doc)
        full_parts.append(f"{header}\n{body}")

    if not full_parts:
        return ContextSelectionResult(
            text="",
            was_truncated=False,
            total_source_chars=0,
            selected_chunk_count=0,
            total_chunk_count=0,
            mode=mode,
        )

    full_text = "\n\n".join(full_parts)
    if total_chars <= budget:
        return ContextSelectionResult(
            text=full_text,
            was_truncated=False,
            total_source_chars=total_chars,
            selected_chunk_count=0,
            total_chunk_count=0,
            mode=mode,
        )

    chunks = chunk_sources(sources)
    if not chunks:
        return ContextSelectionResult(
            text=full_text[:budget],
            was_truncated=True,
            total_source_chars=total_chars,
            selected_chunk_count=0,
            total_chunk_count=0,
            mode=mode,
        )

    selected = _pick_chunks(chunks, query, budget, mode)
    note = (
        f"[LOMA retrieved {len(selected)} of {len(chunks)} passages from "
        f"{total_chars:,} characters of sources — mode: {mode}]\n\n"
    )
    body = _format_chunks(selected)
    return ContextSelectionResult(
        text=note + body,
        was_truncated=True,
        total_source_chars=total_chars,
        selected_chunk_count=len(selected),
        total_chunk_count=len(chunks),
        mode=mode,
    )


def _source_header(doc: SourceDocument) -> str:
    if doc.source_kind == "web":
        return f"--- WEB SOURCE: {doc.name} ---"
    if doc.source_kind == "excerpt":
        return f"--- EXCERPT: {doc.name} ---"
    return f"--- ATTACHED DOCUMENT: {doc.name} ---"


def _format_chunks(chunks: list[TextChunk]) -> str:
    blocks: list[str] = []
    for c in chunks:
        loc = f" ({c.section})" if c.section else ""
        from services.security.untrusted import wrap

        blocks.append(f"--- PASSAGE from {c.source}{loc} [{c.chunk_id}] ---\n{wrap(c.text, c.source)}")
    return "\n\n".join(blocks)


def _pick_chunks(
    chunks: list[TextChunk],
    query: str,
    budget: int,
    mode: SelectionMode,
) -> list[TextChunk]:
    if mode == "summarize":
        return _pick_for_summarize(chunks, query, budget)
    if mode == "translate":
        return _pick_for_translate(chunks, budget)
    return _pick_for_qa(chunks, query, budget)


def _pick_for_qa(chunks: list[TextChunk], query: str, budget: int) -> list[TextChunk]:
    ranked = rank_chunks(chunks, query)
    if not ranked and query.strip():
        ranked = [(c, float(-c.index)) for c in _first_chunk_per_source(chunks)]

    selected: list[TextChunk] = []
    used = 0
    seen_ids: set[str] = set()

    for chunk, _score in ranked:
        if chunk.chunk_id in seen_ids:
            continue
        block_len = len(_format_chunks([chunk])) + 2
        if used + block_len > budget and selected:
            break
        if block_len > budget and not selected:
            trimmed = TextChunk(
                chunk_id=chunk.chunk_id,
                source=chunk.source,
                source_kind=chunk.source_kind,
                text=chunk.text[: max(budget - 200, 500)],
                index=chunk.index,
                section=chunk.section,
            )
            selected.append(trimmed)
            break
        selected.append(chunk)
        seen_ids.add(chunk.chunk_id)
        used += block_len

    if not selected:
        selected = chunks[:1]
    return selected


def _pick_for_summarize(chunks: list[TextChunk], query: str, budget: int) -> list[TextChunk]:
    by_source: dict[str, list[TextChunk]] = {}
    for c in chunks:
        by_source.setdefault(c.source, []).append(c)
    for src in by_source:
        by_source[src].sort(key=lambda x: x.index)

    candidates: list[TextChunk] = []
    for src_chunks in by_source.values():
        candidates.extend(src_chunks[:2])
        if len(src_chunks) > 4:
            mid = len(src_chunks) // 2
            candidates.append(src_chunks[mid])
        if len(src_chunks) > 1:
            candidates.append(src_chunks[-1])

    ranked = rank_chunks(candidates, query)
    order = [c for c, _ in ranked] if ranked else candidates
    seen: set[str] = set()
    unique: list[TextChunk] = []
    for c in order:
        if c.chunk_id not in seen:
            seen.add(c.chunk_id)
            unique.append(c)

    return _fill_to_budget(unique, budget)


def _pick_for_translate(chunks: list[TextChunk], budget: int) -> list[TextChunk]:
    by_source: dict[str, list[TextChunk]] = {}
    for c in chunks:
        by_source.setdefault(c.source, []).append(c)
    ordered: list[TextChunk] = []
    for src in sorted(by_source.keys()):
        ordered.extend(sorted(by_source[src], key=lambda x: x.index))
    return _fill_to_budget(ordered, budget)


def _first_chunk_per_source(chunks: list[TextChunk]) -> list[TextChunk]:
    seen: set[str] = set()
    out: list[TextChunk] = []
    for c in sorted(chunks, key=lambda x: (x.source, x.index)):
        if c.source not in seen:
            seen.add(c.source)
            out.append(c)
    return out


def _fill_to_budget(ordered: list[TextChunk], budget: int) -> list[TextChunk]:
    selected: list[TextChunk] = []
    used = 0
    for chunk in ordered:
        block_len = len(_format_chunks([chunk])) + 2
        if used + block_len > budget and selected:
            break
        if block_len > budget:
            continue
        selected.append(chunk)
        used += block_len
    return selected or (ordered[:1] if ordered else [])
