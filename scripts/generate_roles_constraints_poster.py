#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Generate LOMA agent roles & output constraints reference poster PNG."""
from __future__ import annotations

import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "generated", "loma_roles_and_constraints_reference.png")

ROLES: list[tuple[str, str]] = [
    ("general_answer", "General conversational answers"),
    ("summarizer", "Summarize into key points"),
    ("translator", "Full translation to target language"),
    ("selective_translator", "Translate only specified spans"),
    ("writer", "Polished user-facing prose"),
    ("editor", "Rewrite for clarity"),
    ("tone_rewriter", "Adjust tone, preserve facts"),
    ("analyser", "Qualitative analysis"),
    ("data_analyst", "Tabular/chart quantitative analysis"),
    ("extractor", "Structured facts & entities"),
    ("outliner", "Plans and outlines"),
    ("synthesizer", "Synthesize workspace context"),
    ("slide_author", "Slide markdown for .pptx"),
    ("deck_planner", "Deck structure JSON"),
    ("spreadsheet_author", "Workbook JSON for .xlsx"),
    ("bullet_formatter", "Concise bullet points"),
    ("image_prompt_author", "Image generation prompts"),
    ("transcriber", "Audio/video transcription"),
    ("sound_script_author", "TTS speakable scripts"),
    ("coder", "Code generation (chat roles)"),
    ("vision_analyst", "Image/visual analysis"),
]

CONSTRAINT_GROUPS: list[tuple[str, list[str]]] = [
    (
        "Chat",
        [
            "chat_default",
            "chat_translation",
            "chat_summary",
            "chat_extract",
            "chat_rewrite",
            "chat_outline",
        ],
    ),
    (
        "Document",
        [
            "document_outline",
            "document_outline_charts",
            "document_markdown",
            "document_markdown_charts",
            "document_synthesis",
            "excerpt_revision",
            "excerpt_translation",
            "excerpt_shorten",
        ],
    ),
    (
        "Mutation",
        [
            "mutation_fragment_map",
            "mutation_selective_translate",
            "mutation_full_translate",
            "mutation_summarize",
            "mutation_rewrite",
            "spreadsheet_mutation_cell_map",
        ],
    ),
    (
        "Presentation",
        [
            "presentation_metadata_json",
            "presentation_narrative_json",
            "presentation_slide_plan_json",
            "presentation_deck_spec",
            "presentation_slide_copy",
            "presentation_markdown",
            "presentation_outline",
        ],
    ),
    (
        "Deliverable / Gen",
        [
            "deliverable_outline",
            "deliverable_synthesis",
            "deliverable_markdown",
            "spreadsheet_markdown",
            "spreadsheet_workbook_spec",
            "sound_script_markdown",
            "research_notes",
            "generic_stub",
        ],
    ),
    (
        "Image",
        [
            "image_prompt",
            "image_context",
            "image_brief",
            "image_prompt_brand",
            "image_prompt_slide",
            "image_prompt_document",
            "image_prompt_standalone",
            "image_action_scene",
            "image_mut_global",
            "image_mut_preserve",
        ],
    ),
    (
        "Media / Software / Compose",
        [
            "transcript_markdown",
            "transcript_summary",
            "transcript_translation",
            "software_plan",
            "software_python",
            "compose_general",
            "compose_coder",
            "compose_vision",
            "compose_extract",
        ],
    ),
]

BG = (15, 18, 28)
PANEL = (22, 28, 42)
BORDER = (55, 70, 100)
TEXT = (220, 228, 240)
MUTED = (140, 155, 175)
ACCENT = (56, 189, 248)
ACCENT2 = (129, 140, 248)
TITLE = (255, 255, 255)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        ("segoeui.ttf", "segoeuib.ttf"),
        ("arial.ttf", "arialbd.ttf"),
        ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ]
    for regular, bold_name in candidates:
        path = bold_name if bold else regular
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_panel(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    title: str,
    *,
    accent: tuple[int, int, int],
) -> None:
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=10, fill=PANEL, outline=BORDER, width=1)
    draw.rectangle((x0, y0, x0 + 5, y1), fill=accent)
    draw.text((x0 + 14, y0 + 10), title, font=_font(15, True), fill=TITLE)


def main() -> str:
    width, height = 1800, 2500
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    draw.text((48, 36), "LOMA", font=_font(34, True), fill=ACCENT)
    draw.text((48, 78), "Agent Roles & Output Constraints", font=_font(22, True), fill=TITLE)
    draw.text(
        (48, 112),
        "Direct pipeline task roles (left) · Output constraint IDs by family (right)",
        font=_font(13),
        fill=MUTED,
    )

    role_x0, role_y0, role_x1, role_y1 = 40, 150, 860, 2440
    cons_x0, cons_y0, cons_x1, cons_y1 = 880, 150, 1760, 2440
    _draw_panel(draw, (role_x0, role_y0, role_x1, role_y1), "Agent Roles (21)", accent=ACCENT)

    y = role_y0 + 44
    id_font = _font(12, True)
    desc_font = _font(11)
    group_font = _font(12, True)
    item_font = _font(11)
    for role_id, desc in ROLES:
        draw.text((role_x0 + 18, y), role_id, font=id_font, fill=ACCENT2)
        wrapped = textwrap.fill(desc, width=52)
        draw.text((role_x0 + 18, y + 16), wrapped, font=desc_font, fill=TEXT)
        y += 16 + 14 * max(1, wrapped.count("\n") + 1) + 6

    _draw_panel(
        draw,
        (cons_x0, cons_y0, cons_x1, cons_y1),
        f"Output Constraints ({sum(len(v) for _, v in CONSTRAINT_GROUPS)})",
        accent=ACCENT2,
    )

    y_left = cons_y0 + 44
    y_right = cons_y0 + 44
    col_w = (cons_x1 - cons_x0 - 48) // 2
    for gi, (group, items) in enumerate(CONSTRAINT_GROUPS):
        is_left = gi % 2 == 0
        gx = cons_x0 + 18 if is_left else cons_x0 + 24 + col_w
        gy = y_left if is_left else y_right
        draw.text((gx, gy), group, font=group_font, fill=ACCENT)
        iy = gy + 20
        for item in items:
            draw.text((gx + 6, iy), f"• {item}", font=item_font, fill=TEXT)
            iy += 16
        block_h = 20 + len(items) * 16 + 12
        if is_left:
            y_left += block_h
        else:
            y_right += block_h

    draw.text(
        (48, height - 36),
        "Generated from pipeline/direct/task_roles.py and pipeline/contracts/*",
        font=_font(10),
        fill=MUTED,
    )

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img.save(OUT, "PNG", optimize=True)
    print(OUT)
    return OUT


if __name__ == "__main__":
    main()
