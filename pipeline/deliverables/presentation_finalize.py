# -*- coding: utf-8 -*-
"""
Agentic presentation delivery: never ship research prose as the compile source.
Builds slide markdown from deck spec, slide-theme outlines, or safe minimal deck.
"""
from __future__ import annotations

import json
import re

from pipeline.deliverables.presentation_deck import (
    DeckSpec,
    SlideSpec,
    deck_spec_from_completed,
    parse_deck_spec,
    validate_deck_spec,
)
from pipeline.deliverables.presentation_limits import MAX_BULLET_CHARS, MAX_BULLETS_PER_SLIDE
from pipeline.deliverables.presentation_theme import resolve_theme
from pipeline.deliverables.specs import infer_slide_count
from pipeline.schemas.task_schema import AgenticPlan

_SLIDE_MARKER = re.compile(r"^---\s*Slide\s+\d+\s*---\s*$", re.MULTILINE | re.IGNORECASE)
_THEME_LINE = re.compile(
    r"^\s*(?:\d+[\).\]:]?\s*)?(.+?)\s*(?:[–—\-:]|\s{2,})\s*(.+?)\s*$"
)
_SECTION_HEAD = re.compile(r"^([A-Z][\w\s/&]+):\s*$", re.MULTILINE)


def is_compile_ready_presentation(text: str) -> bool:
    body = (text or "").strip()
    if not body:
        return False
    if not _SLIDE_MARKER.search(body):
        return False
    blocks = _count_slide_blocks(body)
    return blocks >= 1


def _count_slide_blocks(text: str) -> int:
    from services.presentation_markdown import split_presentation_slides

    return len(split_presentation_slides(text))


def finalize_presentation_source(
    completed: dict[str, str],
    plan: AgenticPlan,
    raw: str,
    query: str,
) -> str:
    """
    Return compile-ready slide markdown. Ignores research dumps when markers absent.
    """
    slide_count = infer_slide_count(query) or 5
    raw = (raw or "").strip()

    if is_compile_ready_presentation(raw):
        return _strip_prose_preamble(raw)

    deck = deck_spec_from_completed(completed, plan.steps or [])
    if not deck:
        deck = _deck_from_any_completed_json(completed)

    if deck:
        v = validate_deck_spec(deck, slide_count=slide_count)
        if v.valid or deck.slides:
            return deck.to_markdown()

    corpus = _research_corpus(completed, plan, raw)
    themes = extract_slide_themes_from_prose(corpus)
    if themes:
        deck = _deck_from_themes(themes, query, slide_count, corpus)
        return deck.to_markdown()

    return _minimal_deck(query, slide_count).to_markdown()


def presentation_chat_summary(markdown: str, artifact_name: str = "") -> str:
    """Short chat message — not the full research dump."""
    from services.presentation_markdown import split_presentation_slides

    theme, body = _split_theme_comment(markdown)
    _ = theme
    blocks = split_presentation_slides(body)
    n = len(blocks)
    lines = [f"✅ **Presentation ready** — {n} slide{'s' if n != 1 else ''}."]
    if artifact_name:
        lines[0] += f" `{artifact_name}`."
    lines.append("")
    for i, block in enumerate(blocks[:10], start=1):
        title = _block_title(block) or f"Slide {i}"
        bullet_n = len([ln for ln in block.splitlines() if ln.strip().startswith("- ")])
        extra = f" ({bullet_n} bullets)" if bullet_n and i > 1 else ""
        if i == 1 and bullet_n <= 1:
            extra = " (title slide)"
        lines.append(f"- **Slide {i}:** {title}{extra}")
    return "\n".join(lines)


