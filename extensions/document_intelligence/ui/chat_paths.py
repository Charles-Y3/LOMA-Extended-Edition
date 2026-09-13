# -*- coding: utf-8 -*-
"""Parse Document Intelligence chat paths for interactive buttons."""
from __future__ import annotations

import html
import re
from urllib.parse import unquote

_PATH_BLOCK_RE = re.compile(
    r"[^\n]+:\s*\[([^\]]+)\]\(loma-open:([^)]+)\)\s*\n{1,2}"
    r"[^\n]+:\s*\[([^\]]+)\]\(loma-open:([^)]+)\)",
    re.MULTILINE,
)

_HTML_BLOCK_RE = re.compile(
    r"[^\n]+:\s*<a[^>]+data-path=\"([^\"]+)\"[^>]*>([^<]+)</a>\s*\n{1,2}"
    r"[^\n]+:\s*<a[^>]+data-path=\"([^\"]+)\"[^>]*>([^<]+)</a>",
    re.MULTILINE | re.IGNORECASE,
)


def extract_path_blocks(content: str) -> list[dict[str, str]]:
    text = content or ""
    blocks: list[dict[str, str]] = []
    for m in _PATH_BLOCK_RE.finditer(text):
        blocks.append(
            {
                "folder_label": m.group(1),
                "folder_path": unquote(m.group(2)),
                "file_label": m.group(3),
                "file_path": unquote(m.group(4)),
            }
        )
    for m in _HTML_BLOCK_RE.finditer(text):
        blocks.append(
            {
                "folder_label": html.unescape(m.group(2)),
                "folder_path": html.unescape(m.group(1)),
                "file_label": html.unescape(m.group(4)),
                "file_path": html.unescape(m.group(3)),
            }
        )
    return blocks


def strip_path_blocks(content: str) -> str:
    text = content or ""
    text = _PATH_BLOCK_RE.sub("", text)
    text = _HTML_BLOCK_RE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_content_with_path_blocks(content: str) -> list[str | dict[str, str]]:
    """Split message text into ordered text/path-block parts, in document order.

    Unlike extract_path_blocks + strip_path_blocks (which pull every path block
    out to render as one list at the end), this keeps each block's position —
    the caller can render text parts as markdown and dict parts as buttons in
    sequence, so a folder/file link stays right under its own citation instead
    of being regrouped with every other citation's links."""
    text = content or ""
    matches = sorted(
        [*_PATH_BLOCK_RE.finditer(text), *_HTML_BLOCK_RE.finditer(text)],
        key=lambda m: m.start(),
    )
    parts: list[str | dict[str, str]] = []
    pos = 0
    for m in matches:
        if m.start() < pos:
            continue  # overlapping match from the other pattern — already covered
        if m.start() > pos:
            parts.append(text[pos : m.start()])
        if m.re is _PATH_BLOCK_RE:
            parts.append(
                {
                    "folder_label": m.group(1),
                    "folder_path": unquote(m.group(2)),
                    "file_label": m.group(3),
                    "file_path": unquote(m.group(4)),
                }
            )
        else:
            parts.append(
                {
                    "folder_label": html.unescape(m.group(2)),
                    "folder_path": html.unescape(m.group(1)),
                    "file_label": html.unescape(m.group(4)),
                    "file_path": html.unescape(m.group(3)),
                }
            )
        pos = m.end()
    if pos < len(text):
        parts.append(text[pos:])
    return parts
