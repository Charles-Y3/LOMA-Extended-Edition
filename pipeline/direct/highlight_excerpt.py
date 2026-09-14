# -*- coding: utf-8 -*-
"""Parse highlighted excerpt blocks from viewer highlight queries."""
from __future__ import annotations

import re

_QUOTE_WRAP = re.compile(
    r'^["\'“‘](.+?)["\'”’]\s*$',
    re.DOTALL,
)
# Greedy variant for a quoted excerpt immediately followed by a blank line and a task
# instruction, e.g. '"...excerpt..."\n\ninstruction'. Greedy `.*` naturally backtracks to
# the LAST quote+blank-line boundary in the string, so this still finds the right split
# point even when the excerpt itself contains internal blank lines (e.g. a whole scraped
# multi-paragraph web page) — unlike splitting on the FIRST blank line, which truncates
# the excerpt at its own first internal paragraph break.
_QUOTE_WRAP_WITH_TRAILER = re.compile(
    r'^["\'“‘](.*)["\'”’]\s*\n\n+.+$',
    re.DOTALL,
)

_HEADER_RE: re.Pattern | None = None


def _header_pattern() -> re.Pattern:
    """Built from every locale's actual chat.excerpt_prefix template (see
    ui/components/viewer_query.py, ui/components/viewer_selection.py, and
    extensions/document_editor/extension.py, which all build the highlight
    query by prefixing the excerpt with tr('chat.excerpt_prefix', ...)) —
    NOT a hardcoded English literal. The old hardcoded "Regarding the
    following excerpt" check only ever matched the English locale's wording;
    in any other UI language the built query used a translated prefix that
    never matched, so is_highlight_query() silently returned False and every
    highlight/Ask-LOMA query fell through to the general chat pipeline
    instead of the dedicated excerpt+instruction handling in
    highlight_runner.py — confirmed failure: a Traditional Chinese UI's
    "translate to English" request on a highlighted excerpt got answered
    with a garbage reply instead of an actual translation."""
    global _HEADER_RE
    if _HEADER_RE is not None:
        return _HEADER_RE
    from pipeline.i18n import SUPPORTED_LOCALES, TRANSLATIONS

    placeholder = re.escape("{source}")
    parts = []
    for loc in SUPPORTED_LOCALES:
        template = (TRANSLATIONS.get(loc) or {}).get("chat.excerpt_prefix", "")
        if not template:
            continue
        parts.append(re.escape(template).replace(placeholder, ".*?"))
    pattern = r"^(?:" + "|".join(parts) + r")\s*\n+" if parts else r"(?!)"
    _HEADER_RE = re.compile(pattern, re.MULTILINE | re.DOTALL)
    return _HEADER_RE


def extract_highlight_excerpt(full_query: str) -> str:
    text = (full_query or "").strip()
    m = _header_pattern().match(text)
    if not m:
        return ""
    body = text[m.end():].strip()
    if not body:
        return ""
    mt = _QUOTE_WRAP_WITH_TRAILER.match(body)
    if mt:
        return mt.group(1).strip()
    parts = re.split(r"\n\n+", body, maxsplit=1)
    block = (parts[0] if parts else body).strip()
    m2 = _QUOTE_WRAP.match(block)
    if m2:
        return m2.group(1).strip()
    return block


def is_highlight_query(full_query: str) -> bool:
    return _header_pattern().match((full_query or "").strip()) is not None
