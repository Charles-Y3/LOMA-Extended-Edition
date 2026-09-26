# -*- coding: utf-8 -*-
"""Parse and validate agentic presentation deck specs."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pipeline.deliverables.presentation_limits import (
    ALLOWED_LAYOUTS,
    MAX_BULLET_CHARS,
    MAX_BULLETS_PER_SLIDE,
    MAX_DECK_TITLE_CHARS,
    MAX_SLIDE_TITLE_CHARS,
    MAX_SUBTITLE_CHARS,
    MIN_SLIDES,
    MAX_SLIDES,
)
from pipeline.i18n import t as tr
from pipeline.validate.result import ValidationResult


@dataclass
class SlideSpec:
    index: int
    layout: str
    title: str
    subtitle: str = ""
    bullets: list[str] = field(default_factory=list)
    notes: str = ""
    visual_type: str = ""
    visual_description: str = ""


@dataclass
class DeckSpec:
    deck_title: str
    slides: list[SlideSpec]
    design: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Compile-ready slide markdown with canonical markers."""
        lines: list[str] = []
        for slide in self.slides:
            lines.append(f"--- Slide {slide.index} ---")
            if slide.layout == "title" or slide.index == 1:
                lines.append(f"# {slide.title}")
                if slide.subtitle:
                    words = slide.subtitle.split()
                    short = " ".join(words[:14]) + ("…" if len(words) > 14 else "")
                    lines.append(short)
            else:
                lines.append(f"## {slide.title}")
                for bullet in slide.bullets:
                    lines.append(f"- {bullet}")
            if slide.visual_description and slide.visual_type not in ("", "none"):
                lines.append(f"[IMAGE: {slide.visual_description}]")
            if slide.layout in ("section", "quote"):
                lines.append(f"[LAYOUT: {slide.layout}]")
            if slide.notes:
                lines.append(f"[NOTES: {slide.notes}]")
            lines.append("")
        return "\n".join(lines).strip()


def _strip_fences(text: str) -> str:
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.split("\n")
        end = len(lines)
        if lines[-1].strip() == "```":
            end -= 1
        return "\n".join(lines[1:end]).strip()
    return body


def parse_deck_spec(text: str) -> tuple[DeckSpec | None, list[str]]:
    """Parse JSON/YAML-ish deck spec from worker output."""
    errors: list[str] = []
    raw = _strip_fences(text)
    if not raw:
        return None, ["deck spec is empty"]

    data: dict[str, Any] | None = None
    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid JSON deck spec: {exc}")
            return None, errors
    else:
        data = _parse_loose_deck_text(raw)

    if not data:
        errors.append("could not parse deck spec structure")
        return None, errors

    deck_title = str(data.get("deck_title") or data.get("title") or "").strip()
    slides_raw = data.get("slides") or []
    if not deck_title:
        errors.append("deck_title is required")
    if not isinstance(slides_raw, list) or not slides_raw:
        errors.append("slides array is required")

    design = data.get("design") if isinstance(data.get("design"), dict) else {}
    if design is None:
        design = {}

    slides: list[SlideSpec] = []
    for i, item in enumerate(slides_raw):
        if not isinstance(item, dict):
            errors.append(f"slide {i + 1} must be an object")
            continue
        # Trust array position, not the model's self-reported index — some models emit
        # 0-based indices, and a naive `item.get("index") or i + 1` silently accepts a
        # wrong-but-truthy value (e.g. slide 2 reporting index 1), corrupting slide order.
        idx = i + 1
        layout = str(item.get("layout") or ("title" if idx == 1 else "content")).strip().lower()
        title = str(item.get("title") or "").strip()
        subtitle = str(item.get("subtitle") or "").strip()
        bullets_raw = item.get("bullets") or []
        bullets = [str(b).strip() for b in bullets_raw if str(b).strip()] if isinstance(bullets_raw, list) else []

        if layout not in ALLOWED_LAYOUTS:
            layout = "title" if idx == 1 else "content"
        if idx == 1:
            layout = "title"
            bullets = []

        visual = item.get("visual") if isinstance(item.get("visual"), dict) else {}
        visual_type = str(visual.get("type") or "").strip().lower()
        visual_description = str(visual.get("description") or "").strip()
        if visual_type == "none":
            visual_description = ""

        slides.append(
            SlideSpec(
                index=idx,
                layout=layout,
                title=title,
                subtitle=subtitle,
                bullets=bullets,
                notes=str(item.get("notes") or "").strip(),
                visual_type=visual_type,
                visual_description=visual_description,
            )
        )

    return DeckSpec(deck_title=deck_title, slides=slides, design=design), errors


