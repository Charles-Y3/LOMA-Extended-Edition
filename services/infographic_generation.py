# -*- coding: utf-8 -*-
"""Two infographic layouts that don't fit the poster (single visual + caption) or
diagram (boxes + arrows) renderers: a stat/icon grid ("3 things you should know
about X") and a chronological timeline. Both are deterministic PIL layouts — no
diffusion — reusing the same supersampled-rendering, theme, and font machinery as
services/diagram_generation.py and services/poster_generation.py, plus the small
built-in icon set in services/diagram_icons.py.

The icon set is intentionally small and fixed (see diagram_icons.ICON_NAMES) — the
author LLM picks the closest name rather than free-form icon matching, and callers
should show infographic.limitation_icons alongside the result so this stays an
honest, stated limitation."""
from __future__ import annotations

import json
import re

from services.diagram_icons import ICON_NAMES, draw_icon
from services.image_generation import GENERATED_IMAGE_DIR, ImageGenerationResult, unique_output_path

_SCALE = 3
_ICON_NAMES_HINT = ", ".join(ICON_NAMES)

_STAT_GRID_AUTHOR_SYSTEM = f"""You are LOMA's Stat Grid Author. Return ONLY valid JSON (no markdown fences).

Given the user's "key facts / stats / quick facts" infographic request, produce:
{{
  "title": "short title, in the SAME language as the user's request",
  "cards": [
    {{"icon": "one of: {_ICON_NAMES_HINT}", "stat": "a short bold number/phrase (1-5 words)", "label": "a short supporting phrase (2-8 words)"}}
  ]
}}

Rules:
- 3 to 6 cards. Use ONLY facts/numbers directly supported by the grounding
  material given below — never invent, estimate, or round a stat that isn't
  stated there.
- icon must be exactly one of the listed names — pick the closest match, never a
  name outside that list.
- stat/label must be in the user's own language, not translated to English.
"""

_TIMELINE_AUTHOR_SYSTEM = """You are LOMA's Timeline Author. Return ONLY valid JSON (no markdown fences).

Given the user's "timeline / history / milestones" infographic request, produce:
{
  "title": "short title, in the SAME language as the user's request",
  "events": [
    {"date": "short date/label (e.g. a year, or a phase name)", "title": "short event title", "description": "one short phrase, optional"}
  ]
}

Rules:
- 3 to 7 events, in chronological order.
- date/title/description must be in the user's own language, not translated to English.
- Use ONLY events/dates directly supported by the grounding material given below —
  never invent a date or event that isn't stated there.
"""


_COMPARISON_AUTHOR_SYSTEM = """You are LOMA's Comparison Author. Return ONLY valid JSON (no markdown fences).

Given the user's "compare X vs Y" infographic request, produce:
{
  "title": "short title, in the SAME language as the user's request",
  "attributes": ["short attribute name", "..."],
  "items": [
    {"name": "short item name", "values": ["value for attribute 1", "value for attribute 2", "..."]}
  ]
}

Rules:
- 2 to 4 items being compared, 3 to 6 attributes.
- Every item's "values" list must have exactly one entry per attribute, in the
  same order as "attributes".
- Use ONLY real figures/facts from the grounding material given below — every
  value must be directly supported by it. Never invent, estimate, or round a
  number or claim that isn't stated there.
- title/attributes/values must be in the user's own language, not translated to
  English.
"""


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


def _stem_for(user_query: str, fallback: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (user_query or fallback).lower())[:40].strip("_") or fallback


def _with_context(user_query: str, context: str) -> str:
    """`context`, if given, is grounding material (an attached-source excerpt or
    web search result) the infographic's facts should be based on — see
    pipeline/base/grounding.py."""
    if not context:
        return user_query
    return (
        f"{user_query}\n\nGround your facts in this material — do not state a "
        f"number or date that isn't supported by it:\n{context}"
    )


# ---------------------------------------------------------------------------
# Stat / icon grid
# ---------------------------------------------------------------------------