def extract_slide_themes_from_prose(text: str) -> list[tuple[str, str]]:
    """
    Parse lines like:
      What is Kindness? – Define kindness through...
    from a 'Presentation Structure' / 'Slide Themes' section.
    """
    body = text or ""
    section = _extract_structure_section(body)
    if not section:
        return []

    themes: list[tuple[str, str]] = []
    for line in section.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = _THEME_LINE.match(line)
        if m:
            title = m.group(1).strip().strip("*")
            desc = m.group(2).strip()
            if len(title) > 3:
                themes.append((title, desc))
            continue
        m2 = re.match(r"^\s*\d+[\).\]]\s*(.+)$", line)
        if m2:
            chunk = m2.group(1).strip()
            if " – " in chunk or " - " in chunk:
                parts = re.split(r"\s+[–—\-]\s+", chunk, maxsplit=1)
                themes.append((parts[0].strip(), parts[1].strip() if len(parts) > 1 else ""))
            else:
                themes.append((chunk, ""))
    return themes


def _extract_structure_section(text: str) -> str:
    lines = text.splitlines()
    capturing = False
    buf: list[str] = []
    for line in lines:
        lower = line.lower().strip()
        if not capturing and re.search(
            r"presentation structure|slide themes|slide outline|\d+\s*slide themes",
            lower,
        ):
            capturing = True
            continue
        if capturing:
            if re.match(r"^✅\s*task:", lower) or lower.startswith("task:"):
                break
            if lower.startswith("## deliverable") or lower == "---":
                break
            buf.append(line)
    return "\n".join(buf)


def _deck_from_themes(
    themes: list[tuple[str, str]],
    query: str,
    slide_count: int,
    corpus: str,
) -> DeckSpec:
    deck_title = _infer_deck_title(query)
    design = {"mood": "warm" if "kind" in query.lower() else "", "palette": ""}
    theme = resolve_theme(query=query, design=design)
    design = {"mood": theme.mood, "palette": theme.palette_id}

    slides: list[SlideSpec] = []
    subtitle = _infer_subtitle(query)
    content_themes: list[tuple[str, str]] = []

    if len(themes) >= slide_count:
        subtitle = _truncate(themes[0][1] or themes[0][0], 120) or subtitle
        content_themes = themes[1:slide_count]
    elif themes:
        subtitle = _truncate(themes[0][1] or "", 120) or subtitle
        content_themes = themes[1:] if len(themes) > 1 else themes

    while len(content_themes) < max(1, slide_count - 1):
        content_themes.append((f"Key idea {len(content_themes) + 1}", "Supporting point for the topic."))

    slides.append(
        SlideSpec(index=1, layout="title", title=deck_title, subtitle=subtitle, bullets=[])
    )
    for i, (title, desc) in enumerate(content_themes[: slide_count - 1], start=2):
        bullets = _bullets_for_slide(title, desc, corpus)
        slides.append(SlideSpec(index=i, layout="content", title=title, bullets=bullets))

    return DeckSpec(deck_title=deck_title, slides=slides, design=design)


def _bullets_for_slide(title: str, desc: str, corpus: str) -> list[str]:
    """Short bullets from theme description + matching research section."""
    bullets: list[str] = []
    if desc:
        bullets.extend(_description_to_bullets(desc))

    section = _find_section_for_title(corpus, title)
    if section:
        for line in section.splitlines():
            line = line.strip()
            if line.startswith(("- ", "* ", "• ")):
                bullets.append(_truncate(line.lstrip("-*• ").strip(), MAX_BULLET_CHARS))
            elif re.match(r"^\d+[\).\]]\s+", line):
                bullets.append(_truncate(re.sub(r"^\d+[\).\]]\s+", "", line), MAX_BULLET_CHARS))

    seen: set[str] = set()
    out: list[str] = []
    for b in bullets:
        key = b.lower()[:40]
        if key in seen or not b:
            continue
        seen.add(key)
        out.append(b)
        if len(out) >= MAX_BULLETS_PER_SLIDE:
            break

    if not out and desc:
        out = _description_to_bullets(desc)
    if not out:
        out = [_truncate(f"Explore {title.lower()} in daily life.", MAX_BULLET_CHARS)]
    return out[:MAX_BULLETS_PER_SLIDE]


