# -*- coding: utf-8 -*-
"""Poster/infographic generation: a diffusion background (explicitly text-free) with
real text drawn on top afterward, since diffusion models cannot reliably spell words —
see pipeline/direct/image_intent.py for why this path exists at all.

The text overlay is authored with three interchangeable layout templates (the LLM
picks the one that fits the request), rendered on a supersampled layer for crisp
anti-aliased edges, and adapts its contrast (fill color, scrim strength, text stroke)
to the actual brightness of the region it's drawn over instead of assuming the
background is always dark there."""
from __future__ import annotations

import json
import os
import re
from typing import Any

from services.image_generation import GENERATED_IMAGE_DIR, ImageGenerationResult, unique_output_path

_POSTER_AUTHOR_SYSTEM = """You are LOMA's Poster Author. Return ONLY valid JSON (no markdown fences).

Given the user's poster/infographic request, produce:
{
  "background_prompt": "vivid scene/art description for the background image — MUST NOT mention any text, words, letters, typography, signage, or writing of any kind",
  "title_text": "a short, punchy title (2-6 words) in the SAME language the user wrote their request in",
  "subtitle_text": "an optional short supporting line (0-10 words), or empty string if not needed",
  "template": "lower_third | top_banner | centered_badge"
}

Rules:
- background_prompt describes ONLY the visual scene/artwork — never ask for text to appear in the image itself.
- title_text/subtitle_text must be in the user's own language, not translated to English.
- Keep title_text short enough to read on a poster at a glance.
- If the request names an event with a date, time, and/or location (e.g. "picnic at
  sunset park, 11 Aug 2026, 4pm"), title_text is the short event name only — put the
  date/time/location in subtitle_text instead of dropping them. Never let event
  details silently disappear because the title had to stay short.
- template: choose "centered_badge" for a short punchy slogan/seal/emblem-style poster
  (a single bold phrase meant to read like a badge or logo mark); "top_banner" when the
  request reads like a header/announcement/event banner; otherwise "lower_third" (the
  general-purpose default for a scenic/photo-style poster with a caption).
"""

_TEMPLATES = ("lower_third", "top_banner", "centered_badge")

# Small schematic SVG mockups for the style picker (see ui/components/style_picker.py)
# — a generic image-gradient card with the template's text-band position, not a real
# render (a real one would need to run the diffusion background 3x before the user
# even picks). Same idea PowerPoint/Canva's own template pickers use.
_POSTER_THUMB_DEFS = (
    '<defs><linearGradient id="loma-poster-grad" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0" stop-color="#7ea7c9"/><stop offset="1" stop-color="#3b4a5a"/>'
    "</linearGradient></defs>"
)
_POSTER_THUMBNAILS = {
    "lower_third": (
        '<svg viewBox="0 0 120 90" width="72" height="54" xmlns="http://www.w3.org/2000/svg">'
        + _POSTER_THUMB_DEFS
        + '<rect x="2" y="2" width="116" height="86" rx="6" fill="url(#loma-poster-grad)"/>'
        '<rect x="2" y="60" width="116" height="28" fill="#00000099"/>'
        '<rect x="12" y="70" width="60" height="7" rx="3" fill="#ffffff"/>'
        '<rect x="12" y="80" width="36" height="4" rx="2" fill="#ffffffaa"/>'
        "</svg>"
    ),
    "top_banner": (
        '<svg viewBox="0 0 120 90" width="72" height="54" xmlns="http://www.w3.org/2000/svg">'
        + _POSTER_THUMB_DEFS
        + '<rect x="2" y="2" width="116" height="86" rx="6" fill="url(#loma-poster-grad)"/>'
        '<rect x="2" y="2" width="116" height="24" fill="#00000099"/>'
        '<rect x="12" y="10" width="60" height="7" rx="3" fill="#ffffff"/>'
        '<rect x="12" y="20" width="36" height="4" rx="2" fill="#ffffffaa"/>'
        "</svg>"
    ),
    "centered_badge": (
        '<svg viewBox="0 0 120 90" width="72" height="54" xmlns="http://www.w3.org/2000/svg">'
        + _POSTER_THUMB_DEFS
        + '<rect x="2" y="2" width="116" height="86" rx="6" fill="url(#loma-poster-grad)"/>'
        '<circle cx="60" cy="45" r="26" fill="#00000099"/>'
        '<rect x="38" y="41" width="44" height="7" rx="3" fill="#ffffff"/>'
        "</svg>"
    ),
}

