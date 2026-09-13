# -*- coding: utf-8 -*-
"""Deterministically re-attach planner-sourced speaker notes and visual descriptions to
slide_author's rewritten markdown.

slide_author is a second, separate LLM call asked to "expand bullet stubs into vivid
lines" — it is not reliable at faithfully carrying forward non-visible `[IMAGE: ...]` /
`[NOTES: ...]` annotation lines through that rewrite. Since the original deck_planner
spec already has this data parsed and validated, re-attach it programmatically by slide
position rather than depending on LLM compliance.
"""
from __future__ import annotations

import re

from pipeline.deliverables.presentation_deck import DeckSpec, _is_placeholder_title
from services.presentation_markdown import parse_speaker_notes_line, split_presentation_slides

_IMAGE_LINE_RE = re.compile(r"\[IMAGE:\s*.+?\]", re.IGNORECASE)
_LAYOUT_LINE_RE = re.compile(r"\[LAYOUT:\s*.+?\]", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,2})\s+(.*)$")


def _has_image_marker(block: str) -> bool:
    return bool(_IMAGE_LINE_RE.search(block))


def _has_layout_marker(block: str) -> bool:
    return bool(_LAYOUT_LINE_RE.search(block))


def _has_notes_marker(block: str) -> bool:
    return any(parse_speaker_notes_line(line) for line in block.splitlines())


def _fix_placeholder_title(block: str, real_title: str) -> str:
    """slide_author rewrites titles freely and sometimes echoes a generic label
    (e.g. 'Title Slide') instead of the deck plan's real title — restore it."""
    if not real_title:
        return block
    lines = block.splitlines()
    for idx, line in enumerate(lines):
        m = _HEADING_RE.match(line.strip())
        if not m:
            continue
        if _is_placeholder_title(m.group(2).strip()):
            lines[idx] = f"{m.group(1)} {real_title}"
        break
    return "\n".join(lines)


def reattach_notes_and_visuals(markdown: str, spec: DeckSpec) -> str:
    """Append `[IMAGE: ...]` / `[NOTES: ...]` lines from the deck spec for each slide
    that doesn't already carry one, matching slides by position. Also restores the
    slide title when slide_author's rewrite left a generic placeholder heading."""
    blocks = split_presentation_slides(markdown)
    if not blocks or not spec.slides:
        return markdown

    out_blocks: list[str] = []
    for i, block in enumerate(blocks):
        extra_lines: list[str] = []
        if i < len(spec.slides):
            slide = spec.slides[i]
            block = _fix_placeholder_title(block, slide.title)
            if (
                slide.visual_description
                and slide.visual_type not in ("", "none")
                and not _has_image_marker(block)
            ):
                extra_lines.append(f"[IMAGE: {slide.visual_description}]")
            if slide.layout in ("section", "quote") and not _has_layout_marker(block):
                extra_lines.append(f"[LAYOUT: {slide.layout}]")
            if slide.notes and not _has_notes_marker(block):
                extra_lines.append(f"[NOTES: {slide.notes}]")
        merged = block.rstrip()
        if extra_lines:
            merged = merged + "\n" + "\n".join(extra_lines)
        out_blocks.append(merged)

    out_lines: list[str] = []
    for idx, block in enumerate(out_blocks, start=1):
        out_lines.append(f"--- Slide {idx} ---")
        out_lines.append(block)
        out_lines.append("")
    return "\n".join(out_lines).strip()