def _description_to_bullets(desc: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", desc)
    bullets = [_truncate(p.strip(), MAX_BULLET_CHARS) for p in parts if len(p.strip()) > 12]
    if not bullets:
        bullets = [_truncate(desc, MAX_BULLET_CHARS)]
    return bullets[:MAX_BULLETS_PER_SLIDE]


def _find_section_for_title(corpus: str, title: str) -> str:
    key = title.lower()
    keywords = [w for w in re.findall(r"[a-z]{4,}", key) if w not in ("what", "the", "slide", "kindness")]
    lines = corpus.splitlines()
    start = -1
    for i, line in enumerate(lines):
        if _SECTION_HEAD.match(line.strip()):
            head = line.strip()[:-1].lower()
            if any(k in head for k in keywords) or key[:20] in head:
                start = i + 1
                break
    if start < 0:
        return ""
    buf: list[str] = []
    for line in lines[start:]:
        if _SECTION_HEAD.match(line.strip()) and buf:
            break
        buf.append(line)
    return "\n".join(buf)


def _minimal_deck(query: str, slide_count: int) -> DeckSpec:
    title = _infer_deck_title(query)
    slides = [
        SlideSpec(index=1, layout="title", title=title, subtitle=_infer_subtitle(query), bullets=[]),
    ]
    for i in range(2, slide_count + 1):
        slides.append(
            SlideSpec(
                index=i,
                layout="content",
                title=f"Section {i - 1}",
                bullets=[f"Add key point {j} for this topic." for j in range(1, 4)],
            )
        )
    return DeckSpec(deck_title=title, slides=slides, design={})


def _deck_from_any_completed_json(completed: dict[str, str]) -> DeckSpec | None:
    for raw in completed.values():
        if not raw or not str(raw).strip().startswith("{"):
            continue
        spec, errs = parse_deck_spec(raw)
        if spec and not errs:
            return spec
    return None


def _research_corpus(
    completed: dict[str, str],
    plan: AgenticPlan,
    raw: str,
) -> str:
    parts: list[str] = []
    for step in plan.steps or []:
        phase = (step.phase or "").lower()
        if phase in ("research", "outline", "draft", "synthesis", "extract"):
            sid = step.step_id or ""
            if sid in completed:
                parts.append(completed[sid])
    if raw:
        parts.append(raw)
    return "\n\n".join(parts)


def _infer_deck_title(query: str) -> str:
    q = (query or "").strip()
    m = re.search(
        r"(?:presentation|slides?)\s+(?:on|about)\s+(.+?)(?:\s*$|\s+with|\s+\d)",
        q,
        re.I,
    )
    if m:
        return _truncate(m.group(1).strip().title(), 70)
    m2 = re.search(r"on\s+(.+?)(?:\s*$|\s+\d)", q, re.I)
    if m2:
        return _truncate(m2.group(1).strip().title(), 70)
    return _truncate(q[:70] or "Presentation", 70)


def _infer_subtitle(query: str) -> str:
    if "beautiful" in (query or "").lower():
        return "A thoughtful visual journey"
    if "kindness" in (query or "").lower():
        return "Compassion in everyday life"
    return ""


def _strip_prose_preamble(text: str) -> str:
    if _SLIDE_MARKER.search(text):
        idx = _SLIDE_MARKER.search(text).start()
        prefix = text[:idx]
        if len(prefix) > 200 and "Merriam" in prefix or "study" in prefix.lower():
            return text[idx:].strip()
    return text.strip()


def _split_theme_comment(text: str) -> tuple[str, str]:
    from pipeline.deliverables.presentation_theme import extract_theme_from_text

    theme, body = extract_theme_from_text(text)
    return str(theme.palette_id if theme else ""), body


def _block_title(block: str) -> str:
    for line in block.splitlines():
        line = line.strip()
        if line.startswith("# "):
            return line[2:].strip()
        if line.startswith("## "):
            return line[3:].strip()
    return ""


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1]
    last_space = clipped.rfind(" ")
    if last_space > limit * 0.6:
        clipped = clipped[:last_space]
    return clipped.rstrip() + "…"
