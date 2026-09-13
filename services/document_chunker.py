# -*- coding: utf-8 -*-
"""Split long source text into overlapping chunks with stable metadata."""
from __future__ import annotations

import re
from services.types import SourceDocument, SourceKind, TextChunk

_DEFAULT_CHUNK_CHARS = 1800
_DEFAULT_OVERLAP_CHARS = 180
_SECTION_RE = re.compile(
    r"^(---\s*(?:Page|Slide)\s+\d+\s*---|#{1,4}\s+.+)$",
    re.MULTILINE,
)


def chunk_document(
    doc: SourceDocument,
    *,
    max_chars: int = _DEFAULT_CHUNK_CHARS,
    overlap: int = _DEFAULT_OVERLAP_CHARS,
) -> list[TextChunk]:
    """Split one source into chunks, preferring breaks at section markers or paragraphs."""
    text = (doc.text or "").strip()
    if not text:
        return []

    if len(text) <= max_chars:
        return [
            TextChunk(
                chunk_id=f"{_slug(doc.name)}:0",
                source=doc.name,
                source_kind=doc.source_kind,
                text=text,
                index=0,
            )
        ]

    sections = _split_sections(text)
    chunks: list[TextChunk] = []
    buf = ""
    section_label: str | None = None

    def flush() -> None:
        nonlocal buf, section_label
        piece = buf.strip()
        if not piece:
            buf = ""
            return
        idx = len(chunks)
        chunks.append(
            TextChunk(
                chunk_id=f"{_slug(doc.name)}:{idx}",
                source=doc.name,
                source_kind=doc.source_kind,
                text=piece,
                index=idx,
                section=section_label,
            )
        )
        buf = ""

    for block, label in sections:
        if not block.strip():
            continue
        if len(block) <= max_chars:
            if buf and len(buf) + len(block) + 2 > max_chars:
                flush()
                tail = buf[-overlap:] if overlap and buf else ""
                buf = tail
            if section_label != label and buf:
                flush()
            section_label = label
            buf = f"{buf}\n\n{block}".strip() if buf else block
            if len(buf) >= max_chars:
                flush()
                if overlap and chunks:
                    buf = chunks[-1].text[-overlap:]
            continue

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", block) if p.strip()]
        for para in paragraphs:
            if len(para) > max_chars:
                for piece in _hard_split(para, max_chars, overlap):
                    if buf:
                        flush()
                    section_label = label
                    buf = piece
                    flush()
                continue
            if buf and len(buf) + len(para) + 2 > max_chars:
                flush()
                if overlap and chunks:
                    buf = chunks[-1].text[-overlap:]
            section_label = label
            buf = f"{buf}\n\n{para}".strip() if buf else para

    flush()
    return chunks


def chunk_sources(
    sources: list[SourceDocument],
    *,
    max_chars: int = _DEFAULT_CHUNK_CHARS,
    overlap: int = _DEFAULT_OVERLAP_CHARS,
) -> list[TextChunk]:
    out: list[TextChunk] = []
    for doc in sources:
        out.extend(chunk_document(doc, max_chars=max_chars, overlap=overlap))
    return out


def _slug(name: str) -> str:
    base = re.sub(r"[^\w.\-]+", "_", (name or "source").strip())[:48]
    return base or "source"


def _split_sections(text: str) -> list[tuple[str, str | None]]:
    """Return (block_text, section_label) preserving order."""
    matches = list(_SECTION_RE.finditer(text))
    if not matches:
        return [(text, None)]

    parts: list[tuple[str, str | None]] = []
    cursor = 0
    current_label: str | None = None
    for m in matches:
        before = text[cursor : m.start()].strip()
        if before:
            parts.append((before, current_label))
        current_label = m.group(0).strip()
        cursor = m.end()
    tail = text[cursor:].strip()
    if tail:
        parts.append((tail, current_label))
    return parts or [(text, None)]


def _hard_split(text: str, max_chars: int, overlap: int) -> list[str]:
    pieces: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        piece = text[start:end]
        if end < len(text):
            break_at = piece.rfind(" ")
            if break_at > max_chars // 2:
                end = start + break_at
                piece = text[start:end]
        pieces.append(piece.strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return [p for p in pieces if p]
