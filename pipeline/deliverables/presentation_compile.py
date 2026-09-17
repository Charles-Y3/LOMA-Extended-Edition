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

        is_section_slide = slide_layout_kind == "section"
        is_quote_slide = slide_layout_kind == "quote"
        use_title_layout = (
            (slide_idx == 0 and bool(title_text) and len(bullet_points) <= 1)
            or is_section_slide
            or is_quote_slide
        )

        # Structural backstop for the 3 deck styles (see presentation_theme.PRESENTATION_
        # STYLES / step_executor.PRESENTATION_STYLE_PROMPT_BIAS) — those only *ask* the
        # planner LLM to pick different layout_hints/visual types per style; this
        # guarantees the visible difference even when the planner ignores that
        # instruction.
        bold_variant = None
        if wants_image and image_desc:
            if presentation_style == "bold":
                # Every image slide (not just the title) gets a full-bleed
                # photo — that's the "modern, picture overlay with words"
                # identity — but the treatment rotates so it doesn't read as
                # "always a dark scrim": title/section slides get the classic
                # full overlay, other content slides alternate between a
                # bottom-band overlay (more of the photo stays visible) and a
                # split-hero (half photo, half solid accent panel — no scrim
                # at all). See bold_variant branches below for the renders.
                layout_hint = "background"
                if slide_idx == 0 or is_section_slide:
                    bold_variant = "overlay-full"
                elif slide_idx % 2 == 1:
                    bold_variant = "split-hero"
                else:
                    bold_variant = "overlay-band"
            elif presentation_style == "insight":
                # Layout follows what the visual actually is, not a fixed
                # column: a chart/diagram/stat grid needs real width to stay
                # legible (that was the complaint — charts squeezed into a
                # 3.9" column are unreadable), while a comparison or a plain
                # fallback photo still reads fine in the side column.
                from pipeline.direct.image_intent import classify_image_request

                kind = classify_image_request(image_desc)
                layout_hint = "wide" if kind in (
                    "chart", "diagram", "infographic_stat", "infographic_timeline",
                ) else "right"
            elif presentation_style == "minimal":
                # Every image sits in the same side column as the other
                # styles — a small corner thumbnail read as an afterthought
                # rather than "minimal" (a real run made it look identical to
                # a plain content slide with a stray icon). The restraint
                # comes from sparse bullets/no accent bar, not from shrinking
                # the image into a corner.
                layout_hint = "right"
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
        if is_section_slide or bold_variant == "split-hero":
            # A section-divider slide is a deliberate visual break between deck
            # sections — a solid accent-colored background (instead of the normal
            # light background) reads as a distinct "chapter marker" at a glance.
            # A split-hero slide reuses the same solid fill for its text half —
            # the picture (added below) only covers the other half, so the fill
            # shows through underneath it.
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
        resolved_image_desc = image_desc
        if bold_variant == "overlay-band" and resolved_image_desc:
            # The bottom ~39% of this image will sit under a scrim + text band
            # (see _add_overlay_band_scrim) — nothing else about the prompt
            # knows that, so a subject the model places low in the frame gets
            # covered regardless of the scrim's own safety margin. Asking for
            # upper-frame composition doesn't guarantee compliance, but it's
            # a real second layer on top of the enlarged scrim, not instead
            # of it.
            resolved_image_desc = (
                f"{resolved_image_desc}, main subject composed in the upper "
                "two-thirds of the frame, lower third kept clear/open"
            )
        img_path, img_sources = (
            _resolve_slide_image(
                resolved_image_desc, slide_idx + 1, query, prof=prof, model=model, log_fn=log_fn,
                # NOT the deck's own generated markdown — pipeline.base.grounding.
                # _source_excerpt() treats ANY non-empty source_text as real material
                # and returns it immediately, before ever attempting a web search, so
                # passing the deck's own (possibly unverified) prose here made every
                # chart/diagram "grounded" in text that was never independently
                # checked, laundering fabricated numbers as if cited (confirmed via a
                # real run: a chart cited "grounding" that traced back to nothing but
                # the deck's own prior sentence). Leaving this empty makes each
                # slide's chart/diagram run its OWN scoped, credibility- and
                # relevance-filtered web search (see resolve_generation_context /
                # gather_grounded_context's topic_relevance path) keyed to that
                # slide's own image_desc — independent of whether the deck's bullet
                # text (presentation_grounded, used only for the numeric-claim scrub
                # above) happened to be grounded.
                source_text="",
                settings=settings,
                # Ground the WEB SEARCH in the slide's own already-written
                # (already numeric-scrubbed) title/bullets, not the raw image
                # marker text — see _resolve_slide_image's docstring for why.
                topic_text=f"{title_text} {' '.join(bullet_points)}".strip(),
            )
            if wants_image and image_desc
            else (None, [])
        )
        is_overlay_slide = bool(
            img_path
            and (
                bold_variant in ("overlay-full", "overlay-band")
                or (use_title_layout and layout_hint in ("background", "full-bleed", "hero", "title"))
            )
        )
        is_split_hero_slide = bool(img_path and bold_variant == "split-hero")
        is_wide_slide = bool(img_path and layout_hint == "wide")
        use_side_image = bool(
            img_path and not is_overlay_slide and not is_split_hero_slide and not is_wide_slide
        )
        # Side image → reserve a real left text column (width+top+height). Setting
        # only width used to collapse placeholder height to 0 in python-pptx.
        text_col = bool(text_left and use_side_image)

        if is_overlay_slide:
            _add_background_image(slide, img_path, Inches)
            if bold_variant == "overlay-band":
                _add_overlay_band_scrim(slide, Inches)
            else:
                _add_overlay_scrim(slide, Inches)
        elif is_split_hero_slide:
            _add_slide_image(slide, img_path, Inches, position="half-right", alt_text=(image_desc or title_text))

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

        if is_overlay_slide or is_split_hero_slide:
            # Scrim/solid-accent-panel guarantees contrast against any photo,
            # but the theme's own (light-background-tuned) title/body colors
            # would still be unreadable on it — force both to white, same
            # treatment as section-divider slides above.
            try:
                from pptx.dml.color import RGBColor

                if slide.shapes.title:
                    for run in slide.shapes.title.text_frame.paragraphs[0].runs:
                        run.font.color.rgb = RGBColor(255, 255, 255)
                if len(slide.placeholders) > 1:
                    body_ph = slide.placeholders[1]
                    if body_ph.has_text_frame:
                        for para in body_ph.text_frame.paragraphs:
                            for run in para.runs:
                                run.font.color.rgb = RGBColor(255, 255, 255)
            except Exception:
                pass

        if text_col:
            _apply_left_text_column(
                slide,
                Inches,
                Pt,
                title_layout=use_title_layout,
            )
        elif is_split_hero_slide:
            # Same idea as text_col but narrower (fits inside the solid-color
            # half rather than a 3.9"-image-implied column) and independent
            # of text_left, since a split-hero's text placement isn't driven
            # by layout_hint.
            _apply_left_text_column(
                slide,
                Inches,
                Pt,
                title_layout=use_title_layout,
                narrow=True,
            )
        elif is_overlay_slide and bold_variant == "overlay-band" and not use_title_layout:
            _apply_overlay_band_text(slide, Inches, Pt)

        if img_path and not is_overlay_slide and not is_split_hero_slide:
            alt_text = image_desc or title_text
            if is_wide_slide:
                _add_slide_image(slide, img_path, Inches, position="wide", alt_text=alt_text)
                if not use_title_layout:
                    _apply_wide_visual_band(slide, Inches, Pt)
            elif layout_hint in ("small-image", "icon", "thumbnail"):
                _add_slide_image(slide, img_path, Inches, position="corner", alt_text=alt_text)
            else:
                _add_slide_image(slide, img_path, Inches, position="right", alt_text=alt_text)

        if img_sources:
            # Only ever populated when a slide's chart/diagram/photo was
            # actually grounded in a real web search (see resolve_generation_
            # context's docstring) — never fabricated, so it's safe to cite
            # directly in the notes rather than leaving the number/claim
            # unattributed.
            ref_text = "; ".join(
                f"{(s.get('title') or '').strip()} ({(s.get('url') or '').strip()})".strip()
                if (s.get("title") and s.get("url"))
                else (s.get("title") or s.get("url") or "").strip()
                for s in img_sources
                if (s.get("title") or "").strip() or (s.get("url") or "").strip()
            )
            if ref_text:
                speaker_notes = (
                    f"{speaker_notes}\n\nImage source: {ref_text}".strip()
                    if speaker_notes.strip()
                    else f"Image source: {ref_text}"
                )

        if not speaker_notes.strip():
            # Every slide gets speaker notes regardless of style or whether the
            # user explicitly asked for them — the model doesn't reliably write
            # a notes line for each slide, so this is a mechanical backstop,
            # not a gate on user intent.
            from pipeline.deliverables.presentation_deck import _fallback_notes

            notes_source = bullet_points or ([subtitle_text] if subtitle_text else [])
            speaker_notes = _fallback_notes(title_text, notes_source)
            notes_repaired += 1
        _apply_slide_notes(slide, speaker_notes)

    if notes_repaired and log_fn:
        log_fn(
            f"Auto-generated fallback speaker notes for {notes_repaired} slide(s) "
            "without model-authored notes."
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
    source_text: str = "", settings: dict | None = None, topic_text: str = "",
) -> tuple[str | None, list[dict[str, str]]]:
    """`description` (the slide's own [IMAGE_PROMPT:]/[IMAGE:] text or title) drives
    classification via services.marker_visual.resolve_marker_visual() — a process/
    steps description becomes a real flowchart, a key-facts description becomes a
    stat grid, otherwise a plain diffusion photo (using a slide-context-enriched
    prompt, same as before this became a shared dispatcher). The returned sources
    list is only ever non-empty when a chart/diagram/infographic was genuinely
    grounded in a real web search — never populated for a plain photo or an
    attached-source excerpt (not an online citation) — so it's safe to surface
    directly as a speaker-note reference.

    `topic_text` (the slide's own title+bullets) drives the web search instead
    of `description` when given — a marker's own free-text description can
    invent a framing detail (an extra comparison axis, a qualifier) that isn't
    in the slide's actual content, which then dominates the search and grounds
    the visual in something the slide was never about (confirmed via a real
    run: an image marker invented "vs. traditional emergency vehicles" as a
    comparison for a slide that was actually about green infrastructure, and
    the resulting chart ended up about firetrucks)."""
    desc = (description or "").strip()
    if not desc:
        return None, []
    try:
        from services.image_generation import prepare_image_prompt
        from services.marker_visual import resolve_marker_visual
        from services.model_router import image_generation_deps_available

        deps_ok, _ = image_generation_deps_available()
        if not deps_ok:
            return None, []
        photo_prompt = prepare_image_prompt(f"{desc}. Presentation slide visual. {query[:120]}")
        path = os.path.join(GENERATED_DIR, f"slide_{slide_number}_visual.png")
        result_path, sources = resolve_marker_visual(
            desc, output_path=path, prof=prof, model=model, log_fn=log_fn, photo_prompt=photo_prompt,
            source_text=source_text, settings=settings, topic_text=topic_text,
        )
        if result_path and os.path.isfile(result_path):
            return result_path, sources
    except Exception:
        return None, []
    return None, []