def _parse_loose_deck_text(raw: str) -> dict[str, Any] | None:
    """Fallback: deck_title: line plus Slide N blocks."""
    lines = [ln.rstrip() for ln in raw.splitlines()]
    deck_title = ""
    slides: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for line in lines:
        if not line.strip():
            continue
        if re.match(r"^deck_title\s*:", line, re.I):
            deck_title = line.split(":", 1)[1].strip()
            continue
        m = re.match(r"^slide\s*(\d+)\s*:", line, re.I)
        if m:
            if current:
                slides.append(current)
            current = {"index": int(m.group(1)), "title": "", "bullets": []}
            continue
        if current is None:
            if not deck_title and line.startswith("#"):
                deck_title = line.lstrip("#").strip()
            continue
        if re.match(r"^title\s*:", line, re.I):
            current["title"] = line.split(":", 1)[1].strip()
        elif re.match(r"^layout\s*:", line, re.I):
            current["layout"] = line.split(":", 1)[1].strip().lower()
        elif re.match(r"^subtitle\s*:", line, re.I):
            current["subtitle"] = line.split(":", 1)[1].strip()
        elif line.startswith("- "):
            current.setdefault("bullets", []).append(line[2:].strip())
        elif line.startswith("## "):
            current["title"] = line[3:].strip()

    if current:
        slides.append(current)
    if not deck_title and not slides:
        return None
    return {"deck_title": deck_title, "slides": slides, "design": {}}


def validate_deck_spec(
    spec: DeckSpec,
    *,
    slide_count: int | None = None,
) -> ValidationResult:
    errors: list[str] = []
    if not spec.deck_title:
        errors.append("deck_title is required")
    elif len(spec.deck_title) > MAX_DECK_TITLE_CHARS:
        errors.append(f"deck_title exceeds {MAX_DECK_TITLE_CHARS} characters")

    n = len(spec.slides)
    if n < MIN_SLIDES:
        errors.append("deck must have at least one slide")
    if n > MAX_SLIDES:
        errors.append(f"deck exceeds maximum {MAX_SLIDES} slides")
    if slide_count is not None and slide_count > 0 and n != slide_count:
        errors.append(f"expected {slide_count} slides, found {n}")

    if not spec.slides or spec.slides[0].index != 1:
        errors.append("first slide must be index 1")
    elif spec.slides[0].layout != "title":
        errors.append("slide 1 layout must be title")
    elif spec.slides[0].bullets:
        errors.append("slide 1 must not have bullets (use subtitle only)")

    if n >= 4:
        agenda = spec.slides[1]
        if agenda.index != 2:
            errors.append("slide 2 must be index 2 (agenda)")
        elif len(agenda.bullets) < 2:
            errors.append("slide 2 (agenda) must have at least 2 bullets")
        closing = spec.slides[-1]
        if closing.layout not in ("closing", "content", "section"):
            errors.append("final slide layout should be closing")
        elif len(closing.bullets) < 2:
            errors.append("final slide (conclusion) must have at least 2 bullets")
        for slide in spec.slides[2:-1]:
            if not slide.bullets:
                errors.append(f"slide {slide.index} (content) must have bullets")

    for slide in spec.slides:
        if not slide.title:
            errors.append(f"slide {slide.index} missing title")
        elif len(slide.title) > MAX_SLIDE_TITLE_CHARS:
            errors.append(f"slide {slide.index} title too long")
        if len(slide.subtitle) > MAX_SUBTITLE_CHARS:
            errors.append(f"slide {slide.index} subtitle too long")
        if slide.index == 1 and len(slide.bullets) > 0:
            errors.append("slide 1 must not include bullets")
        if slide.index != 1 and len(slide.bullets) > MAX_BULLETS_PER_SLIDE:
            errors.append(f"slide {slide.index} has too many bullets (max {MAX_BULLETS_PER_SLIDE})")
        for b in slide.bullets:
            if len(b) > MAX_BULLET_CHARS:
                errors.append(f"slide {slide.index} bullet exceeds {MAX_BULLET_CHARS} chars")

    return ValidationResult(valid=len(errors) == 0, errors=errors)


_PLACEHOLDER_TITLE = re.compile(
    r"^(?:slide\s*)?\d*\s*[:\-\.]?\s*"
    r"(title|slide\s*\d*|agenda|outline|title\s*slide|presentation\s*title|"
    r"main\s*title|deck\s*title|presentation|untitled|"
    r"section\s+\d+|key\s*topic\s+\d+)\s*$",
    re.IGNORECASE,
)