def _author_stat_grid(user_query: str, prof: dict, model: str, context: str = "") -> dict:
    if not (context or "").strip():
        # No attached-source excerpt or web search result to draw real stats from —
        # refuse rather than let the model invent plausible-looking numbers (see
        # marker_visual._try_infographic's caller, which falls back to a plain
        # qualitative photo on any exception here instead of rendering fabricated
        # facts). Mirrors _author_chart's / _author_comparison's same guarantee.
        raise ValueError("No grounding material available — refusing to fabricate stat cards.")

    from pipeline.capability_runtime.chat_runner import generate_text_sync

    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _STAT_GRID_AUTHOR_SYSTEM},
            {"role": "user", "content": _with_context(user_query, context)},
        ],
        disable_thinking=True,
    )
    data = _parse_json(raw) or {}
    cards = []
    for c in (data.get("cards") or [])[:6]:
        if not isinstance(c, dict):
            continue
        stat = str(c.get("stat") or "").strip()
        label = str(c.get("label") or "").strip()
        if not stat and not label:
            continue
        cards.append({"icon": str(c.get("icon") or "").strip(), "stat": stat, "label": label})
    if not cards:
        cards = [{"icon": "star", "stat": user_query.strip()[:24] or "Fact", "label": ""}]
    return {"title": str(data.get("title") or "").strip(), "cards": cards}


def generate_stat_grid(
    user_query: str, *, prof: dict, model: str, output_path: str | None = None, context: str = "",
) -> ImageGenerationResult:
    from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

    data = _author_stat_grid(user_query, prof, model, context)
    theme = resolve_theme(query=user_query, design={"palette": infer_palette_from_query(user_query)})

    import os

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(_stem_for(user_query, "stat_grid") + ".png", "infographic")

    _render_stat_grid(data["title"], data["cards"], theme, output_path)

    from PIL import Image

    with Image.open(output_path) as img:
        w, h = img.size
    return ImageGenerationResult(output_path, user_query, 0, w, h, 0, 0.0, "infographic-render")


def _render_stat_grid(title: str, cards: list[dict], theme, output_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    from services.poster_fonts import font_for_text, wrap_text

    S = _SCALE
    n = len(cards)
    cols = 3 if n >= 5 else (2 if n >= 2 else 1)
    rows = (n + cols - 1) // cols

    margin, gap = 60, 40
    title_h = 100 if title else 20
    card_w, card_h = 320, 220
    icon_box = 64

    canvas_w = margin * 2 + cols * card_w + (cols - 1) * gap
    canvas_h = margin * 2 + title_h + rows * card_h + max(0, rows - 1) * gap

    bg = tuple(theme.background_rgb)
    primary = tuple(theme.primary_rgb)
    accent = tuple(theme.accent_rgb)
    title_color = tuple(theme.title_rgb)
    body_color = tuple(theme.body_rgb)

    img = Image.new("RGB", (canvas_w * S, canvas_h * S), bg)
    draw = ImageDraw.Draw(img)

    if title:
        title_font = font_for_text(title, 36 * S, bold=True)
        tw = draw.textbbox((0, 0), title, font=title_font)[2]
        draw.text(((canvas_w * S - tw) / 2, (margin // 2) * S), title, font=title_font, fill=title_color)

    card_boxes = []
    for i in range(n):
        r, c = divmod(i, cols)
        row_n = min(cols, n - r * cols)
        row_w = row_n * card_w + (row_n - 1) * gap
        row_x0 = (canvas_w - row_w) / 2
        x0 = row_x0 + c * (card_w + gap)
        y0 = margin + title_h + r * (card_h + gap)
        card_boxes.append((x0, y0, x0 + card_w, y0 + card_h))

    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    offset = 6 * S
    for (x0, y0, x1, y1) in card_boxes:
        shadow_draw.rounded_rectangle(
            [x0 * S + offset, y0 * S + offset, x1 * S + offset, y1 * S + offset],
            radius=20 * S, fill=(0, 0, 0, 80),
        )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5 * S))
    img = Image.alpha_composite(img.convert("RGBA"), shadow_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    for card, (x0, y0, x1, y1) in zip(cards, card_boxes):
        X0, Y0, X1, Y1 = x0 * S, y0 * S, x1 * S, y1 * S
        draw.rounded_rectangle([X0, Y0, X1, Y1], radius=20 * S, fill=(255, 255, 255), outline=primary, width=3 * S)

        cx = (X0 + X1) / 2
        icon_x0, icon_y0 = cx - icon_box / 2 * S, Y0 + 22 * S
        draw_icon(draw, card["icon"], icon_x0, icon_y0, icon_x0 + icon_box * S, icon_y0 + icon_box * S, primary)

        stat_font = font_for_text(card["stat"], 30 * S, bold=True)
        stat_lines = wrap_text(card["stat"], stat_font, (card_w - 40) * S, draw)[:2]
        sy = icon_y0 + icon_box * S + 14 * S
        for line in stat_lines:
            lw = draw.textbbox((0, 0), line, font=stat_font)[2]
            draw.text((cx - lw / 2, sy), line, font=stat_font, fill=accent)
            sy += 34 * S

        if card["label"]:
            label_font = font_for_text(card["label"], 16 * S)
            label_lines = wrap_text(card["label"], label_font, (card_w - 40) * S, draw)[:2]
            for line in label_lines:
                lw = draw.textbbox((0, 0), line, font=label_font)[2]
                draw.text((cx - lw / 2, sy), line, font=label_font, fill=body_color)
                sy += 22 * S

    img = img.resize((canvas_w, canvas_h), Image.LANCZOS)
    img.save(output_path)


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

def _author_timeline(user_query: str, prof: dict, model: str, context: str = "") -> dict:
    if not (context or "").strip():
        raise ValueError("No grounding material available — refusing to fabricate timeline events.")

    from pipeline.capability_runtime.chat_runner import generate_text_sync

    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _TIMELINE_AUTHOR_SYSTEM},
            {"role": "user", "content": _with_context(user_query, context)},
        ],
        disable_thinking=True,
    )
    data = _parse_json(raw) or {}
    events = []
    for e in (data.get("events") or [])[:7]:
        if not isinstance(e, dict):
            continue
        title = str(e.get("title") or "").strip()
        if not title:
            continue
        events.append({
            "date": str(e.get("date") or "").strip(),
            "title": title,
            "description": str(e.get("description") or "").strip(),
        })
    if not events:
        events = [{"date": "", "title": user_query.strip()[:24] or "Event", "description": ""}]
    return {"title": str(data.get("title") or "").strip(), "events": events}


