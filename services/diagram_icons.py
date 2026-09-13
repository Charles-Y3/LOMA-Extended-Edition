# -*- coding: utf-8 -*-
"""A small, fixed set of vector icons drawn directly with PIL primitives (lines,
polygons, arcs) — not an external icon font/library. Deliberately limited: infographic
authors (LLM) pick the closest name from ICON_NAMES rather than free-form matching, and
the caller shows a short "built-in icon set" note (infographic.limitation_icons)
alongside the result so this stays an honest, stated limitation rather than a silent
gap.

Each draw function takes (draw, x0, y0, x1, y1, color) and draws inside that
square-ish bounding box using stroke-only shapes sized relative to the box — callers
are expected to pass a roughly square box."""
from __future__ import annotations

import math


def _pad(x0, y0, x1, y1, frac=0.15):
    w, h = x1 - x0, y1 - y0
    return x0 + w * frac, y0 + h * frac, x1 - w * frac, y1 - h * frac


def _stroke_w(x0, y0, x1, y1) -> int:
    return max(2, int((x1 - x0) * 0.09))


def draw_checkmark(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.18)
    w, h = x1 - x0, y1 - y0
    sw = _stroke_w(x0, y0, x1, y1)
    pts = [(x0, y0 + h * 0.55), (x0 + w * 0.38, y1), (x1, y0 + h * 0.12)]
    draw.line(pts, fill=color, width=sw, joint="curve")


def draw_arrow_up(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1)
    cx = (x0 + x1) / 2
    sw = _stroke_w(x0, y0, x1, y1)
    draw.line([(cx, y1), (cx, y0)], fill=color, width=sw)
    head = (x1 - x0) * 0.32
    draw.line([(cx - head, y0 + head), (cx, y0), (cx + head, y0 + head)], fill=color, width=sw, joint="curve")


def draw_arrow_down(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1)
    cx = (x0 + x1) / 2
    sw = _stroke_w(x0, y0, x1, y1)
    draw.line([(cx, y0), (cx, y1)], fill=color, width=sw)
    head = (x1 - x0) * 0.32
    draw.line([(cx - head, y1 - head), (cx, y1), (cx + head, y1 - head)], fill=color, width=sw, joint="curve")


def draw_gear(draw, x0, y0, x1, y1, color) -> None:
    """Callers render icons onto a white-filled card (see
    services/infographic_generation.py), so the gear's center hole is
    unconditionally punched white — matches the card background it's drawn on."""
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r_outer, r_inner, r_hole = (x1 - x0) * 0.46, (x1 - x0) * 0.33, (x1 - x0) * 0.16
    teeth = 8
    pts = []
    for i in range(teeth * 2):
        angle = math.pi * i / teeth
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=color)
    draw.ellipse([cx - r_hole, cy - r_hole, cx + r_hole, cy + r_hole], fill=(255, 255, 255))


def draw_lightbulb(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.14)
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    sw = _stroke_w(x0, y0, x1, y1)
    bulb_r = w * 0.36
    bulb_cy = y0 + bulb_r
    draw.ellipse([cx - bulb_r, bulb_cy - bulb_r, cx + bulb_r, bulb_cy + bulb_r], outline=color, width=sw)
    draw.line([(cx - w * 0.14, y1 - h * 0.12), (cx + w * 0.14, y1 - h * 0.12)], fill=color, width=sw)
    draw.line([(cx, bulb_cy + bulb_r * 0.7), (cx, y1 - h * 0.18)], fill=color, width=sw)


def draw_bar_chart(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.12)
    w, h = x1 - x0, y1 - y0
    bar_w = w * 0.2
    heights = (0.5, 0.85, 0.65)
    for i, hf in enumerate(heights):
        bx0 = x0 + i * (w / 3) + (w / 3 - bar_w) / 2
        draw.rounded_rectangle([bx0, y1 - h * hf, bx0 + bar_w, y1], radius=bar_w * 0.25, fill=color)


def draw_calendar(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.12)
    w, h = x1 - x0, y1 - y0
    sw = _stroke_w(x0, y0, x1, y1)
    top = y0 + h * 0.16
    draw.rounded_rectangle([x0, top, x1, y1], radius=w * 0.1, outline=color, width=sw)
    draw.line([(x0, top + h * 0.2), (x1, top + h * 0.2)], fill=color, width=sw)
    draw.line([(x0 + w * 0.22, y0), (x0 + w * 0.22, top + h * 0.12)], fill=color, width=sw)
    draw.line([(x1 - w * 0.22, y0), (x1 - w * 0.22, top + h * 0.12)], fill=color, width=sw)