# User-facing template picker options — id must match _TEMPLATES. Labels/descriptions
# are keys resolved via pipeline.i18n at display time, not raw strings, so the picker
# UI stays localized.
POSTER_TEMPLATES = [
    {
        "id": "lower_third",
        "label_key": "poster.template.lower_third.label",
        "description_key": "poster.template.lower_third.description",
        "thumbnail_svg": _POSTER_THUMBNAILS["lower_third"],
    },
    {
        "id": "top_banner",
        "label_key": "poster.template.top_banner.label",
        "description_key": "poster.template.top_banner.description",
        "thumbnail_svg": _POSTER_THUMBNAILS["top_banner"],
    },
    {
        "id": "centered_badge",
        "label_key": "poster.template.centered_badge.label",
        "description_key": "poster.template.centered_badge.description",
        "thumbnail_svg": _POSTER_THUMBNAILS["centered_badge"],
    },
]


# A local LLM asked to "keep event details" in a prompt is not reliable about
# actually doing it — observed dropping the date, the time, or the venue
# unpredictably from one request to the next. These patterns pull the literal
# date/time/venue substrings out of the user's own query BEFORE authoring, so
# their presence in the final title+subtitle can be verified afterward and
# force-appended if the model dropped them — a guarantee, not a suggestion.
_DATE_RE = re.compile(
    r"\b(\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s*,?\s*\d{4}"
    r"|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}"
    r"|\d{1,2}/\d{1,2}/\d{2,4}"
    r"|\d{4}-\d{2}-\d{2})\b",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b", re.IGNORECASE)
# Not anchored to capitalized words — venue names in a casual chat request are
# often typed lowercase ("at sunset park"), and the word-start-with-letter
# requirement (vs. a digit) is what naturally stops the match before it can
# swallow a following date/time ("...at sunset park 11 Aug 2026" stops at
# "park" because "11" doesn't match [A-Za-z]).
_VENUE_RE = re.compile(
    r"\b(?:at|@)\s+(?:the\s+)?([A-Za-z][\w'.-]*(?:\s+[A-Za-z][\w'.-]*){0,4})", re.UNICODE
)
_STOPWORDS = {"the", "a", "an", "on", "at", "in", "for", "of", "to"}


def _extract_event_details(user_query: str) -> dict[str, str]:
    """Best-effort date/time/venue substrings found verbatim in the user's own
    query — empty string for any not found. Deliberately simple regex, not NLP:
    only needs to catch the common "event, venue, date, time" phrasing well
    enough to verify nothing got silently dropped later."""
    q = user_query or ""
    date_m = _DATE_RE.search(q)
    time_m = _TIME_RE.search(q)
    venue_m = _VENUE_RE.search(q)
    venue = venue_m.group(1).strip() if venue_m else ""
    # The venue regex is greedy about trailing capitalized words — trim off a
    # trailing date fragment it may have swallowed (e.g. "Sunset Park 11 Aug").
    if date_m and venue and date_m.group(0) in venue:
        venue = venue.split(date_m.group(0))[0].strip()
    return {
        "date": date_m.group(0).strip() if date_m else "",
        "time": time_m.group(0).strip() if time_m else "",
        "venue": venue,
    }


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[\w]+", (text or "").lower()) if w not in _STOPWORDS}


def _date_present(date_value: str, combined_words: set[str]) -> bool:
    """Format-agnostic: the LLM is free to reformat "11 Aug 2026" as "August 11,
    2026" — both are fine, so this checks that every NUMBER in the extracted
    date (day, year) shows up somewhere in the output, not that the exact
    substring survived verbatim."""
    numbers = re.findall(r"\d+", date_value)
    return bool(numbers) and all(n in combined_words for n in numbers)


def _time_present(time_value: str, combined_words: set[str]) -> bool:
    """Same idea as _date_present: "4pm"/"4 pm"/"4:00 PM" are all acceptable
    renderings, so this just checks the hour number and am/pm marker are both
    present somewhere, not the exact original spacing."""
    hour_m = re.search(r"\d{1,2}", time_value)
    ampm_m = re.search(r"am|pm", time_value, re.IGNORECASE)
    if not hour_m or not ampm_m:
        return False
    hour = hour_m.group(0)
    ampm = ampm_m.group(0).lower()
    # combined_words tokenizes on \w+, so "4pm" stays one token — check both the
    # split (hour, ampm as separate words) and the glued ("4pm") forms.
    return (hour in combined_words and ampm in combined_words) or any(
        w == f"{hour}{ampm}" for w in combined_words
    )


def _ensure_event_details_present(parts: dict[str, str], details: dict[str, str]) -> dict[str, str]:
    """If the author LLM dropped a date/time/venue that was actually in the
    user's request, append it to subtitle_text — guaranteed inclusion instead
    of hoping the model's prompt compliance held. Date/time checks tolerate
    reformatting (the LLM may write "August 11, 2026" instead of "11 Aug
    2026") — they only flag it as missing when it's genuinely absent, not
    just reworded."""
    combined_words = _words(parts.get("title_text", "")) | _words(parts.get("subtitle_text", ""))
    missing: list[str] = []
    date_value = details.get("date", "")
    if date_value and not _date_present(date_value, combined_words):
        missing.append(date_value)
    time_value = details.get("time", "")
    if time_value and not _time_present(time_value, combined_words):
        missing.append(time_value)
    venue_value = details.get("venue", "")
    if venue_value:
        venue_words = _words(venue_value)
        # Require every word of the venue name, not just any one — "Sunset Park"
        # only counts as present if both words show up; the title happening to
        # also say "Sunset" (as in "Sunset Picnic") isn't the same as naming the
        # venue, so a partial match must still be treated as missing.
        if venue_words and not venue_words.issubset(combined_words):
            missing.append(venue_value)
    if not missing:
        return parts
    subtitle = (parts.get("subtitle_text") or "").strip()
    addition = " | ".join(missing)
    parts = dict(parts)
    parts["subtitle_text"] = f"{subtitle} | {addition}" if subtitle else addition
    return parts


def resolve_explicit_poster_template(user_query: str) -> str | None:
    """None unless the user's own wording already names a specific template
    (e.g. "top banner style poster about X") — lets that phrasing skip the
    style picker and go straight to generation."""
    from pipeline.query_intent_i18n import matches

    if matches(user_query, "poster_style_top_banner_hints"):
        return "top_banner"
    if matches(user_query, "poster_style_centered_badge_hints"):
        return "centered_badge"
    if matches(user_query, "poster_style_lower_third_hints"):
        return "lower_third"
    return None


def _parse_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _author_poster_text(
    user_query: str, prof: dict, model: str, context: str = "", template: str | None = None,
) -> dict[str, str]:
    """`context`, if given, is grounding material (an attached-source excerpt or web
    search result) — see pipeline/base/grounding.py. Posters are usually short
    slogans, not factual claims, but a "did you know X%..." style poster deserves
    the same treatment as an infographic.

    `template`, if given (a user's explicit picker choice), overrides the LLM's own
    template pick — the author prompt still runs for the text/background content,
    just not for the layout decision."""
    from pipeline.capability_runtime.chat_runner import generate_text_sync

    user_content = user_query
    if context:
        user_content = (
            f"{user_query}\n\nGround any factual claim in this material — do not "
            f"state a number or date that isn't supported by it:\n{context}"
        )

    raw = generate_text_sync(
        prof,
        model,
        [
            {"role": "system", "content": _POSTER_AUTHOR_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        disable_thinking=True,
    )
    data = _parse_json(raw) or {}
    forced_template = (template or "").strip().lower()
    if forced_template not in _TEMPLATES:
        forced_template = ""
    author_template = str(data.get("template") or "").strip().lower()
    return {
        "background_prompt": str(data.get("background_prompt") or user_query).strip(),
        "title_text": str(data.get("title_text") or "").strip(),
        "subtitle_text": str(data.get("subtitle_text") or "").strip(),
        "template": forced_template or (author_template if author_template in _TEMPLATES else "lower_third"),
    }


_TEXT_SUPPRESSION = (
    "text, words, letters, typography, writing, caption, watermark, signage, "
    "labels, subtitles"
)

# Portrait aspect ratio suits most poster layouts; SDXL-family models get the larger
# native size, SD1.5-family the smaller one — mirrors services/image_generation.py's
# own per-family resolution presets rather than inventing a new size scheme.
_POSTER_SIZE_SD15 = (512, 768)
_POSTER_SIZE_SDXL = (896, 1152)

# The text overlay is drawn on a layer this many times the final resolution, then
# LANCZOS-downsampled once before compositing — keeps scrim gradients and text edges
# smooth instead of visibly stepped.
_OVERLAY_SCALE = 2


def generate_poster(
    user_query: str,
    *,
    prof: dict,
    model: str,
    output_path: str | None = None,
    progress_cb=None,
    context: str = "",
    template: str | None = None,
) -> ImageGenerationResult:
    from services.image_generation import (
        ANATOMY_NEGATIVE_PROMPT,
        generate_image,
        prepare_image_prompt,
    )
    from services.image_generation import resolve_image_presets
    from services.image_model_prefs import get_image_model_prefs
    from services.model_router import get_default_image_model_from_settings
    from config.model_catalog import image_pipeline_kind
    from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

    parts = _author_poster_text(user_query, prof, model, context, template=template)
    parts = _ensure_event_details_present(parts, _extract_event_details(user_query))
    background_prompt = prepare_image_prompt(parts["background_prompt"])
    theme = resolve_theme(query=user_query, design={"palette": infer_palette_from_query(user_query)})

    image_model_id = get_default_image_model_from_settings()
    family = image_pipeline_kind(image_model_id)
    width, height = _POSTER_SIZE_SDXL if family == "sdxl_lightning" else _POSTER_SIZE_SD15

    prefs = get_image_model_prefs(image_model_id)
    quality_mode = prefs.get("quality_mode", "")
    preset_steps, _preset_width, _preset_height = resolve_image_presets(
        image_model_id, quality_mode=quality_mode, resolution_preset=prefs.get("resolution_preset", ""),
    )

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        stem = re.sub(r"[^a-z0-9]+", "_", (user_query or "poster").lower())[:40].strip("_") or "poster"
        output_path = unique_output_path(stem + ".png", "poster")

    bg_result = generate_image(
        background_prompt,
        output_path=output_path,
        negative_prompt=f"{ANATOMY_NEGATIVE_PROMPT}, {_TEXT_SUPPRESSION}",
        width=width,
        height=height,
        steps=preset_steps,
        model_id=image_model_id,
        quality_mode=quality_mode,
        progress_cb=progress_cb,
    )
    if bg_result.fallback:
        return bg_result

    _overlay_poster_text(
        bg_result.path,
        title_text=parts["title_text"],
        subtitle_text=parts["subtitle_text"],
        template=parts["template"],
        accent_rgb=tuple(theme.accent_rgb),
    )
    return bg_result


def _sample_luminance(base_rgb_image, box: tuple[int, int, int, int]) -> float:
    """Average perceptual brightness (0-255) of `box` in the source image, used to
    decide whether overlaid text should be light-on-dark or dark-on-light."""
    from PIL import Image

    x0, y0, x1, y1 = box
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = max(x0 + 1, x1), max(y0 + 1, y1)
    region = base_rgb_image.crop((x0, y0, min(base_rgb_image.width, x1), min(base_rgb_image.height, y1)))
    if region.width == 0 or region.height == 0:
        return 128.0
    r, g, b = region.resize((1, 1), Image.BOX).getpixel((0, 0))[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b


def _text_palette_for_luminance(lum: float) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """(fill_color, stroke_color) with reliable contrast against a region whose
    average brightness is `lum`."""
    if lum > 150:
        return (20, 20, 20), (255, 255, 255)
    return (255, 255, 255), (0, 0, 0)


def _overlay_poster_text(
    image_path: str,
    *,
    title_text: str,
    subtitle_text: str,
    template: str,
    accent_rgb: tuple[int, int, int],
) -> None:
    if not title_text:
        return
    from PIL import Image

    with Image.open(image_path).convert("RGB") as base:
        w, h = base.size
        S = _OVERLAY_SCALE
        overlay = Image.new("RGBA", (w * S, h * S), (0, 0, 0, 0))

        if template == "centered_badge":
            _draw_centered_badge(overlay, base, w, h, S, title_text, subtitle_text, accent_rgb)
        elif template == "top_banner":
            _draw_gradient_band(overlay, base, w, h, S, title_text, subtitle_text, accent_rgb, side="top")
        else:
            _draw_gradient_band(overlay, base, w, h, S, title_text, subtitle_text, accent_rgb, side="bottom")

        overlay = overlay.resize((w, h), Image.LANCZOS)
        composited = Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB")
        composited.save(image_path)


def _draw_gradient_band(
    overlay, base, w: int, h: int, S: int,
    title_text: str, subtitle_text: str, accent_rgb, *, side: str,
) -> None:
    """lower_third / top_banner: a gradient scrim band with a title block, an
    accent-colored tag bar tying the text to the poster's color theme, adaptive
    fill/stroke contrast, and a text stroke as a contrast floor regardless of what's
    behind it."""
    from PIL import ImageDraw

    from services.poster_fonts import font_for_text, wrap_text

    draw = ImageDraw.Draw(overlay)
    W, H = w * S, h * S

    if side == "bottom":
        band_top = int(H * 0.62)
        sample_box = (0, int(h * 0.62), w, h)
    else:
        band_top = 0
        band_bottom = int(H * 0.38)
        sample_box = (0, 0, w, int(h * 0.38))

    lum = _sample_luminance(base, sample_box)
    fill_color, stroke_color = _text_palette_for_luminance(lum)
    # Midtone backgrounds are the hardest to guarantee legibility over, so they get
    # a stronger scrim than a background that's already clearly light or dark.
    scrim_alpha_peak = 190 if abs(lum - 128) < 40 else 150

    if side == "bottom":
        for y in range(band_top, H):
            t = (y - band_top) / max(1, (H - band_top))
            alpha = int(scrim_alpha_peak * min(1.0, t * 1.4))
            draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))
    else:
        for y in range(0, band_bottom):
            t = 1 - (y / max(1, band_bottom))
            alpha = int(scrim_alpha_peak * min(1.0, t * 1.4))
            draw.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    margin = int(W * 0.08)
    max_text_width = W - 2 * margin

    title_font_size = max(28, min(72, int(w / 11))) * S
    title_font = font_for_text(title_text, title_font_size, bold=True)
    title_lines = wrap_text(title_text, title_font, max_text_width, draw)
    while len(title_lines) > 3 and title_font_size > 24 * S:
        title_font_size -= 4 * S
        title_font = font_for_text(title_text, title_font_size, bold=True)
        title_lines = wrap_text(title_text, title_font, max_text_width, draw)

    subtitle_font_size = max(16 * S, int(title_font_size * 0.42))
    subtitle_font = font_for_text(subtitle_text, subtitle_font_size) if subtitle_text else None
    subtitle_lines = (
        wrap_text(subtitle_text, subtitle_font, max_text_width, draw) if subtitle_font else []
    )

    line_gap = int(title_font_size * 0.18)
    title_line_h = int(title_font_size * 1.25)
    subtitle_line_h = int(subtitle_font_size * 1.3) if subtitle_font else 0
    block_h = (
        len(title_lines) * title_line_h
        + (int(title_font_size * 0.35) if subtitle_lines else 0)
        + len(subtitle_lines) * subtitle_line_h
    )

    y = H - int(H * 0.08) - block_h if side == "bottom" else int(H * 0.06)

    accent_bar_w = int(W * 0.12)
    accent_bar_h = max(3 * S, int(title_font_size * 0.08))
    accent_y = y - int(title_font_size * 0.3)
    draw.rectangle([margin, accent_y, margin + accent_bar_w, accent_y + accent_bar_h], fill=accent_rgb)
    y += int(title_font_size * 0.15)

    stroke_w = max(1, S // 2)
    for line in title_lines:
        draw.text((margin, y), line, font=title_font, fill=fill_color, stroke_width=stroke_w, stroke_fill=stroke_color)
        y += title_line_h + line_gap
    if subtitle_lines:
        y += int(title_font_size * 0.15)
        for line in subtitle_lines:
            draw.text(
                (margin, y), line, font=subtitle_font, fill=fill_color,
                stroke_width=max(1, stroke_w // 2), stroke_fill=stroke_color,
            )
            y += subtitle_line_h


def _draw_centered_badge(
    overlay, base, w: int, h: int, S: int,
    title_text: str, subtitle_text: str, accent_rgb,
) -> None:
    """centered_badge: a rounded card centered on the image, accent-outlined, with
    centered title/subtitle — for a short punchy slogan/seal-style poster rather than
    a photo-with-caption layout."""
    from PIL import ImageDraw

    from services.poster_fonts import font_for_text, wrap_text

    draw = ImageDraw.Draw(overlay)
    W, H = w * S, h * S
    cx, cy = W // 2, H // 2

    badge_w = int(W * 0.72)
    title_font_size = max(30, min(64, int(w / 10))) * S
    title_font = font_for_text(title_text, title_font_size, bold=True)
    title_lines = wrap_text(title_text, title_font, int(badge_w * 0.86), draw)
    while len(title_lines) > 3 and title_font_size > 22 * S:
        title_font_size -= 4 * S
        title_font = font_for_text(title_text, title_font_size, bold=True)
        title_lines = wrap_text(title_text, title_font, int(badge_w * 0.86), draw)

    subtitle_font_size = max(14 * S, int(title_font_size * 0.4))
    subtitle_font = font_for_text(subtitle_text, subtitle_font_size) if subtitle_text else None
    subtitle_lines = (
        wrap_text(subtitle_text, subtitle_font, int(badge_w * 0.8), draw) if subtitle_font else []
    )

    title_line_h = int(title_font_size * 1.25)
    subtitle_line_h = int(subtitle_font_size * 1.3) if subtitle_font else 0
    pad_y = int(title_font_size * 0.6)
    content_h = (
        len(title_lines) * title_line_h
        + (int(title_font_size * 0.3) if subtitle_lines else 0)
        + len(subtitle_lines) * subtitle_line_h
    )
    badge_h = content_h + pad_y * 2

    lum = _sample_luminance(base, (
        max(0, (cx - badge_w // 2) // S), max(0, (cy - badge_h // 2) // S),
        min(w, (cx + badge_w // 2) // S), min(h, (cy + badge_h // 2) // S),
    ))
    fill_color, stroke_color = _text_palette_for_luminance(lum)
    scrim_color = (0, 0, 0, 165) if fill_color == (255, 255, 255) else (255, 255, 255, 175)

    draw.rounded_rectangle(
        [cx - badge_w // 2, cy - badge_h // 2, cx + badge_w // 2, cy + badge_h // 2],
        radius=28 * S, fill=scrim_color, outline=accent_rgb, width=max(2, 3 * S),
    )

    accent_bar_w = int(badge_w * 0.18)
    accent_bar_h = max(3 * S, int(title_font_size * 0.07))
    accent_y = cy - content_h // 2 - int(title_font_size * 0.45)
    draw.rectangle(
        [cx - accent_bar_w // 2, accent_y, cx + accent_bar_w // 2, accent_y + accent_bar_h],
        fill=accent_rgb,
    )

    stroke_w = max(1, S // 2)
    ty = cy - content_h // 2
    for line in title_lines:
        lw = draw.textbbox((0, 0), line, font=title_font)[2]
        draw.text((cx - lw // 2, ty), line, font=title_font, fill=fill_color, stroke_width=stroke_w, stroke_fill=stroke_color)
        ty += title_line_h
    if subtitle_lines:
        ty += int(title_font_size * 0.15)
        for line in subtitle_lines:
            lw = draw.textbbox((0, 0), line, font=subtitle_font)[2]
            draw.text(
                (cx - lw // 2, ty), line, font=subtitle_font, fill=fill_color,
                stroke_width=max(1, stroke_w // 2), stroke_fill=stroke_color,
            )
            ty += subtitle_line_h