def _apply_left_text_column(slide, Inches, Pt, *, title_layout: bool = False, narrow: bool = False) -> None:
    """Pin body into the left column; content titles span full width (8.8").
    `narrow=True` (bold style's split-hero) fits inside the left HALF of the
    slide (image covers the other 5") instead of the 3.9"-side-image-implied
    column, so text never overlaps the picture."""
    left = Inches(0.55)
    col_width = 4.3 if narrow else 4.7
    body_width = Inches(col_width)
    title_width = Inches(col_width) if title_layout else Inches(8.8 if not narrow else col_width)
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
                if title_layout:
                    # Just the (already short-clamped elsewhere) subtitle —
                    # one line, generous cap, purely defensive.
                    _cap_body_text_frame(body.text_frame, Pt, max_bullets=1, max_chars=160, font_pt=16)
                else:
                    _cap_body_text_frame(
                        body.text_frame, Pt, max_bullets=4,
                        max_chars=(80 if narrow else 95), font_pt=(13 if narrow else 14),
                    )
    except Exception:
        pass


def _add_slide_image(slide, img_path: str, Inches, *, position: str = "right", alt_text: str = "") -> None:
    try:
        if position == "corner":
            shape = slide.shapes.add_picture(img_path, Inches(8.8), Inches(5.8), width=Inches(1.2))
        elif position == "wide":
            # Insight style's chart/diagram slot — near-full width so a chart
            # actually stays legible, unlike the 3.9" side column.
            shape = slide.shapes.add_picture(
                img_path, Inches(0.55), Inches(1.55), width=Inches(8.9), height=Inches(4.15)
            )
        elif position == "half-right":
            # Bold style's split-hero — image fills the right half; the left
            # half shows the slide's own solid accent fill underneath.
            shape = slide.shapes.add_picture(img_path, Inches(5.0), Inches(0), width=Inches(5.0), height=Inches(7.5))
        else:
            # Right column visual — slightly below title band (top 1.65").
            shape = slide.shapes.add_picture(
                img_path, Inches(5.45), Inches(1.65), width=Inches(3.9), height=Inches(4.4)
            )
        _set_pptx_picture_alt_text(shape, alt_text)
    except Exception:
        pass