def generate_timeline(
    user_query: str, *, prof: dict, model: str, output_path: str | None = None, context: str = "",
) -> ImageGenerationResult:
    from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

    data = _author_timeline(user_query, prof, model, context)
    theme = resolve_theme(query=user_query, design={"palette": infer_palette_from_query(user_query)})

    import os

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(_stem_for(user_query, "timeline") + ".png", "infographic")

    _render_timeline(data["title"], data["events"], theme, output_path)

    from PIL import Image

    with Image.open(output_path) as img:
        w, h = img.size
    return ImageGenerationResult(output_path, user_query, 0, w, h, 0, 0.0, "infographic-render")


def _render_timeline(title: str, events: list[dict], theme, output_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    from services.poster_fonts import font_for_text, wrap_text

    S = _SCALE
    n = len(events)
    margin = 60
    title_h = 100 if title else 20
    card_w = 420
    card_pad = 24
    spine_gap = 50  # distance from spine to each card's inner edge

    bg = tuple(theme.background_rgb)
    primary = tuple(theme.primary_rgb)
    accent = tuple(theme.accent_rgb)
    title_color = tuple(theme.title_rgb)
    body_color = tuple(theme.body_rgb)

    probe_img = Image.new("RGB", (10, 10))
    probe_draw = ImageDraw.Draw(probe_img)

    def _card_height(ev: dict) -> int:
        h = card_pad * 2
        if ev["date"]:
            h += 26
        title_font = font_for_text(ev["title"], 20, bold=True)
        lines = wrap_text(ev["title"], title_font, card_w - card_pad * 2, probe_draw)
        h += min(len(lines), 2) * 26
        if ev["description"]:
            desc_font = font_for_text(ev["description"], 14)
            dlines = wrap_text(ev["description"], desc_font, card_w - card_pad * 2, probe_draw)
            h += min(len(dlines), 3) * 18
        return max(90, h)

    card_heights = [_card_height(e) for e in events]
    row_gap = 50
    canvas_w = margin * 2 + spine_gap * 2 + card_w * 2 + 40
    spine_x = canvas_w / 2
    total_h = margin * 2 + title_h + sum(card_heights) + max(0, n - 1) * row_gap
    canvas_h = total_h

    img = Image.new("RGB", (canvas_w * S, canvas_h * S), bg)
    draw = ImageDraw.Draw(img)

    if title:
        title_font = font_for_text(title, 36 * S, bold=True)
        tw = draw.textbbox((0, 0), title, font=title_font)[2]
        draw.text(((canvas_w * S - tw) / 2, (margin // 2) * S), title, font=title_font, fill=title_color)

    spine_top = (margin + title_h) * S
    spine_bottom = canvas_h * S - margin * S
    draw.line([(spine_x * S, spine_top), (spine_x * S, spine_bottom)], fill=primary, width=3 * S)

    y = margin + title_h
    card_boxes = []
    for i, ev in enumerate(events):
        h = card_heights[i]
        left_side = i % 2 == 0
        if left_side:
            x1 = spine_x - spine_gap
            x0 = x1 - card_w
        else:
            x0 = spine_x + spine_gap
            x1 = x0 + card_w
        cy = y + h / 2
        card_boxes.append((x0, y, x1, y + h, cy, left_side))
        y += h + row_gap

    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    offset = 6 * S
    for (x0, y0, x1, y1, cy, left_side) in card_boxes:
        shadow_draw.rounded_rectangle(
            [x0 * S + offset, y0 * S + offset, x1 * S + offset, y1 * S + offset],
            radius=16 * S, fill=(0, 0, 0, 80),
        )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5 * S))
    img = Image.alpha_composite(img.convert("RGBA"), shadow_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    for ev, (x0, y0, x1, y1, cy, left_side) in zip(events, card_boxes):
        X0, Y0, X1, Y1 = x0 * S, y0 * S, x1 * S, y1 * S
        CY = cy * S

        dot_r = 10 * S
        draw.ellipse([spine_x * S - dot_r, CY - dot_r, spine_x * S + dot_r, CY + dot_r], fill=primary)
        connector_x0 = X1 if left_side else spine_x * S
        connector_x1 = spine_x * S if left_side else X0
        draw.line([(connector_x0, CY), (connector_x1, CY)], fill=primary, width=3 * S)

        draw.rounded_rectangle([X0, Y0, X1, Y1], radius=16 * S, fill=(255, 255, 255), outline=primary, width=3 * S)

        tx = X0 + card_pad * S
        ty = Y0 + card_pad * S
        text_w = (card_w - card_pad * 2) * S

        if ev["date"]:
            date_font = font_for_text(ev["date"], 15 * S, bold=True)
            draw.text((tx, ty), ev["date"], font=date_font, fill=accent)
            ty += 26 * S

        title_font = font_for_text(ev["title"], 20 * S, bold=True)
        for line in wrap_text(ev["title"], title_font, text_w, draw)[:2]:
            draw.text((tx, ty), line, font=title_font, fill=title_color)
            ty += 26 * S

        if ev["description"]:
            desc_font = font_for_text(ev["description"], 14 * S)
            for line in wrap_text(ev["description"], desc_font, text_w, draw)[:3]:
                draw.text((tx, ty), line, font=desc_font, fill=body_color)
                ty += 18 * S

    img = img.resize((canvas_w, canvas_h), Image.LANCZOS)
    img.save(output_path)


# ---------------------------------------------------------------------------
# Comparison table
# ---------------------------------------------------------------------------

def _author_comparison(user_query: str, prof: dict, model: str, context: str = "") -> dict:
    if not (context or "").strip():
        raise ValueError("No grounding material available — refusing to fabricate comparison values.")

    from pipeline.capability_runtime.chat_runner import generate_text_sync

    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _COMPARISON_AUTHOR_SYSTEM},
            {"role": "user", "content": _with_context(user_query, context)},
        ],
        disable_thinking=True,
    )
    data = _parse_json(raw) or {}
    attributes = [str(a).strip() for a in (data.get("attributes") or []) if str(a).strip()][:6]
    items = []
    for it in (data.get("items") or [])[:4]:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        raw_values = [str(v).strip() for v in (it.get("values") or [])]
        # Pad/truncate to exactly len(attributes) so rendering never indexes out
        # of range if the LLM under/over-produced values for one item.
        values = (raw_values + [""] * len(attributes))[: len(attributes)]
        items.append({"name": name, "values": values})

    if len(items) < 2 or not attributes:
        # Not enough to compare — fall back to a single-card placeholder rather
        # than rendering a broken/empty table.
        attributes = attributes or ["Note"]
        items = items or [{"name": user_query.strip()[:24] or "Item", "values": ["No data"]}]

    return {"title": str(data.get("title") or "").strip(), "attributes": attributes, "items": items}


