# -*- coding: utf-8 -*-
"""Deterministic date-fact lookup for cover-page metadata.

Cover pages are excluded from normal retrieval/synthesis (ChunkRecord.is_low_content)
to avoid a short, dense page winning keyword search purely on match ratio over real
content, and to avoid the LLM padding/hallucinating an answer from a near-empty
passage. But a cover page often holds exactly one genuinely useful fact: the
document's date or date range. Rather than letting the LLM freely synthesize from that
thin content (reintroducing the hallucination risk this module exists to avoid), a
narrow query-intent check answers date questions by returning the matched text
verbatim — the model is never asked to interpret or expand on it.
"""
from __future__ import annotations

import re

from extensions.knowledge_vault.corpus.types import ChunkRecord
from extensions.knowledge_vault.index.lexical import branch_matches_chunk

def is_date_query(query: str) -> bool:
    from pipeline.query_intent_i18n import matches

    q = (query or "").lower()
    if not q.strip():
        return False
    return matches(q, "date_intent")


_CJK_NUM = "〇零○一二三四五六七八九十百千0-9"
# No \s* between components: PDF extraction can insert stray whitespace ANYWHERE,
# including inside a single numeral run (e.g. "二○ 一一" for what should read "二○一一"
# = 2011) — not just between semantic components like 年/月/日. Matching is done
# against a whitespace-stripped copy of the text instead (see extract_dates), which is
# robust regardless of where the extractor happened to insert spaces.
_GREGORIAN_CJK = (
    rf"公元[{_CJK_NUM}]{{2,4}}年[{_CJK_NUM}]{{1,3}}月[{_CJK_NUM}]{{1,3}}日"
    rf"(?:至[{_CJK_NUM}]{{1,3}}月[{_CJK_NUM}]{{1,3}}日)?"
)
_LUNAR_CJK = (
    r"歲次[甲乙丙丁戊己庚辛壬癸][子丑寅卯辰巳午未申酉戌亥]"
    rf"[{_CJK_NUM}]{{1,4}}月[{_CJK_NUM}]{{1,3}}日"
    rf"(?:至[{_CJK_NUM}]{{1,3}}月[{_CJK_NUM}]{{1,3}}日)?"
)
_ISO_RANGE = r"\d{4}-\d{2}-\d{2}(?:(?:to|~|-)\d{4}-\d{2}-\d{2})?"

_DATE_PATTERN = re.compile(f"(?:{_GREGORIAN_CJK})|(?:{_LUNAR_CJK})|(?:{_ISO_RANGE})")


def extract_dates(text: str) -> list[str]:
    """Every distinct date-like span found in `text`, in the order they appear."""
    if not text:
        return []
    stripped = re.sub(r"\s+", "", text)
    seen: list[str] = []
    for m in _DATE_PATTERN.finditer(stripped):
        span = m.group(0)
        if span and span not in seen:
            seen.append(span)
    return seen


def find_cover_page_dates(
    chunks: list[ChunkRecord], *, branch: str = ""
) -> list[tuple[ChunkRecord, list[str]]]:
    """(chunk, dates) for every low-content chunk in scope that contains a date."""
    out: list[tuple[ChunkRecord, list[str]]] = []
    for ch in chunks:
        if not ch.is_low_content:
            continue
        if not branch_matches_chunk(ch, branch):
            continue
        dates = extract_dates(ch.text)
        if dates:
            out.append((ch, dates))
    return out