_WIDE_BAND_MAX_BULLETS = 3
_WIDE_BAND_MAX_CHARS = 88
_WIDE_BAND_FONT_PT = 13


def _cap_body_text_frame(text_frame, Pt, *, max_bullets: int, max_chars: int, font_pt: int) -> None:
    """Shared text-safety net — caps bullet count/length and forces an
    explicit small font, so a text box can't overflow its slot regardless of
    renderer, theme body size, or how many/how long the authored bullets
    are. MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE (set when bullets were first
    written) is only a PowerPoint-render-time hint — python-pptx doesn't
    enforce it and not every renderer honors it (confirmed via two separate
    real overflow/illegible-text reports: Insight's chart caption band, and
    Bold's split-hero narrow column). Every layout that positions text in a
    constrained box (not the full default content placeholder) should call
    this after repositioning, instead of each keeping its own copy — this
    used to be duplicated per-variant, which is exactly how the narrow
    split-hero column ended up shipped without the same cap the wide band
    already had."""
    try:
        text_frame.word_wrap = True
        paragraphs = list(text_frame.paragraphs)
        for para in paragraphs[max_bullets:]:
            para._p.getparent().remove(para._p)
        for para in paragraphs[:max_bullets]:
            text = "".join(run.text for run in para.runs)
            if len(text) > max_chars:
                clipped = text[:max_chars]
                last_space = clipped.rfind(" ")
                if last_space > max_chars * 0.6:
                    clipped = clipped[:last_space]
                text = clipped.rstrip(" ,.;:") + "…"
            for run in list(para.runs)[1:]:
                run._r.getparent().remove(run._r)
            if para.runs:
                para.runs[0].text = text
            elif text:
                para.add_run().text = text
            for run in para.runs:
                run.font.size = Pt(font_pt)
    except Exception:
        pass


