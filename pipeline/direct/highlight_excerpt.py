# -*- coding: utf-8 -*-
"""Parse highlighted excerpt blocks from viewer highlight queries."""
from __future__ import annotations

import re

_HEADER = re.compile(
    r"^Regarding the following excerpt[^\n]*:\s*\n+",
    re.MULTILINE | re.IGNORECASE,
)
_QUOTE_WRAP = re.compile(
    r'^["\'\u201c\u2018](.+?)["\'\u201d\u2019]\s*$',
    re.DOTALL,
)
# Greedy variant for a quoted excerpt immediately followed by a blank line and a task
# instruction, e.g. '"...excerpt..."\n\ninstruction'. Greedy `.*` naturally backtracks to
# the LAST quote+blank-line boundary in the string, so this still finds the right split
# point even when the excerpt itself contains internal blank lines (e.g. a whole scraped
# multi-paragraph web page) \u2014 unlike splitting on the FIRST blank line, which truncates
# the excerpt at its own first internal paragraph break.
_QUOTE_WRAP_WITH_TRAILER = re.compile(
    r'^["\'\u201c\u2018](.*)["\'\u201d\u2019]\s*\n\n+.+$',
    re.DOTALL,
)


def extract_highlight_excerpt(full_query: str) -> str:
    text = (full_query or "").strip()
    if "Regarding the following excerpt" not in text:
        return ""
    body = _HEADER.sub("", text, count=1).strip()
    if not body:
        return ""
    m = _QUOTE_WRAP_WITH_TRAILER.match(body)
    if m:
        return m.group(1).strip()
    parts = re.split(r"\n\n+", body, maxsplit=1)
    block = (parts[0] if parts else body).strip()
    m2 = _QUOTE_WRAP.match(block)
    if m2:
        return m2.group(1).strip()
    return block


def is_highlight_query(full_query: str) -> bool:
    return "Regarding the following excerpt" in (full_query or "")
