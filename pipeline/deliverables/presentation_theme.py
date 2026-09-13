# -*- coding: utf-8 -*-
"""Topic-aware presentation themes with bounded palettes (agentic compile only)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from pipeline.deliverables.presentation_limits import ALLOWED_MOODS, ALLOWED_PALETTES

try:
    from pptx.dml.color import RGBColor
except ImportError:
    RGBColor = None  # type: ignore


@dataclass(frozen=True)
class PresentationTheme:
    palette_id: str
    mood: str
    primary_rgb: tuple[int, int, int]
    accent_rgb: tuple[int, int, int]
    background_rgb: tuple[int, int, int]
    title_rgb: tuple[int, int, int]
    body_rgb: tuple[int, int, int]
    title_size_pt: int
    body_size_pt: int
    subtitle_size_pt: int
    use_accent_bar: bool = True

    def primary_color(self):
        if RGBColor is None:
            return None
        return RGBColor(*self.primary_rgb)

    def accent_color(self):
        if RGBColor is None:
            return None
        return RGBColor(*self.accent_rgb)

    def background_color(self):
        if RGBColor is None:
            return None
        return RGBColor(*self.background_rgb)

    def title_color(self):
        if RGBColor is None:
            return None
        return RGBColor(*self.title_rgb)

    def body_color(self):
        if RGBColor is None:
            return None
        return RGBColor(*self.body_rgb)


_PALETTES: dict[str, dict[str, Any]] = {
    "warm_coral": {
        "mood": "warm",
        "primary": (198, 72, 72),
        "accent": (255, 183, 120),
        "background": (255, 250, 245),
        "title": (62, 39, 35),
        "body": (78, 52, 46),
    },
    "rose_blush": {
        "mood": "warm",
        "primary": (190, 80, 110),
        "accent": (255, 200, 210),
        "background": (255, 248, 250),
        "title": (80, 40, 55),
        "body": (90, 55, 65),
    },
    "ocean_teal": {
        "mood": "calm",
        "primary": (0, 128, 128),
        "accent": (64, 196, 196),
        "background": (245, 252, 252),
        "title": (20, 55, 60),
        "body": (35, 70, 75),
    },
    "forest_green": {
        "mood": "nature",
        "primary": (46, 125, 50),
        "accent": (129, 199, 132),
        "background": (248, 252, 248),
        "title": (27, 60, 35),
        "body": (40, 75, 48),
    },
    "royal_purple": {
        "mood": "creative",
        "primary": (103, 58, 183),
        "accent": (179, 136, 255),
        "background": (250, 248, 255),
        "title": (45, 30, 70),
        "body": (60, 45, 85),
    },
    "slate_modern": {
        "mood": "professional",
        "primary": (55, 71, 90),
        "accent": (100, 181, 246),
        "background": (248, 249, 251),
        "title": (33, 37, 41),
        "body": (55, 65, 75),
    },
    "sunset_amber": {
        "mood": "bold",
        "primary": (230, 126, 34),
        "accent": (255, 213, 79),
        "background": (255, 252, 245),
        "title": (70, 45, 20),
        "body": (85, 55, 30),
    },
    "midnight_blue": {
        "mood": "professional",
        "primary": (25, 55, 95),
        "accent": (66, 165, 245),
        "background": (245, 247, 250),
        "title": (15, 30, 55),
        "body": (35, 50, 70),
    },
}

# Concept names (query_intent_i18n.CONCEPTS) for each palette's topic signal — one
# shared, multilingual source instead of an English-only per-file tuple. See
# CLAUDE.md section 8.
_TOPIC_CONCEPTS: list[tuple[str, str]] = [
    ("topic_warm_coral", "warm_coral"),
    ("topic_ocean_teal", "ocean_teal"),
    ("topic_forest_green", "forest_green"),
    ("topic_royal_purple", "royal_purple"),
    ("topic_slate_modern", "slate_modern"),
    ("topic_midnight_blue", "midnight_blue"),
    ("topic_sunset_amber", "sunset_amber"),
]


# Small schematic SVG mockups for the style picker (see ui/components/style_picker.py)
# — a mini slide-shaped card in the style's real accent colors (pulled from _PALETTES
# below), not a real render. Same idea PowerPoint/Canva's own template pickers use.
_PRESENTATION_THUMBNAILS = {
    "minimal": (
        '<svg viewBox="0 0 120 90" width="72" height="54" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="2" y="2" width="116" height="86" rx="4" fill="#f8f9fb" stroke="#dde2e8"/>'
        '<rect x="14" y="16" width="60" height="7" rx="2" fill="#37475a"/>'
        '<rect x="14" y="34" width="80" height="4" rx="2" fill="#c7ccd3"/>'
        '<rect x="14" y="44" width="70" height="4" rx="2" fill="#c7ccd3"/>'
        '<rect x="14" y="54" width="50" height="4" rx="2" fill="#c7ccd3"/>'
        "</svg>"
    ),
    "bold_editorial": (
        '<svg viewBox="0 0 120 90" width="72" height="54" xmlns="http://www.w3.org/2000/svg">'
        '<defs><linearGradient id="loma-pres-grad" x1="0" y1="0" x2="1" y2="1">'
        '<stop offset="0" stop-color="#ffd54f"/><stop offset="1" stop-color="#e67e22"/>'
        "</linearGradient></defs>"
        '<rect x="2" y="2" width="116" height="86" rx="4" fill="url(#loma-pres-grad)"/>'
        '<rect x="2" y="58" width="116" height="30" fill="#00000066"/>'
        '<rect x="14" y="66" width="66" height="8" rx="2" fill="#ffffff"/>'
        '<rect x="14" y="78" width="40" height="4" rx="2" fill="#ffffffcc"/>'
        "</svg>"
    ),
    "data_heavy": (
        '<svg viewBox="0 0 120 90" width="72" height="54" xmlns="http://www.w3.org/2000/svg">'
        '<rect x="2" y="2" width="116" height="86" rx="4" fill="#f5fcfc" stroke="#cdeaea"/>'
        '<rect x="14" y="16" width="50" height="7" rx="2" fill="#008080"/>'
        '<rect x="14" y="34" width="42" height="4" rx="2" fill="#8fd4d4"/>'
        '<rect x="14" y="44" width="36" height="4" rx="2" fill="#8fd4d4"/>'
        '<rect x="66" y="46" width="10" height="30" fill="#40c4c4"/>'
        '<rect x="80" y="34" width="10" height="42" fill="#008080"/>'
        '<rect x="94" y="54" width="10" height="22" fill="#40c4c4"/>'
        "</svg>"
    ),
}

# User-facing deck style picker options — each maps to a forced palette plus a
# deck_planner prompt bias (see pipeline/direct/step_executor.py's
# PRESENTATION_STYLE_PROMPT_BIAS). Labels/descriptions are i18n keys, not raw
# strings, so the picker UI stays localized.
PRESENTATION_STYLES = [
    {
        "id": "minimal",
        "label_key": "presentation.style.minimal.label",
        "description_key": "presentation.style.minimal.description",
        "palette": "slate_modern",
        "thumbnail_svg": _PRESENTATION_THUMBNAILS["minimal"],
    },
    {
        "id": "bold_editorial",
        "label_key": "presentation.style.bold_editorial.label",
        "description_key": "presentation.style.bold_editorial.description",
        "palette": "sunset_amber",
        "thumbnail_svg": _PRESENTATION_THUMBNAILS["bold_editorial"],
    },
    {
        "id": "data_heavy",
        "label_key": "presentation.style.data_heavy.label",
        "description_key": "presentation.style.data_heavy.description",
        "palette": "ocean_teal",
        "thumbnail_svg": _PRESENTATION_THUMBNAILS["data_heavy"],
    },
]

_PRESENTATION_STYLE_PALETTE = {row["id"]: row["palette"] for row in PRESENTATION_STYLES}


def resolve_style_palette(style_id: str) -> str | None:
    return _PRESENTATION_STYLE_PALETTE.get((style_id or "").strip().lower())


def resolve_explicit_presentation_style(user_query: str) -> str | None:
    """None unless the user's own wording already names a specific deck style
    (e.g. "minimal presentation about X") — lets that phrasing skip the style
    picker and go straight to generation."""
    from pipeline.query_intent_i18n import matches

    if matches(user_query, "presentation_style_bold_editorial_hints"):
        return "bold_editorial"
    if matches(user_query, "presentation_style_data_heavy_hints"):
        return "data_heavy"
    if matches(user_query, "presentation_style_minimal_hints"):
        return "minimal"
    return None


def infer_palette_from_query(query: str) -> str:
    from pipeline.query_intent_i18n import matches

    lower = (query or "").lower()
    for concept, palette in _TOPIC_CONCEPTS:
        if matches(lower, concept):
            return palette
    return "slate_modern"


def resolve_theme(
    *,
    query: str = "",
    design: dict[str, Any] | None = None,
) -> PresentationTheme:
    """Merge worker design block with safe defaults and topic inference."""
    design = design or {}
    mood = str(design.get("mood") or "").strip().lower()
    if mood not in ALLOWED_MOODS:
        mood = ""

    palette_id = str(design.get("palette") or design.get("palette_id") or "").strip().lower()
    if palette_id not in ALLOWED_PALETTES:
        palette_id = infer_palette_from_query(query)

    row = _PALETTES.get(palette_id) or _PALETTES["slate_modern"]
    if not mood:
        mood = str(row.get("mood") or "professional")

    title_pt = _clamp_int(design.get("title_size_pt"), 32, 28, 44)
    body_pt = _clamp_int(design.get("body_size_pt"), 18, 14, 22)
    subtitle_pt = max(body_pt, min(title_pt - 4, 24))

    return PresentationTheme(
        palette_id=palette_id,
        mood=mood,
        primary_rgb=tuple(row["primary"]),
        accent_rgb=tuple(row["accent"]),
        background_rgb=tuple(row["background"]),
        title_rgb=tuple(row["title"]),
        body_rgb=tuple(row["body"]),
        title_size_pt=title_pt,
        body_size_pt=body_pt,
        subtitle_size_pt=subtitle_pt,
        use_accent_bar=bool(design.get("accent_bar", True)),
    )


def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def theme_meta_block(theme: PresentationTheme) -> str:
    """Embed in markdown for round-trip (stripped before display)."""
    return (
        f"<!-- loma-theme: palette={theme.palette_id}; mood={theme.mood} -->"
    )


def extract_theme_from_text(text: str, query: str = "") -> tuple[PresentationTheme, str]:
    """Parse embedded theme comment or JSON design fence; return cleaned markdown."""
    body = text or ""
    design: dict[str, Any] = {}

    meta = re.search(r"<!--\s*loma-theme:\s*([^>]+)-->", body, re.IGNORECASE)
    if meta:
        chunk = meta.group(1)
        for part in chunk.split(";"):
            if "=" in part:
                k, v = part.split("=", 1)
                design[k.strip()] = v.strip()
        body = body[: meta.start()] + body[meta.end() :]

    fence = re.search(r"```(?:json|yaml)?\s*design\s*\n(.*?)```", body, re.DOTALL | re.IGNORECASE)
    if fence:
        try:
            import json

            design.update(json.loads(fence.group(1)))
        except Exception:
            pass
        body = body[: fence.start()] + body[fence.end() :]

    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return resolve_theme(query=query, design=design), body