def _apply_wide_visual_band(slide, Inches, Pt) -> None:
    """Insight style's chart/diagram layout: the visual is the dominant
    element (near-full width, large), bullets condense into a thin band
    underneath instead of squeezing into a side column — a real chart needs
    the horizontal room to stay legible, unlike a plain photo."""
    try:
        if len(slide.placeholders) <= 1:
            return
        body = slide.placeholders[1]
        body.left = Inches(0.55)
        body.top = Inches(5.85)
        body.width = Inches(8.9)
        body.height = Inches(1.45)
        if not body.has_text_frame:
            return
    except Exception:
        return
    _cap_body_text_frame(
        body.text_frame, Pt,
        max_bullets=_WIDE_BAND_MAX_BULLETS, max_chars=_WIDE_BAND_MAX_CHARS, font_pt=_WIDE_BAND_FONT_PT,
    )


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


def _add_overlay_scrim(slide, Inches, *, alpha_pct: int = 55) -> None:
    """Semi-transparent dark panel over a full-bleed background image so white
    overlay text stays legible regardless of the photo's own colors/brightness.
    python-pptx has no fill-transparency API — the <a:alpha> child is added via
    direct XML. Must run right after _add_background_image so it lands just
    above the picture (index 3) but below the title/body placeholders."""
    try:
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.oxml.ns import qn

        scrim = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(10), Inches(7.5))
        scrim.fill.solid()
        scrim.fill.fore_color.rgb = RGBColor(0, 0, 0)
        scrim.line.fill.background()
        srgb = scrim.fill.fore_color._xFill.find(qn("a:srgbClr"))
        if srgb is not None:
            alpha = srgb.makeelement(qn("a:alpha"), {"val": str(alpha_pct * 1000)})
            srgb.append(alpha)
        slide.shapes._spTree.remove(scrim._element)
        slide.shapes._spTree.insert(3, scrim._element)
    except Exception:
        pass


