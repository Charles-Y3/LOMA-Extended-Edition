# -*- coding: utf-8 -*-
"""Agentic-only PowerPoint compile with topic-aware theme (not used by Direct path)."""
from __future__ import annotations

import os
import re

from pipeline.deliverables.presentation_theme import PresentationTheme, extract_theme_from_text
from pipeline.output_format import EXTENSION_BY_TYPE

GENERATED_DIR = os.path.join("data", "generated")

# Catches a specific quantitative claim (a casualty count, an acreage, a magnitude,
# a percentage) — NOT a bare year, which the deck's own "past decade" framing needs
# to state regardless of grounding and is comparatively low-risk. Three shapes:
# comma-grouped counts (23,000), decimals (9.0, 4.5), and a number immediately next
# to a scale/unit word (million/percent/acres/etc.) in either order.
_UNVERIFIED_NUMBER_RE = re.compile(
    r"\d{1,3}(?:,\d{3})+"            # 23,000
    r"|\d+\.\d+"                     # 9.0 / 4.5
    r"|\d+\s*%"                      # 23%
    r"|\b\d+\s*(?:million|billion|thousand|percent|acres?|degrees?|tons?|"
    r"kilometers?|km|miles?|magnitude)\b"  # 16 million / magnitude 9
    r"|\b(?:million|billion|thousand)\s+\d+\b",
    re.IGNORECASE,
)


def _has_unverified_numeric_claim(text: str) -> bool:
    return bool(_UNVERIFIED_NUMBER_RE.search(text or ""))


def _strip_unverified_numeric_sentences(text: str) -> str:
    """Drop only the sentence(s) carrying a numeric claim, keep the rest — speaker
    notes are a paragraph, not a single bullet, so whole-block removal would lose
    good context along with the bad number."""
    text = (text or "").strip()
    if not text or not _has_unverified_numeric_claim(text):
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    kept = [s for s in sentences if not _has_unverified_numeric_claim(s)]
    return " ".join(kept).strip()


