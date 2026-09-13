# -*- coding: utf-8 -*-
"""Merge lexical and semantic retrieval results."""
from __future__ import annotations

from extensions.document_intelligence.corpus.types import HitRecord


def merge_hits(
    *hit_lists: list[HitRecord],
    limit: int = 50,
    lexical_weight: float = 0.45,
    semantic_weight: float = 0.55,
) -> list[HitRecord]:
    """Combine any number of hit lists into one, keyed by chunk id.

    A chunk seen more than once is a real hybrid signal (lexical AND semantic both found
    it) and should score higher than either alone — not just take whichever single score
    was bigger. Each hit list contributes lexical_weight or semantic_weight (matched by
    call position: engine.py always passes lexical results first, semantic second) to a
    running weighted sum; a chunk's final score divides by the weight actually
    accumulated for it, so a chunk found by only one signal keeps that signal's own score
    scale instead of being penalized for the other signal's absent weight."""
    weights = [lexical_weight, semantic_weight]
    by_id: dict[str, tuple[float, float, HitRecord]] = {}  # id -> (weighted_sum, weight_sum, hit)
    for i, hits in enumerate(hit_lists):
        w = weights[i] if i < len(weights) else 1.0
        for h in hits:
            cid = h.chunk.chunk_id or f"{h.chunk.source}:{h.chunk.segment_index}"
            weighted_sum, weight_sum, existing = by_id.get(cid, (0.0, 0.0, None))
            weighted_sum += w * h.score
            weight_sum += w
            snippet = existing.snippet if existing else h.snippet
            by_id[cid] = (weighted_sum, weight_sum, HitRecord(chunk=h.chunk, score=0.0, snippet=snippet))

    merged: list[HitRecord] = []
    for weighted_sum, weight_sum, hit in by_id.values():
        hit.score = weighted_sum / weight_sum if weight_sum > 0 else 0.0
        merged.append(hit)
    merged.sort(key=lambda x: x.score, reverse=True)
    return merged[:limit]


def apply_cutoff(hits: list[HitRecord], cutoff: float) -> list[HitRecord]:
    return [h for h in hits if h.score >= cutoff]