def generate_comparison(
    user_query: str, *, prof: dict, model: str, output_path: str | None = None, context: str = "",
) -> ImageGenerationResult:
    from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

    data = _author_comparison(user_query, prof, model, context)
    theme = resolve_theme(query=user_query, design={"palette": infer_palette_from_query(user_query)})

    import os

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(_stem_for(user_query, "comparison") + ".png", "infographic")

    _render_comparison(data["title"], data["attributes"], data["items"], theme, output_path)

    from PIL import Image

    with Image.open(output_path) as img:
        w, h = img.size
    return ImageGenerationResult(output_path, user_query, 0, w, h, 0, 0.0, "infographic-render")


def _render_comparison(title: str, attributes: list[str], items: list[dict], theme, output_path: str) -> None:
    from PIL import Image, ImageDraw, ImageFilter

    from services.poster_fonts import font_for_text, wrap_text

    S = _SCALE
    n = len(items)
    top_margin, gap = 60, 30
    title_h = 100 if title else 20
    card_w = 300
    row_h = 74
    header_h = 70
    pad = 20

    bg = tuple(theme.background_rgb)
    primary = tuple(theme.primary_rgb)
    accent = tuple(theme.accent_rgb)
    title_color = tuple(theme.title_rgb)
    body_color = tuple(theme.body_rgb)

    # The row-label column's width is content-dependent (e.g. "Battery Life" is
    # much wider than "Price") — measure it first rather than assuming a fixed
    # margin fits, or a long label clips off the left edge of the canvas.
    probe_img = Image.new("RGB", (10, 10))
    probe_draw = ImageDraw.Draw(probe_img)
    label_col_w = 0
    for attr in attributes:
        label_font = font_for_text(attr, 15, bold=True)
        label_col_w = max(label_col_w, probe_draw.textbbox((0, 0), attr, font=label_font)[2])
    left_margin = 30 + label_col_w + 20  # canvas edge + label text + gap to first card

    card_h = header_h + len(attributes) * row_h + pad
    canvas_w = left_margin + n * card_w + max(0, n - 1) * gap + top_margin
    canvas_h = top_margin * 2 + title_h + card_h

    img = Image.new("RGB", (canvas_w * S, canvas_h * S), bg)
    draw = ImageDraw.Draw(img)

    if title:
        title_font = font_for_text(title, 36 * S, bold=True)
        tw = draw.textbbox((0, 0), title, font=title_font)[2]
        draw.text(((canvas_w * S - tw) / 2, (top_margin // 2) * S), title, font=title_font, fill=title_color)

    card_boxes = []
    for i in range(n):
        x0 = left_margin + i * (card_w + gap)
        y0 = top_margin + title_h
        card_boxes.append((x0, y0, x0 + card_w, y0 + card_h))

    shadow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow_layer)
    offset = 6 * S
    for (x0, y0, x1, y1) in card_boxes:
        shadow_draw.rounded_rectangle(
            [x0 * S + offset, y0 * S + offset, x1 * S + offset, y1 * S + offset],
            radius=20 * S, fill=(0, 0, 0, 80),
        )
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=5 * S))
    img = Image.alpha_composite(img.convert("RGBA"), shadow_layer).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Attribute row labels are drawn once, left of the first card, so they're not
    # repeated inside every column — reads like a real comparison table's row axis.
    label_x = card_boxes[0][0] * S - 4 * S
    for r, attr in enumerate(attributes):
        ly = (card_boxes[0][1] + header_h + r * row_h + row_h / 2) * S
        label_font = font_for_text(attr, 15 * S, bold=True)
        lw = draw.textbbox((0, 0), attr, font=label_font)[2]
        draw.text((label_x - lw, ly), attr, font=label_font, fill=body_color, anchor="lm")

    for item, (x0, y0, x1, y1) in zip(items, card_boxes):
        X0, Y0, X1, Y1 = x0 * S, y0 * S, x1 * S, y1 * S
        draw.rounded_rectangle([X0, Y0, X1, Y1], radius=20 * S, fill=(255, 255, 255), outline=primary, width=3 * S)

        cx = (X0 + X1) / 2
        name_font = font_for_text(item["name"], 22 * S, bold=True)
        name_lines = wrap_text(item["name"], name_font, (card_w - 30) * S, draw)[:2]
        ny = Y0 + 16 * S
        for line in name_lines:
            lw = draw.textbbox((0, 0), line, font=name_font)[2]
            draw.text((cx - lw / 2, ny), line, font=name_font, fill=accent)
            ny += 26 * S

        header_bottom = Y0 + header_h * S
        draw.line([(X0 + 16 * S, header_bottom), (X1 - 16 * S, header_bottom)], fill=primary, width=1 * S)

        for r, value in enumerate(item["values"]):
            row_top = header_bottom + r * row_h * S
            row_cy = row_top + (row_h * S) / 2
            if r > 0:
                draw.line(
                    [(X0 + 16 * S, row_top), (X1 - 16 * S, row_top)], fill=tuple(
                        int(a * 0.85 + b * 0.15) for a, b in zip(bg, primary)
                    ), width=1 * S,
                )
            value_font = font_for_text(value or "-", 17 * S, bold=True)
            value_lines = wrap_text(value or "-", value_font, (card_w - 30) * S, draw)[:2]
            block_h = len(value_lines) * 22 * S
            vy = row_cy - block_h / 2
            for line in value_lines:
                lw = draw.textbbox((0, 0), line, font=value_font)[2]
                draw.text((cx - lw / 2, vy), line, font=value_font, fill=title_color)
                vy += 22 * S

    img = img.resize((canvas_w, canvas_h), Image.LANCZOS)
    img.save(output_path)
