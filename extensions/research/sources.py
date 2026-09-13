# -*- coding: utf-8 -*-
"""Source records, credibility heuristics, and reference formatting."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
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


@dataclass
class ResearchSource:
    source_id: str = ""
    title: str = ""
    url: str = ""
    text: str = ""
    domain: str = ""
    source_type: str = "web"
    credibility_score: float = 0.5
    credibility_note: str = ""
    relevance_score: float = 0.5
    key_points: list[str] = field(default_factory=list)
    selected: bool = False

    def snippet(self, limit: int = 400) -> str:
        t = (self.text or "").replace("\n", " ").strip()
        return t[:limit] + ("…" if len(t) > limit else "")

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "source_type": self.source_type,
            "credibility_score": self.credibility_score,
            "credibility_note": self.credibility_note,
            "relevance_score": self.relevance_score,
            "key_points": list(self.key_points),
            "selected": self.selected,
        }


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


def hit_to_source(hit: dict[str, str], text: str) -> ResearchSource:
    url = (hit.get("url") or "").strip()
    title = (hit.get("title") or url).strip()
    stype = (hit.get("source") or "web").strip().lower()
    score, note = heuristic_credibility(url, title, stype)
    return ResearchSource(
        title=title,
        url=url,
        text=(text or "")[:18000],
        domain=domain_from_url(url),
        source_type=stype,
        credibility_score=score,
        credibility_note=note,
    )


def upload_to_source(name: str, text: str) -> ResearchSource:
    score, note = heuristic_credibility("", name, "upload")
    return ResearchSource(
        title=name,
        url="",
        text=(text or "")[:18000],
        domain="upload",
        source_type="upload",
        credibility_score=score,
        credibility_note=note,
    )


def assign_source_ids(sources: list[ResearchSource]) -> None:
    for i, src in enumerate(sources, start=1):
        src.source_id = f"S{i}"


def format_references_section(sources: list[ResearchSource]) -> str:
    lines = ["## References", ""]
    for src in sources:
        if not src.selected and src.source_id:
            continue
        sid = src.source_id or "?"
        title = src.title or "Untitled"
        # Trailing "  " forces a markdown hard line break so each reference renders on
        # its own line even when the renderer collapses a lone blank-line paragraph gap.
        if src.url:
            lines.append(f"[{sid}] [{title}]({src.url})  ")
        else:
            lines.append(f"[{sid}] {title} *(uploaded file)*  ")
    return "\n".join(lines).strip()


# Whole-line match only (not a prefix) — a real section titled e.g. "## Sources of Bias
# in AI Training" must NOT be treated as the citation dump and truncated.
_TRAILING_SOURCE_HEADING_RE = re.compile(
    r"^(#{1,3}\s*(references|sources)\s*:?|\*\*sources:?\*\*)$", re.IGNORECASE
)

# A self-added citation dump doesn't always use a heading — some models instead write a
# plain lead-in sentence ("The following sources are cited throughout this report:")
# followed by one "[S#] Title (url)" line per source. Each such line starts a fresh line
# with the bracketed id, which body prose never does (inline citations like "[S1]" always
# sit mid-sentence), so it's a safe signature to key off regardless of the intro wording.
_CITATION_DUMP_LINE_RE = re.compile(r"^\[S\d+\]\s")


def _strip_trailing_source_sections(body: str) -> str:
    """Drop any references/sources section the model added on its own, despite the
    system prompt's "Do NOT add a References section — it is appended automatically"
    rule — some models add a "**Sources:**" list, or a narrative lead-in sentence plus a
    "[S1] ... [S6]" dump, anyway. Left in place, append_references() only recognized an
    exact "## References" heading, so anything else slipped through and both ended up in
    the final deliverable, duplicating the citations."""
    lines = body.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if _TRAILING_SOURCE_HEADING_RE.match(stripped):
            cut = i
            break
        if _CITATION_DUMP_LINE_RE.match(stripped):
            cut = i
            # Absorb one immediately preceding lead-in line (short, not itself a citation
            # line) so its intro sentence doesn't linger with nothing after it.
            if cut > 0 and lines[cut - 1].strip() and len(lines[cut - 1].strip()) < 160:
                cut -= 1
            break
    trimmed = "\n".join(lines[:cut]).rstrip()
    # A lone "---" divider left dangling right before the section we just cut.
    trimmed = re.sub(r"\n?-{3,}\s*$", "", trimmed).rstrip()
    return trimmed


def append_references(deliverable: str, sources: list[ResearchSource]) -> str:
    body = _strip_trailing_source_sections((deliverable or "").rstrip())
    refs = format_references_section(sources)
    if not refs or refs in body:
        return body
    return f"{body}\n\n---\n\n{refs}"


def parse_source_count(answer: str, default: int = 6) -> int:
    m = re.search(r"\d+", answer or "")
    if m:
        return max(3, min(15, int(m.group(0))))
    return default
