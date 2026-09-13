# -*- coding: utf-8 -*-
"""Choose context strategy when sources exceed model budget."""
from __future__ import annotations

import re

from pipeline.context.types import ContextStrategy, SourceDigest

_TRANSLATE_FULL = re.compile(
    r"\b(translate|translation|localize|localise)\b", re.I
)
_SUMMARIZE_FULL = re.compile(
    r"\b(summarize|summarise|summary of the (?:whole|entire|full)|translate the (?:whole|entire|full))\b",
    re.I,
)
_CROSS_SOURCE_COMPARE = re.compile(
    r"\b(common|compare|comparison|contrast|across|all (?:five|four|three|sources|files|inputs)|"
    r"findings from|synthesize|synthesise|between these)\b",
    re.I,
)
_PER_SOURCE_EACH = re.compile(
    r"\b(?:each|every|per)\s+(?:source|document|file|upload)s?\b", re.I
)
_MUTATION_HINT = re.compile(
    r"\b(edit|update|change|modify|mutate|translate)\b.*\b(file|pptx|docx|xlsx|slide|document)\b",
    re.I,
)


_TRANSLATE_MIN_MAP_REDUCE = 2_500


def resolve_context_strategy(
    query: str,
    digests: list[SourceDigest],
    *,
    total_chars: int,
    char_budget: int,
    file_count: int = 0,
    link_count: int = 0,
) -> ContextStrategy:
    """Pick how to feed sources to planners and LLM steps."""
    q = (query or "").strip()
    n_sources = len(digests) or (file_count + link_count)
    usable = max(char_budget - 8_000, char_budget // 2)

    if total_chars <= int(usable * 0.9) and n_sources <= 1:
        return "fit"
    if total_chars <= usable and n_sources <= 2:
        return "fit"

    if n_sources >= 2 and _PER_SOURCE_EACH.search(q):
        return "per_source"

    # Comparison/synthesis words ("compare", "contrast", "between these", "synthesize", ...)
    # need every source's FULL text considered together, not per-file isolation — "per_source"
    # would instead hand each step only a preview of every file and process them one at a time,
    # which can neither compare anything nor guarantee every file gets covered.
    if n_sources >= 2 and _CROSS_SOURCE_COMPARE.search(q):
        return "map_reduce"

    if _TRANSLATE_FULL.search(q) or _SUMMARIZE_FULL.search(q):
        if total_chars > _TRANSLATE_MIN_MAP_REDUCE:
            return "map_reduce"
        if total_chars > usable // 2:
            return "map_reduce"

    if n_sources >= 2 and total_chars > usable:
        return "per_source"

    if total_chars > usable:
        return "retrieve"

    return "fit"
