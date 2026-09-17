# -*- coding: utf-8 -*-
"""Cheap, no-LLM-call filtering for web-search grounding used by report-shaped
generation (charts/diagrams/infographics/presentations — see grounding.py). Two
independent filters, both mechanical:

1. `heuristic_credibility()` — domain-based trust score, lenient by design (drop
   only the clearly untrustworthy: personal blogs, forums, template
   marketplaces — not "must be .gov/.edu"). Shared with extensions/research,
   which imports it from here rather than keeping its own copy.
2. `extract_relevant_sentences()` — a fetched page is usually mostly irrelevant
   to the one fact/number a chart needs; this pulls out just the handful of
   sentences that actually mention the topic (plus a number/stat bonus),
   instead of handing the authoring LLM 3500 characters of mostly-noise page
   text and hoping the right part is in there."""
from __future__ import annotations

import re
from urllib.parse import urlparse

_TRUSTED_TLDS = (".gov", ".edu", ".ac.uk", ".gov.uk")
_KNOWN_PUBLISHERS = (
    "wikipedia.org",
    "nih.gov",
    "who.int",
    "nature.com",
    "sciencedirect.com",
    "springer.com",
    "jstor.org",
    "pubmed",
    "ncbi.nlm.nih.gov",
    "psychologytoday.com",
    "verywellmind.com",
    "harvard.edu",
    "stanford.edu",
    "ox.ac.uk",
    "cambridge.org",
    "reuters.com",
    "bbc.com",
    "bbc.co.uk",
)
_LOW_TRUST = ("blogspot.", "wordpress.com", "medium.com", "reddit.com", "quora.com", "pinterest.")

# Lenient credibility bar (see module docstring) — drops the _LOW_TRUST bucket
# (0.4) and anything with no resolvable domain (0.35), keeps everything else
# ("general web source" 0.5+ and up).
CREDIBILITY_MIN = 0.45


def domain_from_url(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def heuristic_credibility(url: str, title: str = "", source_type: str = "web") -> tuple[float, str]:
    host = domain_from_url(url)
    if not host:
        return 0.35, "No URL — treat as unverified"
    if source_type == "upload":
        return 0.75, "User-provided document"
    if "wikipedia.org" in host:
        return 0.72, "Wikipedia — good overview; verify primary claims"
    for tld in _TRUSTED_TLDS:
        if host.endswith(tld) or tld.strip(".") in host:
            return 0.9, f"Institutional domain ({host})"
    for pub in _KNOWN_PUBLISHERS:
        if pub in host:
            return 0.82, f"Recognized publisher ({host})"
    for low in _LOW_TRUST:
        if low in host:
            return 0.4, f"Informal / user-generated ({host})"
    if host.count(".") >= 2:
        return 0.58, f"General web source ({host})"
    return 0.5, f"Web source ({host})"


# Not a credibility signal — a legitimate-looking domain can still be a
# presentation-template marketplace or a "how to write your presentation"
# blog post, neither of which is a data source for the fact a chart needs,
# regardless of how trustworthy the domain otherwise looks (confirmed: a real
# run cited "Free Natural Disasters PowerPoint And Google Slides" as a chart's
# "source"). Checked on title AND url since a marketplace's own domain name
# often carries the same words.
_NON_DATA_SOURCE_RE = re.compile(
    r"\b(powerpoint|google\s*slides|slide\s*template|ppt\s*template|slidesgo|"
    r"clipart|canva\.com|template\s*market|how\s+to\s+(?:write|make|create)\s+"
    r"(?:a\s+)?presentation)\b",
    re.IGNORECASE,
)


def is_non_data_source(title: str, url: str) -> bool:
    return bool(_NON_DATA_SOURCE_RE.search(f"{title} {url}"))


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])")
_WORD_RE = re.compile(r"[a-zA-Z]{3,}")
_NUMBER_RE = re.compile(r"\d")
_STOPWORDS = frozenset(
    """
    the a an and or but of to in on for with at by from as is are was were be
    been being this that these those it its into over under between about
    than then so such not no nor which who whom whose what when where why how
    can could will would shall should may might must have has had do does did
    their they them his her our your my we you he she i us also more most
    each other some any all both each few many much each per
    """.split()
)


_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")


def keywords(text: str) -> set[str]:
    """Cheap overlap signal, Latin words + CJK — `_WORD_RE` alone (ASCII-letter
    words) returns nothing for Chinese/Japanese/Korean text, which would make
    any overlap check silently no-op (always "no overlap") for non-Latin
    content instead of actually verifying it. CJK has no whitespace word
    boundaries, so character bigrams stand in for words — a much better
    overlap signal for those scripts than single characters, which are too
    common individually to mean anything."""
    text = text or ""
    latin = {w.lower() for w in _WORD_RE.findall(text) if w.lower() not in _STOPWORDS}
    cjk_chars = _CJK_RE.findall(text)
    if len(cjk_chars) >= 2:
        cjk = {"".join(cjk_chars[i : i + 2]) for i in range(len(cjk_chars) - 1)}
    else:
        cjk = set(cjk_chars)
    return latin | cjk


def _keywords(text: str) -> set[str]:
    return keywords(text)


def extract_relevant_sentences(
    text: str, topic: str, *, max_sentences: int = 6, min_keyword_overlap: int = 2,
) -> list[str]:
    """Pulls out just the sentences of `text` that actually mention `topic`'s
    own keywords, scored by keyword overlap (a number/stat in the sentence
    breaks ties in its favor — that's the part a chart actually needs). Empty
    result means "nothing in this page is actually about what was asked for",
    which is the correct signal to refuse rather than fabricate.

    min_keyword_overlap defaults to 2, not 1 — a single shared word is too
    easy to hit by coincidence on a long page (confirmed via a real run: a
    disaster-frequency chart got "grounded" in a page about daily world
    temperature records, because one incidental word overlapped; the model
    then filled in a suspiciously perfect 1-11 linear series instead of real
    numbers, with a real-but-irrelevant citation attached). Requiring two
    independent keyword matches is a much weaker coincidence."""
    topic_kw = _keywords(topic)
    if not topic_kw or not (text or "").strip():
        return []

    scored: list[tuple[int, int, str]] = []
    for raw in _SENTENCE_SPLIT_RE.split(text):
        sentence = raw.strip()
        if len(sentence) < 20 or len(sentence) > 400:
            continue
        overlap = len(_keywords(sentence) & topic_kw)
        if overlap < min_keyword_overlap:
            continue
        has_number = 1 if _NUMBER_RE.search(sentence) else 0
        scored.append((overlap, has_number, sentence))

    scored.sort(key=lambda row: (row[0], row[1]), reverse=True)
    # Dedup near-identical sentences (scrapers sometimes repeat a caption/lede).
    seen: set[str] = set()
    picked: list[str] = []
    for _overlap, _has_number, sentence in scored:
        key = sentence.lower()[:80]
        if key in seen:
            continue
        seen.add(key)
        picked.append(sentence)
        if len(picked) >= max_sentences:
            break
    return picked
