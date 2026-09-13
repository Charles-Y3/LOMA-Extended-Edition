# -*- coding: utf-8 -*-
"""
Unified source parser for everything in the input panel.

Capabilities should consume ParsedSource (context + optional mutation units),
not re-parse uploads or depend on output format.
"""
from __future__ import annotations

import os
from typing import Any

from services.source_parser.models import ParsedSource

_KIND_BY_TYPE = {
    "text": "text",
    "image": "image",
    "media_audio": "audio",
    "media_video": "video",
    "error": "error",
    "unsupported": "error",
}


def _kind_from_filename(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    # .pdf and .txt used to fall through to the generic "text" default below —
    # unlike .docx, that meant they never registered as a real document to
    # metadata_has_office_artifact()/_is_whole_document_transform() in
    # pipeline/direct/mode_resolver.py, so a PDF could only ever reach
    # generation mode through a narrow, translate-only carve-out instead of
    # the same well-tested whole-document-transform path .docx uses.
    if ext in (".docx", ".pdf", ".txt"):
        return "document"
    if ext == ".pptx":
        return "presentation"
    if ext in (".xlsx", ".xls"):
        return "spreadsheet"
    if ext in (".mp3", ".wav", ".m4a", ".ogg", ".flac"):
        return "audio"
    if ext in (".mp4", ".mkv", ".mov", ".webm", ".avi"):
        return "video"
    if ext in (".png", ".jpg", ".jpeg", ".webp"):
        return "image"
    return "text"


def parse_file(path: str, *, filename: str | None = None) -> ParsedSource:
    """Parse a file on disk (upload or generated artifact)."""
    from services.file_io import parse_uploaded_file

    name = filename or os.path.basename(path)
    raw = parse_uploaded_file(path)
    ftype = raw.get("type") or "error"
    file_kind = _kind_from_filename(path)
    if ftype == "text" and file_kind in ("document", "presentation", "spreadsheet"):
        kind = file_kind
    else:
        kind = _KIND_BY_TYPE.get(ftype, file_kind)

    parsed = ParsedSource(
        name=name,
        kind=kind,
        path=os.path.abspath(path) if path and os.path.isfile(path) else "",
        raw=raw,
    )

    if ftype == "text":
        parsed.markdown = str(raw.get("content") or "")
    elif ftype in ("media_audio", "media_video"):
        parsed.media_path = str(raw.get("content") or path)
    elif ftype == "image":
        parsed.media_path = str(raw.get("content") or path)
    elif ftype == "error":
        parsed.error = str(raw.get("content") or "parse error")
        parsed.kind = "error"

    if kind in ("document", "presentation", "spreadsheet") and parsed.path:
        from services.office_mutation.extract import extract_units

        parsed.mutation_units = extract_units(parsed.path)

    return parsed


def parse_context_entry(entry: dict[str, Any]) -> ParsedSource:
    """Parse one item from state.active_context_files."""
    if not isinstance(entry, dict):
        return ParsedSource(name="unknown", kind="error", error="invalid context entry")

    name = entry.get("filename") or "unknown"
    ftype = entry.get("type") or "text"
    enriched_kind = entry.get("source_kind")
    enriched_path = entry.get("source_path") or ""

    if enriched_kind and enriched_kind not in ("error",):
        markdown = str(entry.get("content") or entry.get("_resolved_transcript") or "")
        parsed = ParsedSource(
            name=name,
            kind=enriched_kind,
            path=enriched_path if enriched_path and os.path.isfile(enriched_path) else "",
            markdown=markdown,
            raw=entry,
        )
        if ftype in ("media_audio", "media_video", "image"):
            parsed.media_path = str(entry.get("content") or enriched_path or "")
        if parsed.is_office() and parsed.path:
            from services.office_mutation.extract import extract_units

            parsed.mutation_units = extract_units(parsed.path)
        return parsed

    kind = _KIND_BY_TYPE.get(ftype, "text")

    if ftype == "text":
        kind = _kind_from_filename(name)
        path = enriched_path or os.path.join("data", "uploads", name)
        cached_body = str(entry.get("content") or "").strip()
        abs_path = os.path.abspath(path) if path and os.path.isfile(path) else ""
        # Reuse parsed markdown from context; do not re-read disk on follow-up turns.
        if cached_body and (
            not abs_path
            or cached_body != abs_path
            or (len(cached_body) > 260 and not os.path.isfile(cached_body))
        ):
            parsed = ParsedSource(
                name=name,
                kind=kind,
                path=abs_path,
                markdown=cached_body,
                raw=entry,
            )
            if parsed.is_office() and parsed.path:
                from services.office_mutation.extract import extract_units

                parsed.mutation_units = extract_units(parsed.path)
            return parsed
        if abs_path:
            return parse_file(abs_path, filename=name)
        parsed = ParsedSource(
            name=name,
            kind=kind,
            path="",
            markdown=cached_body,
            raw=entry,
        )
        return parsed

    path = entry.get("content") or os.path.join("data", "uploads", name)
    if isinstance(path, str) and os.path.isfile(path):
        return parse_file(path, filename=name)

    parsed = ParsedSource(name=name, kind=kind, raw=entry)
    if ftype in ("media_audio", "media_video", "image") and isinstance(path, str):
        parsed.media_path = path
    return parsed


def _markdown_from_web_payload(payload) -> str:
    from services.web_context_cache import scrape_text_from_payload

    return scrape_text_from_payload(payload)


def parse_web_link(url: str, *, scraped_markdown: str = "") -> ParsedSource:
    """Parse a web link (optionally with pre-fetched markdown)."""
    md = (scraped_markdown or "").strip()
    if not md:
        from services.web_context_cache import get_cached, scrape_text_from_payload

        cached = get_cached(url)
        if cached is not None:
            md = scrape_text_from_payload(cached)
        if not md:
            try:
                from services.web_fetch import scrape_website_text

                payload = scrape_website_text(url)
                md = _markdown_from_web_payload(payload)
            except Exception as exc:
                return ParsedSource(name=url, kind="web", error=str(exc), raw={"url": url})
    if not md:
        return ParsedSource(name=url, kind="web", error="empty web content", raw={"url": url})
    return ParsedSource(name=url, kind="web", markdown=md, raw={"url": url, "content": md})


def parse_video_link(url: str) -> ParsedSource:
    """YouTube / video URL — prefer platform captions."""
    from services.media_fetch import fetch_transcript_from_url, link_is_video_or_audio

    if not link_is_video_or_audio(url):
        return parse_web_link(url)

    cap = fetch_transcript_from_url(url)
    if cap and cap.get("content"):
        return ParsedSource(
            name=url,
            kind="video",
            markdown=str(cap["content"]),
            raw=cap,
        )
    return ParsedSource(name=url, kind="video", raw={"url": url})


def parse_all_context(
    context_files: list[dict] | None,
    web_links: list[str] | None = None,
    *,
    web_markdown: dict[str, str] | None = None,
) -> list[ParsedSource]:
    """Parse every source attached in the input panel."""
    out: list[ParsedSource] = []
    for entry in context_files or []:
        out.append(parse_context_entry(entry))
    for link in web_links or []:
        u = (link or "").strip()
        if not u:
            continue
        pre_fetched = (web_markdown or {}).get(u, "")
        if "youtube.com" in u or "youtu.be" in u:
            if pre_fetched:
                out.append(
                    ParsedSource(name=u, kind="video", markdown=pre_fetched, raw={"url": u, "content": pre_fetched})
                )
            else:
                out.append(parse_video_link(u))
        else:
            out.append(parse_web_link(u, scraped_markdown=pre_fetched))
    return out


def attach_uploaded_file(path: str, *, filename: str | None = None) -> dict[str, Any]:
    """Parse upload on disk and return enriched active_context_files entry."""
    parsed = parse_file(path, filename=filename)
    return parsed.to_context_dict()
