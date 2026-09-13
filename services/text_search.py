# -*- coding: utf-8 -*-
"""Deterministic keyword and phrase scoring over text chunks."""
from __future__ import annotations

import re
from typing import Iterable

from services.types import TextChunk

_WORD_RE = re.compile(r"[\w\u0080-\uffff]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "can",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "as",
        "from",
        "with",
        "by",
        "about",
        "into",
        "through",
        "during",
        "before",
        "after",
        "above",
        "below",
        "between",
        "under",
        "again",
        "further",
        "then",
        "once",
        "here",
        "there",
        "when",
        "where",
        "why",
        "how",
        "all",
        "each",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "no",
        "nor",
        "not",
        "only",
        "own",
        "same",
        "so",
        "than",
        "too",
        "very",
        "just",
        "also",
        "what",
        "which",
        "who",
        "whom",
        "me",
        "my",
        "we",
        "our",
        "you",
        "your",
        "they",
        "their",
        "he",
        "she",
        "him",
        "her",
        "i",
    }
)


def tokenize_query(query: str) -> list[str]:
    tokens = [t.lower() for t in _WORD_RE.findall(query or "") if len(t) > 1]
    return [t for t in tokens if t not in _STOPWORDS]


def score_chunk(chunk: TextChunk, query: str) -> float:
    """Higher score = more relevant. Uses term frequency and phrase overlap."""
    q = (query or "").strip()
    if not q:
        return 0.0

    text_lower = chunk.text.lower()
    tokens = tokenize_query(q)
    if not tokens:
        return 0.0

    score = 0.0
    for tok in tokens:
        count = text_lower.count(tok)
        if count:
            score += count * (1.0 + min(len(tok), 12) / 20.0)

    q_lower = q.lower()
    if len(q_lower) >= 4 and q_lower in text_lower:
        score += 8.0

    # quoted phrases in query
    for phrase in re.findall(r'"([^"]+)"', q):
        pl = phrase.lower().strip()
        if len(pl) >= 3 and pl in text_lower:
            score += 12.0

    if chunk.section and any(t in chunk.section.lower() for t in tokens[:6]):
        score += 1.5

    return score


def rank_chunks(
    chunks: Iterable[TextChunk],
    query: str,
    *,
    min_score: float = 0.0,
) -> list[tuple[TextChunk, float]]:
    ranked: list[tuple[TextChunk, float]] = []
    for chunk in chunks:
        s = score_chunk(chunk, query)
        if s >= min_score:
            ranked.append((chunk, s))
    ranked.sort(key=lambda pair: (-pair[1], pair[0].source, pair[0].index))
    return ranked


def grep_chunks(
    chunks: Iterable[TextChunk],
    pattern: str,
    *,
    ignore_case: bool = True,
) -> list[TextChunk]:
    """Return chunks whose text matches a regex pattern."""
    if not pattern:
        return []
    flags = re.IGNORECASE if ignore_case else 0
    try:
        rx = re.compile(pattern, flags)
    except re.error:
        return []
    return [c for c in chunks if rx.search(c.text)]
