# -*- coding: utf-8 -*-
"""Build and cache per-source digests after parse."""
from __future__ import annotations

import hashlib
import re

from pipeline.context.types import SourceDigest, make_preview


def _digest_id(name: str, kind: str, text: str) -> str:
    key = f"{name}|{kind}|{len(text)}|{hashlib.sha256(text[:4096].encode()).hexdigest()[:16]}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def build_source_digests(
    parsed_sources: list,
    *,
    media_blocks: list[tuple[str, str]] | None = None,
    image_paths: list[str] | None = None,
) -> list[SourceDigest]:
    """One digest per parsed source; media_blocks are gap placeholders only
    (pending transcription / vision required) — a successfully transcribed
    audio/video source already carries its digest via context_text() below,
    so it must never also appear in media_blocks (that would double-count it)."""
    digests: list[SourceDigest] = []
    seen_names: set[str] = set()

    for ps in parsed_sources or []:
        name = getattr(ps, "name", None) or "source"
        kind = getattr(ps, "kind", None) or "document"
        if kind == "image":
            path = getattr(ps, "media_path", None) or getattr(ps, "path", None) or ""
            text = f"[Image attachment: {name}]"
            preview, trunc = make_preview(text)
            digests.append(
                SourceDigest(
                    digest_id=_digest_id(name, kind, text),
                    name=name,
                    kind=kind,
                    char_count=len(text),
                    full_text=text,
                    preview=preview,
                    truncated_in_preview=trunc,
                    vision_paths=[path] if path else [],
                )
            )
            continue

        text = ""
        if hasattr(ps, "context_text"):
            text = (ps.context_text() or "").strip()
        if not text:
            text = (getattr(ps, "markdown", None) or "").strip()
        if not text and kind in ("audio", "video"):
            continue

        if name in seen_names and kind != "web":
            base = name
            n = 2
            while name in seen_names:
                name = f"{base} ({n})"
                n += 1
        seen_names.add(name)

        preview, trunc = make_preview(text)
        paths: list[str] = []
        if kind == "video" and image_paths:
            paths = list(image_paths)

        digests.append(
            SourceDigest(
                digest_id=_digest_id(name, kind, text),
                name=name,
                kind=kind,
                char_count=len(text),
                full_text=text,
                preview=preview,
                truncated_in_preview=trunc,
                vision_paths=paths,
            )
        )

    for name, block in media_blocks or []:
        text = (block or "").strip()
        if not text:
            continue
        name = name or "media_transcript"
        if name in seen_names:
            base = name
            n = 2
            while name in seen_names:
                name = f"{base} ({n})"
                n += 1
        seen_names.add(name)
        preview, trunc = make_preview(text)
        digests.append(
            SourceDigest(
                digest_id=_digest_id(name, "media", text),
                name=name,
                kind="media",
                char_count=len(text),
                full_text=text,
                preview=preview,
                truncated_in_preview=trunc,
            )
        )

    return digests


def digest_index_markdown(digests: list[SourceDigest], *, strategy: str, total_chars: int) -> str:
    lines = [
        "## Source digest index",
        f"Strategy: **{strategy}** | Total parsed: **{total_chars:,}** characters across **{len(digests)}** source(s)",
        "",
    ]
    for d in digests:
        lines.append(d.to_index_line())
        if d.preview:
            lines.append(f"  Preview: {d.preview[:400].replace(chr(10), ' ')}")
        lines.append("")
    return "\n".join(lines).strip()


def join_digest_plain_text(digests: list[SourceDigest]) -> str:
    """Full source bodies only — no Source:/digest headers (for translate/summarize)."""
    parts: list[str] = []
    for d in digests:
        if not (d.full_text or "").strip():
            continue
        if d.kind == "image":
            continue
        parts.append(d.full_text.strip())
    return "\n\n".join(parts).strip()


def per_digest_bodies(digests: list[SourceDigest]) -> list[tuple[str, str]]:
    """(name, full_text) pairs — same filter as join_digest_plain_text, but keeps each
    document's text separate so a batch/translate pass can chunk per document instead of
    treating the concatenation as one undifferentiated blob (a chunk spanning two documents'
    boundary, or a dedupe pass comparing paragraphs across documents, can otherwise drop real
    content from the second/third document)."""
    out: list[tuple[str, str]] = []
    for d in digests:
        text = (d.full_text or "").strip()
        if not text or d.kind == "image":
            continue
        out.append((d.name or "", text))
    return out


def join_digest_full_text(digests: list[SourceDigest]) -> str:
    parts: list[str] = []
    for d in digests:
        if not (d.full_text or "").strip():
            continue
        if d.kind == "image":
            continue
        parts.append(f"## Source: {d.name}\n\n{d.full_text.strip()}")
    return "\n\n".join(parts).strip()