def _is_placeholder_title(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    from pipeline.deck_i18n import is_bare_agenda_title, is_filler_section_title

    if is_bare_agenda_title(t) or is_filler_section_title(t):
        return True
    if _PLACEHOLDER_TITLE.match(t):
        return True
    if re.match(r"^slide\s+\d+\s*[:\-\.]", t, re.I):
        return True
    if re.fullmatch(r"slide\s+\d+", t, re.I):
        return True
    return False


_PLACEHOLDER_BULLET = re.compile(
    r"^(?:bullet\s*(?:point)?|key\s*point|point|content|text|item|detail)s?\s*\d*\s*$"
    r"|^(?:tbd|n/?a|todo|placeholder|lorem\s+ipsum|coming\s+soon)\s*$"
    r"|^(?:add|insert)\s+(?:your\s+)?(?:text|content|bullets?)\s+here\s*$",
    re.IGNORECASE,
)


def _is_placeholder_bullet(text: str) -> bool:
    """Catch generic filler a weak planner emits instead of real bullet content,
    e.g. 'Bullet point 1', 'Key point', 'TBD' — same idea as _is_placeholder_title."""
    t = (text or "").strip()
    if not t:
        return True
    return bool(_PLACEHOLDER_BULLET.match(t))


_PLACEHOLDER_VISUAL = re.compile(
    r"^(?:tbd|n/?a|todo|placeholder|none|no\s+image(?:\s+needed)?|not\s+applicable)\s*$"
    r"|^(?:a|an|the)?\s*(?:relevant|appropriate|suitable|generic)\s+image\s*$"
    r"|^image\s*(?:description|here|goes\s+here)?\s*$"
    r"|^visual\s*(?:description)?\s*$"
    r"|^insert\s+.*\s+(?:here|image)\s*$",
    re.IGNORECASE,
)


def _is_placeholder_visual(text: str) -> bool:
    """Catch generic filler in a slide's visual description, e.g. 'Image description',
    'A relevant image', 'TBD' — an empty description is normal and not a placeholder."""
    t = (text or "").strip()
    if not t:
        return False
    return bool(_PLACEHOLDER_VISUAL.match(t))


def _fallback_notes(title: str, bullets: list[str]) -> str:
    """Deterministic speaker note when the planner LLM dropped the `notes` field —
    guarantees every non-title slide has talking points instead of a silent blank."""
    t = (title or tr("deck.this_section")).strip()
    if bullets:
        highlight = "; ".join(b.rstrip(".") for b in bullets[:2])
        return tr("deck.notes_walk", title=t, highlight=highlight)
    return tr("deck.notes_intro", title=t)


def repair_deck_spec(
    spec: DeckSpec,
    *,
    query: str,
    target_slides: int = 8,
) -> DeckSpec:
    """Fill weak planner output: placeholder titles, thin agenda, too few slides."""
    from pipeline.deliverables.presentation_finalize import (
        _infer_deck_title,
        _infer_subtitle,
        _minimal_deck,
    )

    target = max(MIN_SLIDES, min(MAX_SLIDES, int(target_slides or 8), 12))
    if not spec.slides:
        return _minimal_deck(query, target)

    deck_title = (spec.deck_title or "").strip()
    if not deck_title or _is_placeholder_title(deck_title):
        deck_title = _infer_deck_title(query)

    slides: list[SlideSpec] = []
    for slide in spec.slides:
        title = (slide.title or "").strip()
        if _is_placeholder_title(title):
            if slide.index == 1:
                title = deck_title
            elif slide.index == 2:
                title = tr("deck.agenda")
            else:
                title = tr("deck.key_topic", n=slide.index - 2)
        bullets = [
            b for b in (slide.bullets or []) if str(b).strip() and not _is_placeholder_bullet(b)
        ]
        visual_type = slide.visual_type
        visual_description = slide.visual_description
        if _is_placeholder_visual(visual_description):
            visual_type, visual_description = "none", ""
        if slide.index == 1:
            slides.append(
                SlideSpec(
                    index=1,
                    layout="title",
                    title=title,
                    subtitle=(slide.subtitle or _infer_subtitle(query)).strip(),
                    bullets=[],
                    notes=slide.notes,
                    visual_type=visual_type,
                    visual_description=visual_description,
                )
            )
        else:
            slides.append(
                SlideSpec(
                    index=slide.index,
                    layout=slide.layout if slide.layout in ALLOWED_LAYOUTS else "content",
                    title=title,
                    subtitle=slide.subtitle,
                    bullets=bullets,
                    notes=slide.notes,
                    visual_type=visual_type,
                    visual_description=visual_description,
                )
            )

    if len(slides) < 2:
        slides.append(SlideSpec(index=2, layout="content", title=tr("deck.agenda"), bullets=[]))

    agenda = slides[1]
    agenda_bullets = list(agenda.bullets)
    if len(agenda_bullets) < 3:
        for sl in slides[2:]:
            if sl.title and not _is_placeholder_title(sl.title):
                agenda_bullets.append(sl.title)
    while len(agenda_bullets) < 3:
        agenda_bullets.append(tr("deck.section", n=len(agenda_bullets) + 1))
    slides[1] = SlideSpec(
        index=2,
        layout="content",
        title=agenda.title if not _is_placeholder_title(agenda.title) else tr("deck.agenda"),
        subtitle=agenda.subtitle,
        bullets=agenda_bullets[:MAX_BULLETS_PER_SLIDE],
        notes=agenda.notes,
        visual_type=agenda.visual_type,
        visual_description=agenda.visual_description,
    )

    # Drop LLM/repair filler slides (Section N, generic closing stubs).
    slides = [slides[0], slides[1]] + [
        s for s in slides[2:]
        if not _is_junk_slide_spec(s)
    ]

    from pipeline.deck_i18n import is_closing_title

    has_closing = any(
        re.search(r"summary|next\s*steps|takeaway|conclusion", (s.title or ""), re.I) or is_closing_title(s.title or "")
        for s in slides[2:]
    )
    if not has_closing:
        slides.append(
            SlideSpec(
                index=len(slides) + 1,
                layout="closing",
                title=tr("deck.summary_next_steps"),
                bullets=[tr("deck.revisit_themes"), tr("deck.choose_action")],
            )
        )

    for i, slide in enumerate(slides[:target], start=1):
        bullets = list(slide.bullets)
        if i > 2 and i < len(slides) and len(bullets) < 2:
            bullets.extend(
                [
                    tr("deck.practical_tip", n=len(bullets) + 1, topic=slide.title.lower()),
                    tr("deck.discussion_prompt", topic=slide.title.lower()),
                ]
            )
        notes = slide.notes.strip() if slide.notes else ""
        if not notes and i != 1:
            notes = _fallback_notes(slide.title, bullets)
        slides[i - 1] = SlideSpec(
            index=i,
            layout="title" if i == 1 else slide.layout,
            title=slide.title,
            subtitle=slide.subtitle,
            bullets=bullets[:MAX_BULLETS_PER_SLIDE],
            notes=notes,
            visual_type=slide.visual_type,
            visual_description=slide.visual_description,
        )

    return DeckSpec(deck_title=deck_title, slides=slides[:target], design=spec.design or {})


_GENERIC_FILLER_BULLETS = {
    "review the main ideas from this section",
    "apply one takeaway in your classroom or home",
    "review the main ideas from this presentation",
    "apply one takeaway this week",
}


def _is_junk_slide_spec(slide: SlideSpec) -> bool:
    """True for Section-N stubs and slides whose only bullets are generic filler."""
    title = (slide.title or "").strip()
    if re.fullmatch(r"section\s+\d+", title, re.I):
        return True
    if re.fullmatch(r"key\s*topic\s+\d+", title, re.I):
        return True
    bullets = [str(b).strip() for b in (slide.bullets or []) if str(b).strip()]
    if not bullets:
        return False
    return all(
        re.sub(r"[.!?]+$", "", b.lower()) in _GENERIC_FILLER_BULLETS
        for b in bullets
    )


def deck_spec_from_completed(
    completed: dict[str, str],
    plan_steps: list,
) -> DeckSpec | None:
    """Load deck spec from outline-phase worker output."""
    for step in reversed(plan_steps or []):
        if (getattr(step, "phase", None) or "").lower() != "outline":
            continue
        sid = getattr(step, "step_id", None) or ""
        raw = completed.get(sid, "")
        if not raw:
            continue
        spec, errs = parse_deck_spec(raw)
        if spec and not errs:
            return spec
    for step in reversed(plan_steps or []):
        if (getattr(step, "deliverable_contract", None) or "") == "presentation_deck_spec":
            sid = getattr(step, "step_id", None) or ""
            raw = completed.get(sid, "")
            if raw:
                spec, _ = parse_deck_spec(raw)
                if spec:
                    return spec
    return None
