# -*- coding: utf-8 -*-
"""Inline markdown emphasis (**bold**, *italic*) for docx/pptx compilers."""
from __future__ import annotations

import re
from typing import Iterator

_INLINE_RE = re.compile(
    r"\*\*\*(.+?)\*\*\*"
    r"|\*\*(.+?)\*\*"
    r"|__(.+?)__"
    r"|\*(.+?)\*"
    r"|(?<!\w)_(.+?)_(?!\w)"
)
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)


def iter_inline_markdown_segments(text: str) -> Iterator[tuple[str, bool, bool]]:
    """Yield (text, bold, italic) segments from inline markdown."""
    if not text:
        return
    pos = 0
    for match in _INLINE_RE.finditer(text):
        if match.start() > pos:
            yield text[pos : match.start()], False, False
        if match.group(1) is not None:
            yield match.group(1), True, True
        elif match.group(2) is not None or match.group(3) is not None:
            yield (match.group(2) or match.group(3)), True, False
        else:
            yield (match.group(4) or match.group(5)), False, True
        pos = match.end()
    if pos < len(text):
        yield text[pos:], False, False


def add_inline_markdown_runs(paragraph, text: str) -> None:
    """Append bold/italic runs to a python-docx paragraph."""
    lines = _BR_RE.split(text)
    for i, line in enumerate(lines):
        if i > 0:
            paragraph.add_run().add_break()
        for segment, bold, italic in iter_inline_markdown_segments(line):
            run = paragraph.add_run(segment)
            run.bold = bold
            run.italic = italic


def add_docx_paragraph_with_inline(doc, text: str, *, style: str | None = None):
    paragraph = doc.add_paragraph(style=style) if style else doc.add_paragraph()
    add_inline_markdown_runs(paragraph, text)
    return paragraph


def add_docx_heading_with_inline(doc, text: str, level: int):
    paragraph = doc.add_heading("", level=min(max(level, 0), 9))
    add_inline_markdown_runs(paragraph, text)
    return paragraph


def parse_markdown_heading(stripped: str) -> tuple[int | None, str]:
    """Return (docx heading level, title text) or (None, line) if not a heading."""
    match = re.match(r"^(#{1,6})\s+(.+)$", stripped)
    if not match:
        return None, stripped
    level = min(len(match.group(1)) - 1, 9)
    return max(level, 0), match.group(2).strip()


def set_pptx_paragraph_inline(paragraph, text: str) -> None:
    """Replace paragraph text with inline markdown emphasis runs."""
    paragraph.text = ""
    for segment, bold, italic in iter_inline_markdown_segments(text):
        run = paragraph.add_run()
        run.text = segment
        run.font.bold = bold
        run.font.italic = italic
