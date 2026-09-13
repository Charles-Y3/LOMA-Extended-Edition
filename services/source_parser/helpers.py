# -*- coding: utf-8 -*-
"""Helpers for consuming ParsedSource across pipeline and capabilities."""
from __future__ import annotations

from services.source_parser.models import ParsedSource

_RETRIEVAL_THRESHOLD_CHARS = 12_000


def primary_office_source(sources: list[ParsedSource] | None) -> ParsedSource | None:
    """First uploaded Office file suitable for template mutation."""
    for ps in sources or []:
        if not ps.ok or not ps.is_office():
            continue
        if ps.path or ps.name:
            return ps
    return None


def primary_mutation_source(sources: list[ParsedSource] | None) -> ParsedSource | None:
    """Office or PDF upload for in-place / export mutation."""
    ps = primary_office_source(sources)
    if ps:
        return ps
    for ps in sources or []:
        if not ps.ok:
            continue
        name = (ps.name or "").lower()
        if name.endswith(".pdf") and (ps.path or ps.name):
            return ps
    return None


def primary_media_source(sources: list[ParsedSource] | None) -> ParsedSource | None:
    """First uploaded audio/video file."""
    for ps in sources or []:
        if not ps.ok or ps.kind not in ("audio", "video"):
            continue
        if ps.media_path or ps.path or ps.name:
            return ps
    return None


def primary_image_source(sources: list[ParsedSource] | None) -> ParsedSource | None:
    """First uploaded image file."""
    for ps in sources or []:
        if not ps.ok or ps.kind != "image":
            continue
        if ps.media_path or ps.path or ps.name:
            return ps
    return None


def resolve_office_filename(sources: list[ParsedSource] | None, fallback: str = "output") -> str:
    ps = primary_office_source(sources)
    if ps and ps.name:
        return ps.name
    return fallback or "output"


def template_office_filename(sources: list[ParsedSource] | None) -> str | None:
    """Prefer pptx then docx template name for generation hints."""
    pptx_name = None
    docx_name = None
    for ps in sources or []:
        if not ps.ok or not ps.is_office():
            continue
        lower = (ps.name or "").lower()
        if lower.endswith(".pptx") and not pptx_name:
            pptx_name = ps.name
        if lower.endswith(".docx") and not docx_name:
            docx_name = ps.name
    return pptx_name or docx_name


def estimate_chars(sources: list[ParsedSource] | None) -> int:
    total = 0
    for ps in sources or []:
        if ps.ok:
            total += len(ps.context_text())
    return total


def sources_need_retrieval(
    sources: list[ParsedSource] | None,
    *,
    threshold: int = _RETRIEVAL_THRESHOLD_CHARS,
) -> bool:
    return estimate_chars(sources) > threshold
