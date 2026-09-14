# -*- coding: utf-8 -*-
"""Analyze task."""
from __future__ import annotations

from typing import Callable

from extensions.knowledge_vault.corpus.types import ReasoningMode, TaskKind
from extensions.knowledge_vault.retrieval.ask_action import run_ask
from extensions.knowledge_vault.retrieval.context_builder import (
    deep_select,
    expand_neighbor_chunks,
    expand_neighbor_chunks_recursive,
    merge_same_section,
    whole_file_fallback,
)
from extensions.knowledge_vault.retrieval.cpu_budget import (
    effective_ctx_ceiling_tokens,
    tighten_for_cpu,
)
from extensions.knowledge_vault.retrieval.engine import CorpusBackend, retrieve
from extensions.knowledge_vault.retrieval.framing import is_analysis_style_query
from extensions.knowledge_vault.retrieval.synthesize import synthesize
from extensions.knowledge_vault.settings import load_settings


def run_analyze(
    backend: CorpusBackend,
    query: str,
    *,
    branch: str = "",
    mode: ReasoningMode = ReasoningMode.DEEP,
    library_id: str | None = None,
    semantic_ready: bool = False,
    semantic_suggest: bool = False,
    deep_extras: bool = False,
    on_chunk: Callable[[str], None] | None = None,
    locale: str = "",
    cutoff_override: float | None = None,
) -> str:
    if mode == ReasoningMode.FAST:
        return run_ask(
            backend,
            query,
            branch=branch,
            mode=mode,
            library_id=library_id,
            semantic_ready=semantic_ready,
            semantic_suggest=semantic_suggest,
            on_chunk=on_chunk,
            locale=locale,
            cutoff_override=cutoff_override,
        )
    cfg = load_settings()
    if deep_extras:
        cfg = tighten_for_cpu(cfg)
    hits = retrieve(
        backend,
        query,
        branch=branch,
        mode=mode,
        library_id=library_id,
        semantic_ready=semantic_ready,
        cutoff_override=cutoff_override,
    )
    selected = deep_select(hits, depth=int(cfg.get("retrieval_depth") or 40))
    selected = expand_neighbor_chunks(selected, backend.chunks, query)
    if deep_extras:
        model = str(cfg.get("answer_model") or "")
        ctx_budget = effective_ctx_ceiling_tokens(model)
        selected, whole_filed = whole_file_fallback(
            selected, backend.chunks, ctx_budget_tokens=ctx_budget
        )
        selected = merge_same_section(selected, backend.chunks, skip_keys=whole_filed)
        selected = expand_neighbor_chunks_recursive(
            selected,
            backend.chunks,
            query,
            skip_keys=whole_filed,
            ctx_budget_tokens=ctx_budget,
        )
    if len(selected) > 30:
        selected = _compress_hits(query, selected)
    task = TaskKind.ANALYZE if is_analysis_style_query(query, default_analysis=deep_extras) else TaskKind.ASK
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


def _compress_hits(query: str, hits: list) -> list:
    from extensions.knowledge_vault.corpus.types import HitRecord
    from extensions.knowledge_vault.retrieval.context_builder import group_hits_by_file
    from extensions.ludicity_shared.llm import ludicity_chat

    compressed: list[HitRecord] = []
    # Batch per file (not a flat slice across all hits) and pick each batch's
    # representative chunk from THAT batch — previously batches were flat-sliced
    # across every file's hits together, and each compressed summary's metadata came
    # from hits[min(batch_number, len(hits)-1)], i.e. the chunk at that numeric
    # position in the ORIGINAL unbatched list, unrelated to what was actually in that
    # batch. Past the first batch this could (and for any multi-file selection,
    # typically did) attribute a summary to the wrong page, or even the wrong file
    # entirely — exactly the citation/page-range info this is meant to preserve.
    for _key, group in group_hits_by_file(hits):
        for i in range(0, len(group), 15):
            batch = group[i : i + 15]
            text = "\n".join(h.chunk.text[:400] for h in batch)
            s = ludicity_chat(
                [
                    {
                        "role": "system",
                        "content": "Compress these excerpts into key bullet points relevant to the query.",
                    },
                    {"role": "user", "content": f"Query: {query}\n\n{text}"},
                ]
            )
            compressed.append(HitRecord(chunk=batch[0].chunk, score=1.0, snippet=s[:500]))
    return compressed or hits[:20]
