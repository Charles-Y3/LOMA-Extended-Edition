# -*- coding: utf-8 -*-
"""Localize research progress log lines and phase labels."""
from __future__ import annotations

import re

from pipeline.i18n import t as tr
from services.console_i18n import translate_console_line

_LEGACY: list[tuple[re.Pattern[str], str, tuple[str, ...]]] = [
    (re.compile(r"^Planning web search queries…$"), "research.progress.plan_search", ()),
    (
        re.compile(r"^Searching the web \(target (\d+) sources\)…$"),
        "research.progress.searching_web",
        ("target",),
    ),
    (re.compile(r"^Reading: (.+)$"), "research.progress.reading", ("title",)),
    (re.compile(r"^No text from: (.+)$"), "research.progress.no_text", ("title",)),
    (
        re.compile(r"^Loaded (\d+) candidate web page\(s\)\.$"),
        "research.progress.loaded_pages",
        ("count",),
    ),
    (
        re.compile(r"^Reading (\d+) uploaded file\(s\)…$"),
        "research.progress.reading_uploads",
        ("count",),
    ),
    (re.compile(r"^Skip missing file: (.+)$"), "research.progress.skip_missing", ("name",)),
    (re.compile(r"^Parsed upload: (.+)$"), "research.progress.parsed_upload", ("name",)),
    (
        re.compile(r"^Upload parse failed \((.+)\): (.+)$"),
        "research.progress.upload_failed",
        ("name", "error"),
    ),
    (
        re.compile(r"^No external sources — writing from brief and general knowledge\.$"),
        "research.progress.no_external",
        (),
    ),
    (
        re.compile(r"^Ranking sources by credibility and relevance…$"),
        "research.progress.ranking",
        (),
    ),
    (
        re.compile(r"^Assessing credibility for (\d+) candidate source\(s\)…$"),
        "research.progress.assessing",
        ("count",),
    ),
    (
        re.compile(r"^Selected (\d+) source\(s\) for deep scan\.$"),
        "research.progress.selected",
        ("count",),
    ),
    (
        re.compile(
            r"^  (S\d+) cred=([\d.]+) rel=([\d.]+) — (.+)$"
        ),
        "research.progress.source_score",
        ("id", "cred", "rel", "title"),
    ),
    (re.compile(r"^Scanning (S\d+): (.+)…$"), "research.progress.scanning", ("id", "title")),
    (
        re.compile(r"^Synthesizing and comparing sources…$"),
        "research.progress.synthesizing",
        (),
    ),
    (
        re.compile(r"^Comparing findings across sources…$"),
        "research.progress.comparing",
        (),
    ),
    (re.compile(r"^Writing (.+)…$"), "research.progress.writing", ("format",)),
    (re.compile(r"^Research complete\.$"), "research.progress.complete", ()),
]


def localize_progress_line(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return line
    translated = translate_console_line(text)
    if translated != text:
        return translated
    for pattern, key, fields in _LEGACY:
        m = pattern.match(text)
        if not m:
            continue
        if not fields:
            return tr(key)
        return tr(key, **{fields[i]: m.group(i + 1) for i in range(len(fields))})
    return line


def phase_label(phase: str) -> str:
    pid = (phase or "starting").strip().lower()
    return tr(f"research.phase.{pid}")
