# -*- coding: utf-8 -*-
"""LOMA brand assets (icon paths and static URLs)."""
from __future__ import annotations

import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(_ROOT, "ui", "assets")
_ICON_DIR = os.path.join(ASSETS_DIR, "loma_icon")
_LEGACY_ICON_DIR = os.path.join(ASSETS_DIR, "loma_icon")


def _resolve_loma_icon() -> str:
    """Prefer ui/assets/loma_icon/, then loma_icon.png, then legacy loma_icon/LOMO paths."""
    for candidate in (
        os.path.join(ASSETS_DIR, "loma_icon.png"),
        os.path.join(_ICON_DIR, "icon.png"),
        os.path.join(_ICON_DIR, "loma_icon.png"),
        os.path.join(ASSETS_DIR, "loma_icon.png"),
        os.path.join(_LEGACY_ICON_DIR, "icon.png"),
        os.path.join(ASSETS_DIR, "LOMO icon.png"),
    ):
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(os.path.join(_ICON_DIR, "icon.png"))


def _resolve_favicon() -> str:
    ico = os.path.join(ASSETS_DIR, "favicon.ico")
    if os.path.isfile(ico):
        return os.path.abspath(ico)
    return _resolve_loma_icon()


LOMA_ICON_FILE = _resolve_loma_icon()
LOMA_FAVICON_FILE = _resolve_favicon()
_rel = os.path.relpath(LOMA_ICON_FILE, ASSETS_DIR).replace("\\", "/")
LOMA_ICON_URL = f"/loma-brand/{_rel}"


def favicon_for_nicegui() -> str:
    """Embed icon as data URL — avoids /favicon.ico cache and NiceGUI default icon."""
    import base64

    path = LOMA_ICON_FILE if os.path.isfile(LOMA_ICON_FILE) else LOMA_FAVICON_FILE
    if not os.path.isfile(path):
        return LOMA_ICON_URL
    with open(path, "rb") as f:
        raw = f.read()
    mime = "image/png" if path.lower().endswith(".png") else "image/x-icon"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