def _add_overlay_band_scrim(slide, Inches, *, alpha_pct: int = 70) -> None:
    """Bold style's 'overlay-band' variant — same idea as _add_overlay_scrim
    but confined to a bottom band instead of the full slide, so most of the
    photo stays visible instead of every image slide looking identically
    dark. Slightly higher alpha than the full scrim since the band sits
    directly behind text with no other help from surrounding darkness.

    Covers the bottom ~39% of the slide (was ~32%) — a real report showed
    text legible against the scrim itself but still competing with visible
    photo detail right at the band's old top edge; the extra margin, paired
    with the image prompt's own "keep the lower third clear" composition
    hint (see presentation_compile's bold_variant image-prompt block), gives
    real headroom instead of assuming the subject never drifts low."""
    try:
        from pptx.dml.color import RGBColor
        from pptx.enum.shapes import MSO_SHAPE
        from pptx.oxml.ns import qn

        scrim = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(4.6), Inches(10), Inches(2.9))
        scrim.fill.solid()
        scrim.fill.fore_color.rgb = RGBColor(0, 0, 0)
        scrim.line.fill.background()
        srgb = scrim.fill.fore_color._xFill.find(qn("a:srgbClr"))
        if srgb is not None:
            alpha = srgb.makeelement(qn("a:alpha"), {"val": str(alpha_pct * 1000)})
            srgb.append(alpha)
        slide.shapes._spTree.remove(scrim._element)
        slide.shapes._spTree.insert(3, scrim._element)
    except Exception:
        pass


def _apply_overlay_band_text(slide, Inches, Pt) -> None:
    """Confines title+body to the bottom band for bold's 'overlay-band'
    variant, matching _add_overlay_band_scrim's geometry — otherwise the
    default title-at-top position would sit on unscrimmed photo."""
    left = Inches(0.5)
    width = Inches(9.0)
    try:
        title = slide.shapes.title
        if title is not None:
            title.left = left
            title.top = Inches(4.75)
            title.width = width
            title.height = Inches(0.85)
    except Exception:
        pass
    try:
        if len(slide.placeholders) > 1:
            body = slide.placeholders[1]
            body.left = left
            body.top = Inches(5.6)
            body.width = width
            body.height = Inches(1.7)
            if body.has_text_frame:
                _cap_body_text_frame(body.text_frame, Pt, max_bullets=3, max_chars=110, font_pt=14)
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
