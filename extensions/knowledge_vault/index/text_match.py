# -*- coding: utf-8 -*-
"""Shared query/text matching primitives — used by both the BM25 index
(index/lexical.py) and display-layer coverage scoring/highlighting
(retrieval/search_format.py), so there is exactly one definition of "what
counts as a meaningful matching unit" instead of two that can drift apart.

Language handling is deliberately general rather than a per-language list:
- CJK (Han script) has no whitespace word boundaries, so it's matched by
  character bigrams — the same granularity index/lexical.py's tokenizer
  already indexes CJK text at.
- Everything else (Latin, Cyrillic, Greek, Arabic, Hebrew, Devanagari,
  Hangul, and other whitespace/punctuation-separated scripts) is matched by
  \\w+ word tokens, which Python's `re` resolves against Unicode word
  categories, not just ASCII.
- Whether a single matched token is "significant" enough to score/highlight
  on its own (the "and"/"its" problem) is decided by the corpus's own BM25
  IDF via the `significant` callback, not a hardcoded stopword list — so it
  works the same way for any language the corpus actually contains.

Known limitation: genuinely space-free non-CJK scripts (Thai, Lao, Khmer,
Myanmar, Japanese kana) need a real segmenter to tokenize correctly; \\w+
alone will glom an unspaced sentence into one token. Not handled here.
"""
from __future__ import annotations

import re
from typing import Callable

_MIN_COVERAGE = 0.15

_CJK_RUN_RE = re.compile(r"[一-鿿]+")
_WORD_RE = re.compile(r"\w+", re.UNICODE)

Significant = Callable[[str], bool]


def _always_significant(_term: str) -> bool:
    return True


def is_cjk_query(q: str) -> bool:
    return bool(_CJK_RUN_RE.search(q))


def cjk_bigrams(text: str) -> list[str]:
    runs = _CJK_RUN_RE.findall(text)
    out: list[str] = []
    for run in runs:
        if len(run) == 1:
            out.append(run)
            continue
        out.append(run)
        for i in range(len(run) - 1):
            out.append(run[i : i + 2])
    return out


def _char_ngrams(q: str) -> list[str]:
    """Character substrings (>=2 chars), longest first — for CJK phrase
    matching/highlighting (distinct from cjk_bigrams, which is for indexing:
    this keeps every substring, not just single run + bigrams, so a longer
    verbatim query match is preferred over a bigram fragment of it)."""
    n = len(q)
    seen: set[str] = set()
    out: list[str] = []
    for length in range(n, 1, -1):
        for start in range(n - length + 1):
            sub = q[start : start + length]
            if sub in seen:
                continue
            seen.add(sub)
            out.append(sub)
    return out


def _word_ngrams(q: str) -> list[str]:
    """Whole-word and multi-word phrases, longest first, original casing
    preserved (callers lowercase for matching as needed) — never a bare
    character fragment: matching a 2-letter piece of the query string
    against ordinary prose lights up unrelated single letters everywhere."""
    words = _WORD_RE.findall(q)
    n = len(words)
    seen: set[str] = set()
    out: list[str] = []
    for length in range(n, 0, -1):
        for start in range(n - length + 1):
            phrase = " ".join(words[start : start + length])
            key = phrase.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(phrase)
    return out


def query_phrases(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return []
    return _char_ngrams(q) if is_cjk_query(q) else _word_ngrams(q)


def phrase_match_coverage(
    query: str, text: str, *, significant: Significant | None = None
) -> float:
    """0-1 score for how much of the query is found in text. `significant`
    decides whether a lone single-token match counts — pass a corpus's
    LexicalIndex.term_is_significant to suppress functional/common-word-only
    matches (e.g. a stray "and") while still allowing them inside a genuine
    multi-word phrase match."""
    q = (query or "").strip()
    if not q or not text:
        return 0.0
    sig = significant or _always_significant
    is_cjk = is_cjk_query(q)
    if is_cjk:
        if q in text:
            return 1.0
    elif re.search(rf"\b{re.escape(q.lower())}\b", text.lower()):
        return 1.0

    if is_cjk:
        qlen = len(q)
        for length in range(qlen, 1, -1):
            for start in range(qlen - length + 1):
                sub = q[start : start + length]
                if length == 1 and not sig(sub):
                    continue
                if sub in text:
                    return length / qlen
        return 0.0

    words = _WORD_RE.findall(q.lower())
    n = len(words)
    if n == 0:
        return 0.0
    text_lower = text.lower()
    for length in range(n, 0, -1):
        for start in range(n - length + 1):
            phrase = " ".join(words[start : start + length])
            if length == 1 and not sig(phrase):
                continue
            if re.search(rf"\b{re.escape(phrase)}\b", text_lower):
                return length / n
    sig_words = [w for w in words if sig(w)]
    if sig_words:
        hits = sum(1 for w in sig_words if re.search(rf"\b{re.escape(w)}\b", text_lower))
        if hits:
            return (hits / len(sig_words)) * 0.5
    return 0.0


def highlight_spans(
    text: str, query: str, *, significant: Significant | None = None
) -> list[tuple[int, int]]:
    if not text or not query:
        return []
    sig = significant or _always_significant
    text_lower = text.lower()
    is_cjk = is_cjk_query(query)
    masked = [False] * len(text)
    spans: list[tuple[int, int]] = []
    matched_phrases: list[str] = []
    for phrase in query_phrases(query):
        phrase_l = phrase.lower()
        is_single = " " not in phrase and not is_cjk
        if is_single and not sig(phrase_l):
            continue
        if is_cjk and len(phrase) < 2:
            continue
        if is_cjk and len(phrase) == 2 and not sig(phrase_l):
            # 2-char CJK "words" are the finest granularity the index itself
            # tokenizes at (see cjk_bigrams) — treat them like single tokens
            # for significance purposes.
            continue
        if any(phrase_l in longer for longer in matched_phrases if phrase_l != longer):
            continue
        found_any = False
        if is_cjk:
            pos = 0
            while pos < len(text):
                idx = text_lower.find(phrase_l, pos)
                if idx < 0:
                    break
                end = idx + len(phrase)
                if not any(masked[idx:end]):
                    spans.append((idx, end))
                    for i in range(idx, end):
                        masked[i] = True
                    found_any = True
                pos = idx + 1
        else:
            for m in re.finditer(rf"\b{re.escape(phrase_l)}\b", text_lower):
                idx, end = m.start(), m.end()
                if not any(masked[idx:end]):
                    spans.append((idx, end))
                    for i in range(idx, end):
                        masked[i] = True
                    found_any = True
        if found_any:
            matched_phrases.append(phrase_l)
    spans.sort()
    return spans
