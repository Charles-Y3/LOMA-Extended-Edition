# -*- coding: utf-8 -*-
"""Score normalization and reranking."""
from __future__ import annotations

from extensions.knowledge_vault.corpus.types import HitRecord


def clamp_scores(hits: list[HitRecord]) -> list[HitRecord]:
    """Clamp to [0, 1] and sort — does NOT stretch the best hit up to 1.0. merge_hits'
    weighted blend and the exact-phrase boost below already produce a real, comparable
    0-1 score (calibrated cosine similarity for semantic, phrase coverage for lexical);
    min-max rescaling the batch used to force the single best-of-these-N-results hit to
    show exactly 1.0 regardless of how weak the true match was, which made the score
    meaningless as a confidence signal. A 1.0 now means "true near-perfect match", not
    "best we found this time."."""
    out: list[HitRecord] = []
    for h in hits:
        clamped = max(0.0, min(1.0, h.score))
        out.append(HitRecord(chunk=h.chunk, score=clamped, snippet=h.snippet))
    out.sort(key=lambda x: x.score, reverse=True)
    return out


def rerank_hits(hits: list[HitRecord], query: str) -> list[HitRecord]:
    q = (query or "").lower()
    boosted: list[HitRecord] = []
    for h in hits:
        boost = 0.0
        text_l = h.chunk.text.lower()
        if q and q in text_l:
            boost += 0.15
        boosted.append(HitRecord(chunk=h.chunk, score=h.score + boost, snippet=h.snippet))
    boosted.sort(key=lambda x: x.score, reverse=True)
    return clamp_scores(boosted)