def _unique_generated_path(path: str) -> str:
    """Numbered (_2, _3, ...) fallback when `path` already exists, so
    re-running the same/similar request produces its own file instead of
    silently overwriting a previous deck — same convention as
    services/image_generation.py::unique_output_path."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    n = 2
    while os.path.exists(f"{root}_{n}{ext}"):
        n += 1
    return f"{root}_{n}{ext}"

# The direct-pipeline slide author (pipeline/direct/task_roles.py's
# slide_author role) is told to write "[IMAGE: desc]" markers — confirmed by
# pipeline/deliverables/presentation_deck.py and presentation_reattach.py,
# which both emit/parse the same "[IMAGE: ...]" shape. Also accept
# "[IMAGE_PROMPT: ...]" (the shape services/artifact_build.py's
# IMAGE_MARKER_RE uses for docx/document markers) in case markdown from that
# other authoring path ever reaches this compiler — cheap to support both,
# and the real bug turned out to live earlier in the pipeline anyway (see
# pipeline/deliverables/presentation_prepare.py::_parse_slide_block, which
# was silently dropping bullets/image/notes lines before they got here).
_SLIDE_IMAGE_MARKER_RE = re.compile(
    r"\[IMAGE(?:_PROMPT)?:\s*(.+?)\]|\[IMAGE_PROMPT\]:?\s*(.+)$",
    re.IGNORECASE,
)

_WANTS_NOTES_RE_EN = re.compile(
    r"speaker\s*notes?|presenter\s*notes?|notes?\s+for\s+(?:each|every)\s+slide|"
    r"include\b.{0,20}\bnotes?\b",
    re.IGNORECASE,
)


class _WantsNotesRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        return _WANTS_NOTES_RE_EN.search(text or "") or (
            matches(text or "", "speaker_notes_request") or None
        )


_WANTS_NOTES_RE = _WantsNotesRE()


def compile_agentic_presentation(
    markdown: str,
    original_filename: str,
    theme: PresentationTheme | None = None,
    *,
    query: str = "",
    include_images: bool = False,
    slide_visuals: list[dict] | None = None,
    log_fn=None,
    prof: dict | None = None,
    model: str | None = None,
    settings: dict | None = None,
    presentation_style: str = "",
    presentation_grounded: bool = False,
) -> str | None:
    """Build .pptx with modern styling; returns output path."""
    try:
        from pptx import Presentation
        from pptx.enum.text import MSO_AUTO_SIZE
        from pptx.util import Inches, Pt
    except ImportError:
        return None

    os.makedirs(GENERATED_DIR, exist_ok=True)
    resolved_theme, body = extract_theme_from_text(markdown, query=query)
    theme = theme or resolved_theme
    if presentation_style == "minimal" and theme.use_accent_bar:
        # Structural backstop matching PRESENTATION_STYLE_PROMPT_BIAS["minimal"]'s
        # "never decorative" guidance — minimal stays the one style with no accent bar,
        # regardless of what the planner/theme design block requested.
        from dataclasses import replace

        theme = replace(theme, use_accent_bar=False)

    from services.presentation_markdown import (
        ensure_deck_structure,
        is_well_structured_deck,
        normalize_presentation_markdown,
        parse_speaker_notes_line,
        partition_slide_extras,
        sanitize_bullet_text,
        sanitize_slide_title,
        scrub_junk_slides,
        split_presentation_slides,
    )

    cleaned = normalize_presentation_markdown(body)
    slides = split_presentation_slides(cleaned)
    if is_well_structured_deck(slides):
        normalized = cleaned
    else:
        normalized = ensure_deck_structure(
            cleaned,
            deck_title=(query or original_filename or "")[:80],
        )
    slides_content = scrub_junk_slides(split_presentation_slides(normalized))
    if not slides_content and normalized.strip():
        slides_content = scrub_junk_slides([normalized.strip()])
    if len(slides_content) < len(re.findall(r"^---\s*Slide\s+\d+\s*---", normalized, re.I | re.M)):
        slides_content = scrub_junk_slides(_split_by_slide_markers(normalized))

    base_name = original_filename or "output"
    if "." in base_name:
        base_name = os.path.splitext(base_name)[0]
    for suffix in ("_en", "_zh", "_loma", "_LOMA", "_loma", "_LOMA"):
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)]
    ext = EXTENSION_BY_TYPE.get("presentation", ".pptx")
    filepath = _unique_generated_path(os.path.join(GENERATED_DIR, f"{base_name}_LOMA{ext}"))

    prs = Presentation()
    title_layout = prs.slide_layouts[0]
    content_layout = prs.slide_layouts[1]
    visuals = slide_visuals or []
    wants_notes = bool(_WANTS_NOTES_RE.search(query or ""))
    notes_repaired = 0

    if include_images:
        from services.model_router import any_image_model_installed

        if not any_image_model_installed():
            # Checked once here rather than per-slide in _resolve_slide_image — avoids a
            # doomed generation attempt for every single slide when nothing can produce
            # an image anyway.
            include_images = False
            if log_fn:
                log_fn(
                    "No image-generation model is downloaded — slides will skip images. "
                    "Download one from Settings → Model Library."
                )

    # NOTE: deliberately NOT wrapped in one outer ResourceGovernor.acquire("image_gen")
    # for the whole loop — a diagram/infographic slide's resolve_marker_visual() call
    # makes its own LLM call (see services/marker_visual.py) on this same thread, and an
    # outer media-exclusive hold would block that LLM call for up to 180s waiting for
    # media to go idle (which it never does mid-batch), then let it run while the image
    # pipeline is still VRAM-resident anyway — a real stall + VRAM-contention regression
    # this was tried and reverted. Each generate_image() call acquires/releases its own
    # image_gen resource; back-to-back image-only slides still reuse the still-loaded
    # pipeline via ResourceGovernor's grace-window keepalive (GOVERNOR_MEDIA_KEEPALIVE_SECONDS),
    # which correctly evicts immediately the moment an LLM task (like the diagram
    # authoring call) actually needs the GPU.
    for slide_idx, slide_data in enumerate(slides_content):
        lines = [line.strip() for line in slide_data.split("\n") if line.strip()]
        if not lines:
            continue

        title_text = ""
        subtitle_text = ""
        bullet_points: list[str] = []
        image_desc = ""
        speaker_notes = ""
        slide_layout_kind = ""

        for line in lines:
            if re.match(r"^---\s*Slide\s+\d+\s*---\s*$", line, re.IGNORECASE):
                continue
            img_m = _SLIDE_IMAGE_MARKER_RE.fullmatch(line)
            if img_m:
                image_desc = (img_m.group(1) or img_m.group(2) or "").strip()
                continue
            layout_m = re.match(r"^\[LAYOUT:\s*(.+?)\]\s*$", line, re.I)
            if layout_m:
                slide_layout_kind = layout_m.group(1).strip().lower()
                continue
            notes_line = parse_speaker_notes_line(line)
            if notes_line:
                speaker_notes = (
                    f"{speaker_notes}\n\n{notes_line}".strip()
                    if speaker_notes
                    else notes_line
                )
                continue
            if line.startswith("# ") and not title_text:
                title_text = sanitize_slide_title(re.sub(r"^#+\s+", "", line))
                continue
            if line.startswith("## "):
                title_text = sanitize_slide_title(re.sub(r"^#+\s+", "", line))
                continue
            bullet_m = re.match(r"^\s*[\*\-]\s+(.+)$", line)
            if bullet_m:
                bullet = sanitize_bullet_text(bullet_m.group(1))
                img_inline = _SLIDE_IMAGE_MARKER_RE.search(bullet)
                if img_inline and not image_desc:
                    image_desc = (img_inline.group(1) or img_inline.group(2) or "").strip()
                    bullet = _SLIDE_IMAGE_MARKER_RE.sub("", bullet).strip()
                if bullet:
                    bullet_points.append(bullet)
                continue
            num_m = re.match(r"^\d+[\).\]]\s+(.+)$", line)
            if num_m:
                bullet = sanitize_bullet_text(num_m.group(1))
                if bullet:
                    bullet_points.append(bullet)
                continue
            elif slide_idx == 0 and title_text and not subtitle_text and not line.startswith("#"):
                subtitle_text = sanitize_bullet_text(line)
            elif not title_text and not line.startswith("#"):
                title_text = sanitize_slide_title(line)
            elif title_text and not line.startswith("#"):
                bullet = sanitize_bullet_text(line)
                img_inline = _SLIDE_IMAGE_MARKER_RE.search(bullet)
                if img_inline and not image_desc:
                    image_desc = (img_inline.group(1) or img_inline.group(2) or "").strip()
                    bullet = _SLIDE_IMAGE_MARKER_RE.sub("", bullet).strip()
                if bullet:
                    bullet_points.append(bullet)

        bullet_points, extra_image, extra_notes = partition_slide_extras(bullet_points)
        if extra_image and not image_desc:
            image_desc = extra_image
        if extra_notes:
            speaker_notes = (
                f"{speaker_notes}\n\n{extra_notes}".strip()
                if speaker_notes
                else extra_notes
            )

        if not presentation_grounded:
            # Mechanical backstop, not just a prompt instruction — the deck_planner/
            # slide_author system prompt already forbids stating numbers without
            # grounding (see PresentationBrief.to_system_block()'s has_source_data
            # rule), but a small local model doesn't reliably obey that instruction
            # (confirmed: a real run stated "23,000 deaths"/"16 million acres" for an
            # ungrounded 2011 tsunami/2018 wildfire slide, both fabricated). Drop any
            # bullet/notes sentence carrying a specific numeric claim instead of
            # trusting compliance.
            bullet_points = [b for b in bullet_points if not _has_unverified_numeric_claim(b)]
            subtitle_text = "" if _has_unverified_numeric_claim(subtitle_text) else subtitle_text
            speaker_notes = _strip_unverified_numeric_sentences(speaker_notes)

        visual = _visual_for_slide(slide_idx + 1, visuals)
        if not image_desc:
            image_desc = str(visual.get("visual_description") or "").strip()
        layout_hint = str(visual.get("layout_hint") or "").strip().lower()
        visual_type = str(visual.get("visual_type") or "none").strip().lower()
        wants_image = include_images and (
            bool(image_desc) or visual_type in ("image", "diagram", "chart", "photo")
        )

        # Structural backstop for the 3 deck styles (see presentation_theme.PRESENTATION_
        # STYLES / step_executor.PRESENTATION_STYLE_PROMPT_BIAS) — those only *ask* the
        # planner LLM to pick different layout_hints per style; this guarantees the
        # visible difference even when the planner ignores that instruction.
        if wants_image and image_desc:
            if presentation_style == "bold_editorial" and slide_idx == 0:
                layout_hint = "background"
            elif presentation_style == "data_heavy" and layout_hint in (
                "background", "full-bleed", "hero",
            ):
                layout_hint = "right"
            elif presentation_style == "minimal" and layout_hint in (
                "background", "full-bleed", "hero",
            ):
                layout_hint = "small-image"

        is_section_slide = slide_layout_kind == "section"
        is_quote_slide = slide_layout_kind == "quote"
        use_title_layout = (
            (slide_idx == 0 and bool(title_text) and len(bullet_points) <= 1)
            or is_section_slide
            or is_quote_slide
        )
        if is_quote_slide:
            # The quote/stat itself is the big text (goes in the title placeholder's
            # large font); the slide's own title (if any) becomes a small attribution
            # line underneath, mirroring how a pull-quote reads.
            quote_text = bullet_points[0] if bullet_points else title_text
            attribution = title_text if bullet_points else ""
            title_text = quote_text
            subtitle_text = attribution
            bullet_points = []
        elif use_title_layout:
            if len(bullet_points) == 1:
                subtitle_text = bullet_points[0]
                bullet_points = []
            elif bullet_points:
                joined = " ".join(bullet_points)
                if len(joined) > 120:
                    clipped = joined[:119]
                    last_space = clipped.rfind(" ")
                    if last_space > 72:
                        clipped = clipped[:last_space]
                    joined = clipped.rstrip() + "…"
                subtitle_text = joined
                bullet_points = []

        layout = title_layout if use_title_layout else content_layout
        slide = prs.slides.add_slide(layout)
        _apply_slide_transition(slide, theme)
        if is_section_slide:
            # A section-divider slide is a deliberate visual break between deck
            # sections — a solid accent-colored background (instead of the normal
            # light background) reads as a distinct "chapter marker" at a glance.
            _apply_accent_background(slide, theme)
        else:
            _apply_slide_background(slide, theme)

        if not use_title_layout and theme.use_accent_bar:
            _add_accent_bar(slide, theme, Inches)

        from services.markdown_inline import set_pptx_paragraph_inline

        text_left = layout_hint in (
            "left-text-right-visual",
            "split",
            "content-left",
            "text-left",
        ) or (wants_image and layout_hint not in ("background", "full-bleed", "hero"))
        # NOT `image_desc or title_text` — a slide title (e.g. "Exercise Keeps One
        # Happy") is a short declarative phrase, not a scene description; handed to a
        # diffusion model as the entire prompt, it has nothing else to draw and
        # defaults to rendering those words as image text, which every diffusion model
        # (even FLUX) renders as garbled nonsense at this length. Skipping the image
        # when there's no real description is a better outcome than a garbled one.
        img_path = (
            _resolve_slide_image(
                image_desc, slide_idx + 1, query, prof=prof, model=model, log_fn=log_fn,
                # Passing the deck's own generated markdown as "source_text" here used
                # to always win as trusted grounding for a chart/infographic marker —
                # pipeline.base.grounding._source_excerpt() treats ANY non-empty
                # source_text as real material and returns it immediately, before ever
                # attempting a web search. For an ungrounded deck that markdown is just
                # the model's own unverified prose (containing no real numbers — the
                # numeric-claim scrub above strips those), so a chart marker was being
                # "grounded" in text that had nothing real to ground on, defeating both
                # the web-search fallback AND chart_generation.py's own refusal check
                # (which only tests "is context non-empty", not "is it real data") —
                # confirmed via a real run that still produced 3 charts with invented
                # numbers despite that refusal. Only pass it when the deck itself is
                # actually grounded (attached source or a successful web search).
                source_text=markdown if presentation_grounded else "",
                settings=settings,
            )
            if wants_image and image_desc
            else None
        )
        use_side_image = bool(
            img_path
            and not (
                use_title_layout
                and layout_hint in ("background", "full-bleed", "hero", "title")
            )
        )
        # Side image → reserve a real left text column (width+top+height). Setting
        # only width used to collapse placeholder height to 0 in python-pptx.
        text_col = bool(text_left and use_side_image)

        if use_title_layout and img_path and layout_hint in ("background", "full-bleed", "hero", "title"):
            _add_background_image(slide, img_path, Inches)

        if slide.shapes.title:
            p = slide.shapes.title.text_frame.paragraphs[0]
            set_pptx_paragraph_inline(p, title_text or "Slide")
            _style_paragraph(p, theme, title=True, use_title_layout=use_title_layout, Pt=Pt)
            if is_section_slide:
                # White text reads reliably on any of the theme's saturated accent
                # colors, unlike theme.title_color() which assumes a light background.
                try:
                    from pptx.dml.color import RGBColor

                    for run in p.runs:
                        run.font.color.rgb = RGBColor(255, 255, 255)
                except Exception:
                    pass
            elif is_quote_slide:
                try:
                    for run in p.runs:
                        run.font.italic = True
                    p.font.size = Pt(max(theme.title_size_pt, 32))
                except Exception:
                    pass

        if use_title_layout and subtitle_text and len(slide.placeholders) > 1:
            ph = slide.placeholders[1]
            if ph and ph.has_text_frame:
                p = ph.text_frame.paragraphs[0]
                set_pptx_paragraph_inline(p, subtitle_text)
                _style_paragraph(p, theme, title=False, use_title_layout=True, Pt=Pt, subtitle=True)
                if is_section_slide:
                    try:
                        from pptx.dml.color import RGBColor

                        for run in p.runs:
                            run.font.color.rgb = RGBColor(255, 255, 255)
                    except Exception:
                        pass

        elif bullet_points and len(slide.placeholders) > 1:
            ph = slide.placeholders[1]
            if ph and ph.has_text_frame:
                tf = ph.text_frame
                tf.clear()
                tf.word_wrap = True
                try:
                    tf.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
                except Exception:
                    pass
                for i, bp in enumerate(bullet_points):
                    para = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
                    set_pptx_paragraph_inline(para, bp)
                    _style_paragraph(para, theme, title=False, use_title_layout=False, Pt=Pt)
                    try:
                        para.level = 0
                    except Exception:
                        pass

        elif (
            not use_title_layout
            and title_text
            and len(slide.placeholders) > 1
        ):
            from services.presentation_markdown import _synthetic_bullets

            fallback = _synthetic_bullets(title_text, query=query)
            ph = slide.placeholders[1]
            if ph and ph.has_text_frame:
                tf = ph.text_frame
                tf.clear()
                for i, bp in enumerate(fallback):
                    para = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
                    set_pptx_paragraph_inline(para, bp)
                    _style_paragraph(para, theme, title=False, use_title_layout=False, Pt=Pt)
                    try:
                        para.level = 0
                    except Exception:
                        pass

        if text_col:
            _apply_left_text_column(
                slide,
                Inches,
                title_layout=use_title_layout,
            )

        if img_path and not (use_title_layout and layout_hint in ("background", "full-bleed", "hero", "title")):
            alt_text = image_desc or title_text
            if layout_hint in ("small-image", "icon", "thumbnail"):
                _add_slide_image(slide, img_path, Inches, position="corner", alt_text=alt_text)
            else:
                _add_slide_image(slide, img_path, Inches, position="right", alt_text=alt_text)

        if wants_notes and not speaker_notes.strip() and not use_title_layout:
            from pipeline.deliverables.presentation_deck import _fallback_notes

            speaker_notes = _fallback_notes(title_text, bullet_points)
            notes_repaired += 1
        _apply_slide_notes(slide, speaker_notes)

    if notes_repaired and log_fn:
        log_fn(
            f"Speaker notes were requested but missing for {notes_repaired} slide(s) — "
            "auto-generated fallback notes."
        )

    prs.save(filepath)
    return filepath


def _apply_slide_notes(slide, notes: str) -> None:
    text = (notes or "").strip()
    if not text:
        return
    try:
        notes_slide = slide.notes_slide
        tf = notes_slide.notes_text_frame
        tf.clear()
        tf.text = text
    except Exception:
        pass


def _visual_for_slide(slide_number: int, visuals: list[dict]) -> dict:
    for item in visuals:
        if int(item.get("slide_number") or 0) == slide_number:
            return item
    return {}


def _resolve_slide_image(
    description: str, slide_number: int, query: str,
    *, prof: dict | None = None, model: str | None = None, log_fn=None,
    source_text: str = "", settings: dict | None = None,
) -> str | None:
    """`description` (the slide's own [IMAGE_PROMPT:]/[IMAGE:] text or title) drives
    classification via services.marker_visual.resolve_marker_visual() — a process/
    steps description becomes a real flowchart, a key-facts description becomes a
    stat grid, otherwise a plain diffusion photo (using a slide-context-enriched
    prompt, same as before this became a shared dispatcher)."""
    desc = (description or "").strip()
    if not desc:
        return None
    try:
        from services.image_generation import prepare_image_prompt
        from services.marker_visual import resolve_marker_visual
        from services.model_router import image_generation_deps_available

        deps_ok, _ = image_generation_deps_available()
        if not deps_ok:
            return None
        photo_prompt = prepare_image_prompt(f"{desc}. Presentation slide visual. {query[:120]}")
        path = os.path.join(GENERATED_DIR, f"slide_{slide_number}_visual.png")
        result_path = resolve_marker_visual(
            desc, output_path=path, prof=prof, model=model, log_fn=log_fn, photo_prompt=photo_prompt,
            source_text=source_text, settings=settings,
        )
        if result_path and os.path.isfile(result_path):
            return result_path
    except Exception:
        return None
    return None


def _apply_left_text_column(slide, Inches, *, title_layout: bool = False) -> None:
    """Pin body into the left column; content titles span full width (8.8")."""
    left = Inches(0.55)
    body_width = Inches(4.7)
    title_width = Inches(4.7) if title_layout else Inches(8.8)
    try:
        title = slide.shapes.title
        if title is not None:
            if title_layout:
                title.left = left
                title.top = Inches(2.0)
                title.width = title_width
                title.height = Inches(1.8)
            else:
                title.left = left
                title.top = Inches(0.35)
                title.width = title_width
                title.height = Inches(1.15)
    except Exception:
        pass
    try:
        if len(slide.placeholders) > 1:
            body = slide.placeholders[1]
            if title_layout:
                body.left = left
                body.top = Inches(4.0)
                body.width = body_width
                body.height = Inches(1.6)
            else:
                body.left = left
                body.top = Inches(1.65)
                body.width = body_width
                body.height = Inches(5.0)
            if body.has_text_frame:
                body.text_frame.word_wrap = True
    except Exception:
        pass


def _add_slide_image(slide, img_path: str, Inches, *, position: str = "right", alt_text: str = "") -> None:
    try:
        if position == "corner":
            shape = slide.shapes.add_picture(img_path, Inches(8.8), Inches(5.8), width=Inches(1.2))
        else:
            # Right column visual — slightly below title band (top 1.65").
            shape = slide.shapes.add_picture(
                img_path, Inches(5.45), Inches(1.65), width=Inches(3.9), height=Inches(4.4)
            )
        _set_pptx_picture_alt_text(shape, alt_text)
    except Exception:
        pass


def _set_pptx_picture_alt_text(shape, alt_text: str) -> None:
    """Sets screen-reader alt text on a just-inserted pptx picture — same idea as
    services/artifact_build.py::_set_docx_picture_alt_text (reuses the image's own
    description text, no extra LLM call). python-pptx's Picture doesn't expose
    this as a property; cNvPr is the underlying <p:cNvPr> element's own descr
    attribute."""
    text = (alt_text or "").strip()
    if not text or shape is None:
        return
    try:
        shape._element.nvPicPr.cNvPr.set("descr", text[:250])
    except Exception:
        pass


def _add_background_image(slide, img_path: str, Inches) -> None:
    try:
        picture = slide.shapes.add_picture(img_path, Inches(0), Inches(0), width=Inches(10), height=Inches(7.5))
        # Move the picture behind existing placeholders (index 2, after the group's
        # own nvGrpSpPr/grpSpPr) so text renders on top of it instead of hiding it.
        # `slide.shapes[-1]` re-evaluates against the *current* tree on each access —
        # capturing `picture._element` up front before any removal is required here;
        # re-reading `slide.shapes[-1]` after the first `.remove()` (the previous
        # version of this code) returns a DIFFERENT shape once the picture is gone,
        # silently discarding the picture and reordering the wrong element instead —
        # the background image never appeared on any slide until this fix.
        slide.shapes._spTree.remove(picture._element)
        slide.shapes._spTree.insert(2, picture._element)
    except Exception:
        pass


def _split_by_slide_markers(text: str) -> list[str]:
    parts = re.split(r"(?=^---\s*Slide\s+\d+\s*---\s*$)", text, flags=re.MULTILINE | re.IGNORECASE)
    blocks: list[str] = []
    for part in parts:
        chunk = part.strip()
        if not chunk or re.match(r"^---\s*Slide\s+\d+\s*---\s*$", chunk, re.I):
            continue
        chunk = re.sub(r"^---\s*Slide\s+\d+\s*---\s*\n?", "", chunk, flags=re.I).strip()
        if chunk:
            blocks.append(chunk)
    return blocks


def _apply_slide_transition(slide, theme: PresentationTheme) -> None:
    """Subtle fade transition when the API allows (python-pptx XML)."""
    try:
        from lxml import etree
        from pptx.oxml.ns import qn

        sld = slide._element
        transition = sld.find(qn("p:transition"))
        if transition is None:
            transition = etree.SubElement(sld, qn("p:transition"))
        transition.set("spd", "med")
        if theme.mood in ("calm", "warm"):
            child = etree.SubElement(transition, qn("p:fade"))
        else:
            child = etree.SubElement(transition, qn("p:fade"))
        child.set("thruBlk", "0")
    except Exception:
        pass


def _apply_slide_background(slide, theme: PresentationTheme) -> None:
    try:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = theme.background_color()
    except Exception:
        pass


def _apply_accent_background(slide, theme: PresentationTheme) -> None:
    """Solid primary-color fill — used for section-divider slides, a deliberate
    visual break from the rest of the deck's light background."""
    try:
        fill = slide.background.fill
        fill.solid()
        fill.fore_color.rgb = theme.primary_color()
    except Exception:
        pass


def _add_accent_bar(slide, theme: PresentationTheme, Inches) -> None:
    try:
        from pptx.enum.shapes import MSO_SHAPE

        bar = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE,
            Inches(0),
            Inches(0),
            Inches(0.12),
            Inches(7.5),
        )
        bar.fill.solid()
        bar.fill.fore_color.rgb = theme.accent_color()
        bar.line.fill.background()
    except Exception:
        pass


def _style_paragraph(
    paragraph,
    theme: PresentationTheme,
    *,
    title: bool,
    use_title_layout: bool,
    Pt,
    subtitle: bool = False,
) -> None:
    try:
        font = paragraph.font
        if title or (use_title_layout and not subtitle):
            font.size = Pt(theme.title_size_pt)
            font.bold = True
            if theme.title_color():
                font.color.rgb = theme.title_color()
        elif subtitle:
            font.size = Pt(theme.subtitle_size_pt)
            if theme.body_color():
                font.color.rgb = theme.body_color()
        else:
            font.size = Pt(theme.body_size_pt)
            if theme.body_color():
                font.color.rgb = theme.body_color()
        try:
            from pptx.enum.text import PP_ALIGN

            paragraph.alignment = PP_ALIGN.LEFT
        except Exception:
            pass
    except Exception:
        pass
