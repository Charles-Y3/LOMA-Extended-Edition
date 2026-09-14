# -*- coding: utf-8 -*-
"""Unified retrieval across workspace and library corpora."""
from __future__ import annotations

from typing import Protocol

from extensions.knowledge_vault.corpus.library import semantic_dir
from extensions.knowledge_vault.corpus.types import HitRecord, ReasoningMode
from extensions.knowledge_vault.index.hybrid import apply_cutoff, merge_hits
from extensions.knowledge_vault.index.lexical import LexicalIndex
from extensions.knowledge_vault.index.semantic import semantic_search
from extensions.knowledge_vault.retrieval.query_expansion import llm_expand, rule_expand
from extensions.knowledge_vault.retrieval.rerank import rerank_hits
from extensions.knowledge_vault.settings import load_settings


class CorpusBackend(Protocol):
    lexical: LexicalIndex
    chunks: list

    def indexed_tree(self) -> dict: ...


def retrieve(
    backend: CorpusBackend,
    query: str,
    *,
    branch: str = "",
    mode: ReasoningMode = ReasoningMode.FAST,
    library_id: str | None = None,
    semantic_ready: bool = False,
    cutoff_override: float | None = None,
) -> list[HitRecord]:
    cfg = load_settings()
    limit = int(cfg.get("max_chunks_returned") or 50)
    # The configured score_cutoff is tuned for corpus-wide search, where it protects
    # against noise from documents that have no real business matching the query.
    # That protection is unneeded — and can actively hurt — once `branch` already
    # confines the search to one specific, excerpt-verified document: a chunk from
    # THAT file scoring just under the corpus-wide cutoff is still very likely
    # relevant (e.g. an intro paragraph listing "1) ... 2) ... 3) ..." that a
    # "what's the third one" question needs for the model to count from, but which
    # doesn't itself contain the literal question text so scores lower on phrase
    # coverage) — callers that already trust the branch pass cutoff_override=0.0.
    cutoff = float(cfg.get("score_cutoff") or 0.0) if cutoff_override is None else cutoff_override
    depth = int(cfg.get("retrieval_depth") or 40)

    if mode == ReasoningMode.FAST:
        queries = [query]
        lim = min(limit, 30)
    else:
        queries = llm_expand(query) if mode == ReasoningMode.AGENT else rule_expand(query)
        lim = depth if mode == ReasoningMode.DEEP else depth

    all_lex: list[HitRecord] = []
    for q in queries:
        # score_cutoff is NOT passed here: LexicalIndex.search()'s cutoff filters
        # on raw phrase coverage (correct for translation.py's direct calls,
        # which pass their own appropriately-scaled threshold), but the user's
        # setting here is meant for the final *normalized* score below — passing
        # it at this raw, pre-rerank stage compared the same 0-1 setting against
        # un-normalized coverage values that rarely reach it, silently dropping
        # every result before normalization ever ran.
        all_lex.extend(backend.lexical.search(q, branch=branch, limit=lim))

    hits = merge_hits(all_lex, limit=lim)

    if semantic_ready and library_id:
        sem_hits: list[HitRecord] = []
        for q in queries[:3]:
            sem_hits.extend(
                semantic_search(
                    library_id,
                    q,
                    persist_dir=semantic_dir(library_id),
                    branch=branch,
                    limit=lim,
                )
            )
        hits = merge_hits(hits, sem_hits, limit=lim)

    hits = rerank_hits(hits, query)
    # getattr, not h.chunk.is_low_content: chunks from a lexical index pickled before
    # this field existed unpickle without it in __dict__ (dataclass defaults only
    # apply via __init__, which unpickling bypasses) — direct attribute access would
    # raise AttributeError on any pre-existing index until it's rebuilt.
    hits = [h for h in hits if not getattr(h.chunk, "is_low_content", False)]
    return apply_cutoff(hits, cutoff)