def draw_users(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.14)
    w, h = x1 - x0, y1 - y0
    sw = _stroke_w(x0, y0, x1, y1)
    for dx in (-0.2, 0.2):
        hcx = (x0 + x1) / 2 + dx * w
        hr = w * 0.16
        hcy = y0 + hr * 1.1
        draw.ellipse([hcx - hr, hcy - hr, hcx + hr, hcy + hr], outline=color, width=sw)
        draw.arc([hcx - w * 0.28, y1 - h * 0.55, hcx + w * 0.28, y1 + h * 0.1], start=200, end=340, fill=color, width=sw)


def draw_globe(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1)
    sw = _stroke_w(x0, y0, x1, y1)
    draw.ellipse([x0, y0, x1, y1], outline=color, width=sw)
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    ry = (y1 - y0) / 2
    draw.line([(x0, cy), (x1, cy)], fill=color, width=sw)
    draw.ellipse([cx - (x1 - x0) * 0.18, y0, cx + (x1 - x0) * 0.18, y1], outline=color, width=max(1, sw - 1))
    draw.arc([x0, y0, x1, y1], start=0, end=360, fill=color, width=sw)


def draw_shield(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.14)
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    pts = [
        (cx, y0), (x1, y0 + h * 0.2), (x1, y0 + h * 0.55),
        (cx, y1), (x0, y0 + h * 0.55), (x0, y0 + h * 0.2),
    ]
    draw.polygon(pts, outline=color, width=_stroke_w(x0, y0, x1, y1))


def draw_star(draw, x0, y0, x1, y1, color) -> None:
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r_outer, r_inner = (x1 - x0) * 0.48, (x1 - x0) * 0.2
    pts = []
    for i in range(10):
        angle = -math.pi / 2 + math.pi * i / 5
        r = r_outer if i % 2 == 0 else r_inner
        pts.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(pts, fill=color)


def draw_warning(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.12)
    w, h = x1 - x0, y1 - y0
    cx = (x0 + x1) / 2
    draw.polygon([(cx, y0), (x1, y1), (x0, y1)], outline=color, width=_stroke_w(x0, y0, x1, y1))
    sw = max(2, int(w * 0.1))
    draw.line([(cx, y0 + h * 0.42), (cx, y0 + h * 0.72)], fill=color, width=sw)
    r = w * 0.045
    draw.ellipse([cx - r, y1 - h * 0.16 - r, cx + r, y1 - h * 0.16 + r], fill=color)


def draw_leaf(draw, x0, y0, x1, y1, color) -> None:
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.1)
    sw = _stroke_w(x0, y0, x1, y1)
    draw.arc([x0, y0, x1, y1], start=200, end=20, fill=color, width=sw)
    draw.line([(x0 + (x1 - x0) * 0.15, y1 - (y1 - y0) * 0.15), (x1 - (x1 - x0) * 0.1, y0 + (y1 - y0) * 0.1)], fill=color, width=sw)


def draw_bullet(draw, x0, y0, x1, y1, color) -> None:
    """Fallback for an icon name not in ICON_NAMES: a plain filled circle."""
    x0, y0, x1, y1 = _pad(x0, y0, x1, y1, 0.28)
    draw.ellipse([x0, y0, x1, y1], fill=color)


_ICON_DRAW_FUNCS = {
    "checkmark": draw_checkmark,
    "arrow_up": draw_arrow_up,
    "arrow_down": draw_arrow_down,
    "gear": draw_gear,
    "lightbulb": draw_lightbulb,
    "bar_chart": draw_bar_chart,
    "calendar": draw_calendar,
    "users": draw_users,
    "globe": draw_globe,
    "shield": draw_shield,
    "star": draw_star,
    "warning": draw_warning,
    "leaf": draw_leaf,
}

ICON_NAMES: tuple[str, ...] = tuple(_ICON_DRAW_FUNCS.keys())


def draw_icon(draw, name: str, x0, y0, x1, y1, color) -> None:
    """Draws `name` (from ICON_NAMES) inside the box, or a plain bullet dot if
    `name` isn't recognized — never raises on an unknown/hallucinated icon name."""
    func = _ICON_DRAW_FUNCS.get((name or "").strip().lower(), draw_bullet)
    func(draw, x0, y0, x1, y1, color)
