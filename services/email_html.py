# -*- coding: utf-8 -*-
"""Convert email HTML bodies to safe display HTML and plain text."""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data and data.strip():
            self._parts.append(data)

    def get_text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "\n".join(self._parts)).strip()


def html_to_plain(html_body: str) -> str:
    if not html_body:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html_body)
        parser.close()
        text = parser.get_text()
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html_body)
    return html.unescape(re.sub(r"[ \t]+\n", "\n", text)).strip()


def sanitize_html_for_view(html_body: str) -> str:
    """Minimal sanitization for in-app reading (no scripts/styles)."""
    if not html_body:
        return "<p></p>"
    text = html_body
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", "", text)
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", "", text)
    text = re.sub(r'(?i)\s+on\w+\s*=\s*["\'][^"\']*["\']', "", text)
    return text.strip() or "<p></p>"
