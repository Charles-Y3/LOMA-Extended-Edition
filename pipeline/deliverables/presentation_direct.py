# -*- coding: utf-8 -*-
"""Direct pipeline: prepare slide markdown and compile themed PPTX with visuals."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from pipeline.deliverables.presentation_brief import build_presentation_brief
from pipeline.deliverables.presentation_deck import parse_deck_spec
from pipeline.deliverables.presentation_prepare import prepare_agentic_presentation
from pipeline.deliverables.presentation_theme import PresentationTheme, resolve_theme


@dataclass
class DirectPresentationPrep:
    markdown: str
    theme: PresentationTheme | None = None
    include_images: bool = True
    slide_visuals: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _extract_json_block(text: str, key: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    for chunk in re.findall(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL):
        try:
            data = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and (key in data or data.get("slides")):
            return data
    if raw.lstrip().startswith("{"):
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _slide_visuals_from_plan(text: str) -> list[dict]:
    data = _extract_json_block(text, "slides")
    if not data:
        return []
    slides = data.get("slides") or []
    out: list[dict] = []
    for item in slides:
        if not isinstance(item, dict):
            continue
        visual = item.get("visual") if isinstance(item.get("visual"), dict) else {}
        out.append(
            {
                "slide_number": int(item.get("slide_number") or len(out) + 1),
                "layout_hint": str(item.get("layout_hint") or "left-text-right-visual").strip(),
                "visual_type": str(visual.get("type") or "none").strip().lower(),
                "visual_description": str(visual.get("description") or "").strip(),
                "title": str(item.get("title") or "").strip(),
            }
        )
    return out


def prepare_direct_presentation(
    markdown: str,
    *,
    query: str = "",
    working_notes: str = "",
) -> DirectPresentationPrep:
    """Normalize markdown, resolve theme, collect per-slide visual hints."""
    brief = build_presentation_brief(query)
    prep = prepare_agentic_presentation((markdown or "").strip(), workflow_instruction=query)
    theme = prep.theme or resolve_theme(
        query=query, design={"palette": brief.palette_id, "tone": brief.tone}
    )
    include_images = brief.include_images

    slide_visuals = _slide_visuals_from_plan(working_notes or "")
    if not slide_visuals:
        spec, _ = parse_deck_spec(working_notes or "")
        if spec:
            for s in spec.slides:
                slide_visuals.append(
                    {
                        "slide_number": s.index,
                        "layout_hint": "content",
                        "visual_type": "none",
                        "visual_description": "",
                        "title": s.title,
                    }
                )

    body = prep.markdown if prep.ok else (markdown or "").strip()
    if body.strip().lstrip().startswith("{") and not _has_canonical_slide_markers(body):
        spec, _ = parse_deck_spec(body)
        if spec:
            body = spec.to_markdown()
            prep = prepare_agentic_presentation(body, workflow_instruction=query)
            if prep.ok:
                body = prep.markdown
    if body.strip():
        from services.presentation_markdown import (
            _has_canonical_slide_markers,
            extract_compile_markdown,
            is_well_structured_deck,
            normalize_presentation_markdown,
            pad_title_only_slides,
            pad_title_only_slides_blocks,
            split_presentation_slides,
            _slides_to_canonical_markdown,
        )

        norm = extract_compile_markdown(body)
        slides = split_presentation_slides(norm)
        if is_well_structured_deck(slides):
            body = _slides_to_canonical_markdown(
                pad_title_only_slides_blocks(slides, query=query)
            )
        else:
            from services.presentation_markdown import ensure_deck_structure

            body = ensure_deck_structure(
                pad_title_only_slides(norm, query=query),
                deck_title=query[:80],
            )
    if not body.strip():
        from pipeline.deliverables.presentation_finalize import _minimal_deck

        body = _minimal_deck(query, brief.number_of_slides or 8).to_markdown()

    errors = list(prep.errors or [])
    return DirectPresentationPrep(
        markdown=body.strip(),
        theme=theme,
        include_images=include_images,
        slide_visuals=slide_visuals,
        errors=errors,
    )
