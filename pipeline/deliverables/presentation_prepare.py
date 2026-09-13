# -*- coding: utf-8 -*-
"""Agentic-only normalization before presentation compile."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.deliverables.presentation_deck import (
    DeckSpec,
    SlideSpec,
    deck_spec_from_completed,
    parse_deck_spec,
    validate_deck_spec,
)
from pipeline.deliverables.presentation_limits import (
    MAX_BULLET_CHARS,
    MAX_BULLETS_PER_SLIDE,
    MAX_CHARS_PER_CONTENT_SLIDE,
)
from pipeline.deliverables.presentation_theme import (
    PresentationTheme,
    extract_theme_from_text,
    resolve_theme,
    theme_meta_block,
)
from pipeline.deliverables.specs import infer_slide_count
from pipeline.validate.result import ValidationResult
from services.presentation_markdown import (
    normalize_presentation_markdown,
    parse_speaker_notes_line,
    sanitize_bullet_text,
    sanitize_slide_title,
    split_presentation_slides,
)

_SLIDE_MARKER = re.compile(r"^---\s*Slide\s+\d+\s*---\s*$", re.MULTILINE | re.IGNORECASE)
_NUMBERED_BULLET = re.compile(r"^\d+[\).\]]\s+")
# "[IMAGE: ...]" is what slide_author (pipeline/direct/task_roles.py) is told
# to emit; "[IMAGE_PROMPT: ...]" is the other authoring path's shape
# (services/artifact_build.py) — accept both for robustness.
_IMAGE_LINE_RE = re.compile(r"^\[IMAGE(?:_PROMPT)?:\s*(.+?)\]\s*$", re.IGNORECASE)
_LAYOUT_LINE_RE = re.compile(r"^\[LAYOUT:\s*(.+?)\]\s*$", re.IGNORECASE)


@dataclass
class PrepareResult:
    ok: bool
    markdown: str = ""
    theme: PresentationTheme | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def repair_hint(self) -> str:
        if self.ok:
            return ""
        return "Fix presentation output: " + "; ".join(self.errors[:4])


def prepare_agentic_presentation(
    markdown: str,
    *,
    workflow_instruction: str = "",
    completed_steps: dict[str, str] | None = None,
    plan_steps: list | None = None,
) -> PrepareResult:
    """
    Normalize markdown, enforce title slide + density limits, resolve theme.
    Uses deck spec from outline phase when assemble markdown is weak.
    """
    errors: list[str] = []
    warnings: list[str] = []
    slide_count = infer_slide_count(workflow_instruction or "")

    from pipeline.deliverables.presentation_finalize import (
        finalize_presentation_source,
        is_compile_ready_presentation,
    )

    if completed_steps is not None and plan_steps is not None and not is_compile_ready_presentation(markdown):
        class _MiniPlan:
            steps = plan_steps

        markdown = finalize_presentation_source(
            completed_steps,
            _MiniPlan(),
            markdown,
            workflow_instruction,
        )

    theme, body = extract_theme_from_text(markdown, query=workflow_instruction)
    body = normalize_presentation_markdown(body)

    deck = None
    if completed_steps and plan_steps:
        deck = deck_spec_from_completed(completed_steps, plan_steps)

    if deck:
        v = validate_deck_spec(deck, slide_count=slide_count)
        if v.valid and not is_compile_ready_presentation(body):
            theme = resolve_theme(query=workflow_instruction, design=deck.design)
            body = deck.to_markdown()
        else:
            warnings.extend(v.errors)

    if not body.strip() and deck:
        body = deck.to_markdown()
        theme = resolve_theme(query=workflow_instruction, design=deck.design)

    body, struct_errors = _enforce_structure(body, slide_count=slide_count)
    errors.extend(struct_errors)

    body, density_errors, density_warnings = _enforce_density(body)
    errors.extend(density_errors)
    warnings.extend(density_warnings)

    if not body.strip():
        errors.append("presentation markdown is empty after preparation")
        return PrepareResult(ok=False, errors=errors, warnings=warnings)

    theme = resolve_theme(query=workflow_instruction, design=(deck.design if deck else {}))
    if not body.startswith("<!-- loma-theme"):
        body = theme_meta_block(theme) + "\n\n" + body

    fit = validate_presentation_fit(body, slide_count=slide_count)
    if not fit.valid:
        repairable = [e for e in fit.errors if "expected" in e and "found" in e]
        if repairable and slide_count and deck:
            body = deck.to_markdown()
            body, _ = _enforce_structure(body, slide_count=slide_count)
            fit = validate_presentation_fit(body, slide_count=slide_count)
        if not fit.valid:
            errors.extend(fit.errors)

    ok = bool(body.strip()) and fit.valid
    return PrepareResult(
        ok=ok,
        markdown=body.strip(),
        theme=theme,
        errors=errors,
        warnings=warnings,
    )


def validate_presentation_fit(
    markdown: str,
    *,
    slide_count: int | None = None,
) -> ValidationResult:
    """Heuristic checks for compile safety (overflow, slide count)."""
    errors: list[str] = []
    theme, body = extract_theme_from_text(markdown)
    _ = theme
    body = normalize_presentation_markdown(body)
    markers = _SLIDE_MARKER.findall(body)
    blocks = split_presentation_slides(body)
    if not blocks and not markers:
        errors.append("no slides detected after normalization")
        return ValidationResult(valid=False, errors=errors)

    if slide_count is not None and slide_count > 0:
        found = len(markers) if markers else len(blocks)
        if found != slide_count:
            errors.append(f"expected {slide_count} slides, found {found}")

    for i, block in enumerate(blocks):
        bullets = [ln[2:].strip() for ln in block.splitlines() if ln.strip().startswith("- ")]
        # [IMAGE:]/[NOTES:]/[LAYOUT:] lines never render as visible slide text
        # (notes go to the presenter-notes pane, image becomes a picture, layout
        # is a compile hint) — counting a paragraph-length speaker note against
        # the visible slide's character budget used to flag decks as "too long"
        # that actually fit fine, once those lines stopped being silently
        # dropped before reaching this check.
        visible_lines = [
            ln for ln in block.splitlines()
            if not _IMAGE_LINE_RE.match(ln.strip())
            and not _LAYOUT_LINE_RE.match(ln.strip())
            and not parse_speaker_notes_line(ln.strip())
        ]
        prose_len = len(re.sub(r"^#+\s+", "", "\n".join(visible_lines), flags=re.MULTILINE))
        if i == 0 and len(bullets) > 1:
            errors.append("slide 1 must be title-only (at most one subtitle line)")
        if i > 0 and len(bullets) > MAX_BULLETS_PER_SLIDE:
            errors.append(f"slide {i + 1} has too many bullets")
        if i > 0 and prose_len > MAX_CHARS_PER_CONTENT_SLIDE:
            errors.append(f"slide {i + 1} text is too long for the slide area")
        for b in bullets:
            if len(b) > MAX_BULLET_CHARS:
                errors.append(f"slide {i + 1} has a bullet that is too long")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def _enforce_structure(body: str, *, slide_count: int | None) -> tuple[str, list[str]]:
    errors: list[str] = []
    parsed = [_parse_slide_block(b) for b in split_presentation_slides(body)]
    parsed = [p for p in parsed if p.get("title") or p.get("bullets")]

    if not parsed:
        return body, errors

    if parsed and parsed[0].get("bullets"):
        bullets0 = parsed[0]["bullets"]
        subtitle = bullets0[0] if bullets0 else ""
        parsed[0] = {
            "title": parsed[0].get("title") or "Presentation",
            "subtitle": subtitle,
            "bullets": [],
            "image": parsed[0].get("image", ""),
            "notes": parsed[0].get("notes", ""),
            "layout": parsed[0].get("layout", ""),
        }

    for i in range(1, len(parsed)):
        parsed[i]["bullets"] = [
            _truncate(b, MAX_BULLET_CHARS)
            for b in (parsed[i].get("bullets") or [])[:MAX_BULLETS_PER_SLIDE]
        ]

    if slide_count is not None:
        parsed = _fit_slide_count(parsed, slide_count, errors)

    out_lines: list[str] = []
    for idx, slide in enumerate(parsed, start=1):
        out_lines.append(f"--- Slide {idx} ---")
        if idx == 1:
            out_lines.append(f"# {slide.get('title') or 'Presentation'}")
            sub = (slide.get("subtitle") or "").strip()
            if sub:
                out_lines.append(sub)
        else:
            clean_title = sanitize_slide_title(slide.get("title") or "") or "Untitled"
            out_lines.append(f"## {clean_title}")
            for b in slide.get("bullets") or []:
                out_lines.append(f"- {b}")
        layout = (slide.get("layout") or "").strip()
        if layout:
            out_lines.append(f"[LAYOUT: {layout}]")
        image = (slide.get("image") or "").strip()
        if image:
            out_lines.append(f"[IMAGE: {image}]")
        notes = (slide.get("notes") or "").strip()
        if notes:
            out_lines.append(f"[NOTES: {notes}]")
        out_lines.append("")
    return "\n".join(out_lines).strip(), errors


def _parse_slide_block(block: str) -> dict:
    """Pull title/bullets plus the [IMAGE:]/[NOTES:]/[LAYOUT:] annotation
    lines out of one slide block.

    Bullets accept "-", "*", or a numbered prefix ("1. "/"1) ") — the
    authoring system prompt (task_roles.py) asks specifically for "- ", but
    local models routinely use "*" instead (same tolerance every other
    bullet-detection spot in services/presentation_markdown.py already has;
    this was the one holdout that only recognized "- ", silently dropping
    every "*" bullet a slide had). The [IMAGE:]/[NOTES:]/[LAYOUT:] lines used
    to be dropped outright here — this function only ever returned
    title/subtitle/bullets — so even a perfectly-formatted slide lost its
    image marker and speaker notes the moment _enforce_structure() rebuilt
    the deck from this dict. See pipeline/deliverables/presentation_reattach.py
    for the other (deck-spec-based, and only conditionally reached) patch for
    the same loss."""
    title = ""
    subtitle = ""
    bullets: list[str] = []
    image = ""
    notes = ""
    layout = ""
    for line in block.splitlines():
        line = line.strip()
        if not line or re.match(r"^---\s*Slide", line, re.I):
            continue
        img_m = _IMAGE_LINE_RE.match(line)
        if img_m:
            image = img_m.group(1).strip()
            continue
        layout_m = _LAYOUT_LINE_RE.match(line)
        if layout_m:
            layout = layout_m.group(1).strip()
            continue
        note_text = parse_speaker_notes_line(line)
        if note_text:
            notes = note_text
            continue
        if line.startswith("# "):
            title = sanitize_slide_title(line[2:])
            continue
        if line.startswith("## "):
            title = sanitize_slide_title(line[3:])
            continue
        bullet_text = ""
        if line.startswith(("- ", "* ")):
            bullet_text = line[2:]
        elif line.startswith("> "):
            # A markdown blockquote — the shape a model reaches for to show a
            # standout quote, even though the system prompt (task_roles.py)
            # asks for an explicit "[LAYOUT: quote]" marker instead. Without
            # this, "> quote text" / "> — attribution" lines matched none of
            # the branches here and were silently dropped, leaving the slide
            # looking title-only and triggering the generic
            # "Clarify why X matters" filler fallback below.
            bullet_text = line[2:]
            if not layout:
                layout = "quote"
        else:
            m = _NUMBERED_BULLET.match(line)
            if m:
                bullet_text = line[m.end():]
        if bullet_text:
            bullet = sanitize_bullet_text(bullet_text)
            if bullet:
                bullets.append(bullet)
        elif not title:
            cleaned = sanitize_slide_title(line)
            if cleaned:
                title = cleaned
        else:
            # A content line with no recognized bullet/quote prefix — the
            # system prompt (task_roles.py) asks for "- " bullets, but models
            # (especially smaller ones) sometimes ignore that and write
            # flowing paragraph sentences instead. Previously this matched no
            # branch at all and was silently dropped, leaving the slide
            # looking title-only and triggering the generic "Clarify why X
            # matters" filler fallback below — treat it as body content
            # instead; _enforce_structure's own truncate/cap already handles
            # an overlong line safely.
            cleaned = sanitize_bullet_text(line)
            if cleaned:
                # A plain paragraph often bundles several sentences on one
                # line — split on sentence boundaries so each becomes its own
                # bullet instead of one line that _enforce_structure later
                # truncates at MAX_BULLET_CHARS, silently dropping most of
                # the paragraph's content.
                for sentence in re.split(r"(?<=[.!?])\s+", cleaned):
                    sentence = sentence.strip()
                    if sentence:
                        bullets.append(sentence)
    return {
        "title": title,
        "subtitle": subtitle,
        "bullets": bullets,
        "image": image,
        "notes": notes,
        "layout": layout,
    }


def _fit_slide_count(parsed: list[dict], target: int, errors: list[str]) -> list[dict]:
    if len(parsed) == target:
        return parsed
    if len(parsed) > target:
        head = parsed[: target - 1]
        tail = parsed[target - 1 :]
        merged = {
            "title": tail[0].get("title") or "Summary",
            "bullets": [],
        }
        for slide in tail:
            merged["bullets"].extend(slide.get("bullets") or [])
        merged["bullets"] = merged["bullets"][:MAX_BULLETS_PER_SLIDE]
        errors.append(f"merged {len(parsed)} slides into {target}")
        return head + [merged]
    while len(parsed) < target:
        parsed.append(
            {
                "title": "Key idea",
                "bullets": ["Add supporting detail for this theme."],
            }
        )
        errors.append(f"padded deck to {target} slides")
    return parsed


def _enforce_density(body: str) -> tuple[str, list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    blocks = split_presentation_slides(body)
    if not blocks:
        return body, errors, warnings

    if len(blocks) <= 1:
        return body, errors, warnings

    new_blocks: list[str] = []
    slide_num = 0
    for block in blocks:
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        slide_num += 1
        title = ""
        bullets: list[str] = []
        image = ""
        notes = ""
        layout = ""
        for line in lines:
            if re.match(r"^---\s*Slide", line, re.I):
                continue
            img_m = _IMAGE_LINE_RE.match(line)
            if img_m:
                image = img_m.group(1).strip()
                continue
            layout_m = _LAYOUT_LINE_RE.match(line)
            if layout_m:
                layout = layout_m.group(1).strip()
                continue
            note_text = parse_speaker_notes_line(line)
            if note_text:
                notes = note_text
                continue
            if line.startswith(("# ", "## ")):
                title = re.sub(r"^#+\s+", "", line).strip()
            elif line.startswith(("- ", "* ")):
                bullets.append(_truncate(line[2:].strip(), MAX_BULLET_CHARS))
            else:
                m = _NUMBERED_BULLET.match(line)
                if m:
                    bullets.append(_truncate(line[m.end():].strip(), MAX_BULLET_CHARS))

        if slide_num == 1:
            new_blocks.append(
                _block_markdown(1, title, bullets[0] if bullets else "", [], image=image, notes=notes, layout=layout)
            )
            continue

        while len(bullets) > MAX_BULLETS_PER_SLIDE:
            chunk = bullets[:MAX_BULLETS_PER_SLIDE]
            bullets = bullets[MAX_BULLETS_PER_SLIDE:]
            # Image/notes/layout belong to the slide's LAST chunk (where they
            # appeared in the source) — an earlier chunk here is a synthetic
            # "(continued)" split, not where the original annotation lines were.
            new_blocks.append(_block_markdown(slide_num, title or "Details", "", chunk))
            slide_num += 1
            title = f"{title} (continued)" if title else "Details"
            warnings.append(f"split overcrowded slide into slide {slide_num}")

        new_blocks.append(_block_markdown(slide_num, title, "", bullets, image=image, notes=notes, layout=layout))

    out: list[str] = []
    for i, blk in enumerate(new_blocks, start=1):
        out.append(f"--- Slide {i} ---")
        out.append(blk)
        out.append("")
    return "\n".join(out).strip(), errors, warnings


def _block_markdown(
    _index: int,
    title: str,
    subtitle: str,
    bullets: list[str],
    *,
    image: str = "",
    notes: str = "",
    layout: str = "",
) -> str:
    lines: list[str] = []
    if _index == 1:
        lines.append(f"# {title or 'Presentation'}")
        if subtitle:
            lines.append(f"- {subtitle}")
    else:
        lines.append(f"## {title or 'Slide'}")
        for b in bullets:
            lines.append(f"- {b}")
    if layout.strip():
        lines.append(f"[LAYOUT: {layout.strip()}]")
    if image.strip():
        lines.append(f"[IMAGE: {image.strip()}]")
    if notes.strip():
        lines.append(f"[NOTES: {notes.strip()}]")
    return "\n".join(lines)


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1]
    last_space = clipped.rfind(" ")
    if last_space > limit * 0.6:
        clipped = clipped[:last_space]
    return clipped.rstrip() + "…"


def merge_deck_and_copy(deck: DeckSpec, copy_text: str) -> str:
    """Optional: merge draft-phase slide copy into deck spec."""
    return deck.to_markdown()
