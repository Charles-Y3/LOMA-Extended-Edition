# -*- coding: utf-8 -*-
"""Infer presentation metadata (tone, theme, visuals) from user prompts."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

_TONES = ("formal", "casual", "academic", "persuasive", "technical")
# Concept names (query_intent_i18n.CONCEPTS) for each tone/include-images signal — one
# shared, multilingual source instead of an English-only per-file tuple. See
# CLAUDE.md section 8.
_TONE_CONCEPTS: list[tuple[str, str]] = [
    ("tone_formal", "formal"),
    ("tone_casual", "casual"),
    ("tone_academic", "academic"),
    ("tone_persuasive", "persuasive"),
    ("tone_technical", "technical"),
]


@dataclass
class PresentationBrief:
    title: str = ""
    subtitle: str = ""
    topic: str = ""
    audience: str = ""
    purpose: str = ""
    tone: str = "formal"
    duration_minutes: int = 0
    number_of_slides: int = 0
    include_images: bool = True
    palette_id: str = "slate_modern"
    mood: str = "professional"
    story_arc: dict[str, str] = field(default_factory=dict)
    has_source_data: bool = False

    def to_system_block(self) -> str:
        arc = self.story_arc or {}
        lines = [
            "PRESENTATION BRIEF (follow for structure and style):",
            f"- tone: {self.tone}",
            f"- theme palette: {self.palette_id} (mood: {self.mood})",
            f"- include_images: {self.include_images}",
        ]
        if self.title:
            lines.append(f"- title: {self.title}")
        if self.subtitle:
            lines.append(f"- subtitle: {self.subtitle}")
        if self.audience:
            lines.append(f"- audience: {self.audience}")
        if self.purpose:
            lines.append(f"- purpose: {self.purpose}")
        if self.number_of_slides:
            lines.append(f"- target_slides: {self.number_of_slides}")
        if arc:
            lines.append("- story_arc:")
            for k, v in arc.items():
                if v:
                    lines.append(f"  - {k}: {v}")
        lines.append(
            "Deck rules: slide 1 = title only; slide 2 = Agenda with bullet list of sections; "
            "slides 3..N-1 = one topic per slide with 3–5 bullets; final slide = Conclusion with takeaways; "
            "max 5 bullets per slide; bullets are full sentences/phrases, never cut off mid-word; "
            "never use 'Slide N' or field labels as visible text; "
            "every slide (except the title slide) should carry speaker notes, and most content slides should "
            "carry a visual — see the notes/visual fields required by the contract."
        )
        if self.include_images:
            lines.append(
                "Visual layout (when images requested): title slide = full-bleed background or "
                "centered hero image; content slides = left-text-right-visual or split 50/50; "
                "data slides = chart/diagram dominant; concept slides = small supporting icon "
                "or photo — image must match slide message, never decorative-only. "
                "Never describe a photo/scene visual as containing a headline, caption, title text, sign "
                "text, or any other readable words 'overlaid' or 'reading'/'saying' something — the slide's "
                "title is already rendered as real text by the slide itself, and diffusion image models "
                "render requested text as garbled nonsense, not legible words. Describe only the visual "
                "scene content (subjects, setting, action, mood) — never words that should appear printed "
                "within the image, even for the title/hero slide."
            )
        if self.has_source_data:
            lines.append(
                "Data integrity: source documents were provided for this deck — any specific number, "
                "statistic, percentage, date, or figure you state (in slide text, speaker notes, or a "
                "chart/visual description) must come directly from that source material. Do not add or "
                "round to numbers the sources don't contain."
            )
        else:
            lines.append(
                "Data integrity: no source documents or datasets were provided for this deck — do not "
                "state any specific number, statistic, percentage, or figure as fact anywhere (slide "
                "text, speaker notes, or chart/visual descriptions), and do not request a chart, graph, "
                "or stat-callout visual. Discuss trends and impacts qualitatively only."
            )
        return "\n".join(lines)


def infer_slide_count_hint(query: str) -> int:
    q = (query or "").lower()
    for pattern in (
        r"\bnot\s+more\s+than\s+(\d{1,2})\s*slides?\b",
        r"\b(?:max|maximum|at\s+most)\s+(\d{1,2})\s*slides?\b",
        r"\b(\d{1,2})\s*slides?\s+(?:max|maximum|only)\b",
        r"\b(\d{1,2})\s*slides?\b",
        r"(\d{1,2})\s*張投影片",
        r"(\d{1,2})\s*张幻灯片",
        r"(\d{1,2})\s*diapositivas?",
        r"(\d{1,2})\s*folien",
    ):
        m = re.search(pattern, q)
        if m:
            return max(3, min(40, int(m.group(1))))
    return 0


def infer_tone(query: str) -> str:
    from pipeline.query_intent_i18n import matches

    lower = (query or "").lower()
    tones: list[str] = []
    for concept, tone in _TONE_CONCEPTS:
        if matches(lower, concept):
            tones.append(tone)
    if len(tones) > 1:
        return "+".join(dict.fromkeys(tones))
    if tones:
        return tones[0]
    return "formal"


def infer_include_images(query: str) -> bool:
    from pipeline.query_intent_i18n import matches

    lower = (query or "").lower()
    if matches(lower, "include_images_no"):
        return False
    if matches(lower, "include_images_yes"):
        return True
    return True


def build_presentation_brief(query: str, *, has_source_data: bool = False) -> PresentationBrief:
    q = (query or "").strip()
    tone = infer_tone(q)
    palette = infer_palette_from_query(q)
    theme = resolve_theme(query=q, design={"palette": palette, "tone": tone})
    slides = infer_slide_count_hint(q)
    title = ""
    if q:
        words = q.split()
        if len(words) <= 3:
            title = q[:1].upper() + q[1:] if q else ""
            if not title.lower().startswith(("create", "make", "build", "presentation")):
                title = f"The Power of {title}" if len(words) == 1 else q.title()
        else:
            title = re.split(r"[.!?\n]", q, maxsplit=1)[0].strip()[:80]
    brief = PresentationBrief(
        title=title or q[:80] or "Presentation",
        subtitle="",
        topic=title or q[:80],
        tone=tone,
        number_of_slides=slides,
        include_images=infer_include_images(q),
        palette_id=theme.palette_id,
        mood=theme.mood,
        story_arc={
            "opening_hook": "Hook the audience with the core topic.",
            "key_idea": title or "Main theme from the request.",
            "resolution_or_conclusion": "Clear takeaway and next steps.",
        },
        has_source_data=has_source_data,
    )
    return brief


def brief_for_capability_config(query: str) -> dict[str, Any]:
    b = build_presentation_brief(query)
    return {
        "presentation_brief": b,
        "presentation_tone": b.tone,
        "presentation_palette": b.palette_id,
        "presentation_include_images": b.include_images,
    }
