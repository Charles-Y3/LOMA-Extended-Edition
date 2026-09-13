# -*- coding: utf-8 -*-
"""Bundled font selection for poster/diagram text rendering.

PIL's built-in default font is a tiny unstyled bitmap face — unusable for poster-
quality text — and relying on whatever font happens to be installed on the user's OS
is unreliable across Windows/macOS and risks missing CJK glyphs entirely on a machine
without a Chinese language pack. So real font files are bundled in the repo instead
(Google Noto Sans, SIL Open Font License — see ui/assets/fonts/OFL.txt).
"""
from __future__ import annotations

import os
import re
from typing import Any

_FONTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ui", "assets", "fonts"
)

_LATIN_PATH = os.path.join(_FONTS_DIR, "NotoSans-Variable.ttf")
_SC_PATH = os.path.join(_FONTS_DIR, "NotoSansSC-Variable.ttf")
_TC_PATH = os.path.join(_FONTS_DIR, "NotoSansTC-Variable.ttf")

_HAN_RE = re.compile(r"[一-鿿]")

_cache: dict[tuple[str, int, bool], Any] = {}


def _font_path_for_text(text: str) -> str:
    if not _HAN_RE.search(text or ""):
        return _LATIN_PATH if os.path.isfile(_LATIN_PATH) else _fallback_path()
    from pipeline.i18n import get_locale

    locale = get_locale()
    path = _TC_PATH if locale == "zh_tw" else _SC_PATH
    return path if os.path.isfile(path) else _fallback_path()


def _fallback_path() -> str:
    """Any bundled/system font as a last resort, so a missing asset degrades to
    *some* readable text rather than raising."""
    for candidate in (_LATIN_PATH, _SC_PATH, _TC_PATH):
        if os.path.isfile(candidate):
            return candidate
    import glob

    matplotlib_dejavu = glob.glob(
        os.path.join(os.path.dirname(os.path.dirname(os.__file__)), "**", "DejaVuSans.ttf"),
        recursive=True,
    )
    return matplotlib_dejavu[0] if matplotlib_dejavu else ""


def font_for_text(text: str, size: int, *, bold: bool = False):
    """A PIL ImageFont for `text`, picking a CJK-capable face when `text` contains Han
    characters and a Latin face otherwise. Variable fonts (as bundled) support a
    "wght" axis for bold via set_variation_by_axes; falls back gracefully if that
    axis isn't available in a given font build."""
    from PIL import ImageFont

    path = _font_path_for_text(text)
    key = (path, size, bold)
    if key in _cache:
        return _cache[key]
    if not path:
        font = ImageFont.load_default()
        _cache[key] = font
        return font
    font = ImageFont.truetype(path, size)
    if bold:
        try:
            font.set_variation_by_axes([700])
        except Exception:
            pass
    _cache[key] = font
    return font


def wrap_text(text: str, font, max_width: int, draw) -> list[str]:
    """Word-wrap for Latin text (splits on spaces) and character-wrap for CJK text
    (no spaces between words) into lines that fit `max_width` px."""
    text = (text or "").strip()
    if not text:
        return []
    if _HAN_RE.search(text):
        units = list(text)
        sep = ""
    else:
        units = text.split(" ")
        sep = " "

    lines: list[str] = []
    current = ""
    for unit in units:
        candidate = f"{current}{sep}{unit}" if current else unit
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = unit
    if current:
        lines.append(current)
    return lines
