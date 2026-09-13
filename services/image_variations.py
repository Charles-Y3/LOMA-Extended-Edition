# -*- coding: utf-8 -*-
"""Batch variations: "give me a few options" for one image prompt. No new model
code — generate_image() called n times with distinct random seeds — composited
into a single labeled contact-sheet PNG so this stays a one-image deliverable like
every other image path in the app, instead of needing new multi-message chat UX."""
from __future__ import annotations

import os
import random

from services.image_generation import GENERATED_IMAGE_DIR, ImageGenerationResult, unique_output_path

_MIN_VARIATIONS, _MAX_VARIATIONS = 2, 4
_CELL_PAD = 14
_LABEL_H = 34


def generate_image_variations(
    prompt: str,
    n: int,
    *,
    output_dir: str | None = None,
    **generate_kwargs,
) -> list[ImageGenerationResult]:
    """n (clamped 2-4) independent renders of the same prompt, each its own random
    seed. `**generate_kwargs` is forwarded to services.image_generation.generate_image
    (width/height/model_id/quality_mode/...) for every variation."""
    from services.image_generation import generate_image

    n = max(_MIN_VARIATIONS, min(_MAX_VARIATIONS, int(n)))
    out_dir = output_dir or os.path.join(GENERATED_IMAGE_DIR, "variations")
    os.makedirs(out_dir, exist_ok=True)

    results: list[ImageGenerationResult] = []
    for i in range(n):
        seed = random.randint(0, 2**31 - 1)
        path = os.path.join(out_dir, f"variation_{i + 1}_{seed}.png")
        results.append(generate_image(prompt, output_path=path, seed=seed, **generate_kwargs))
    return results


def generate_variations_contact_sheet(
    prompt: str,
    n: int,
    *,
    output_path: str | None = None,
    **generate_kwargs,
) -> ImageGenerationResult:
    """Renders n variations and composites them into one labeled grid PNG."""
    from PIL import Image, ImageDraw

    from services.poster_fonts import font_for_text

    variations = generate_image_variations(prompt, n, **generate_kwargs)
    images = [Image.open(v.path).convert("RGB") for v in variations if os.path.exists(v.path)]
    if not images:
        # Nothing rendered (e.g. deps unavailable) — surface the same "fallback"
        # shape the caller already knows how to handle for a single image.
        return variations[0] if variations else ImageGenerationResult("", prompt, 0, 0, 0, 0, 0.0, "", fallback=True)

    cols = 2
    rows = (len(images) + cols - 1) // cols
    cell_w = max(img.width for img in images)
    cell_h = max(img.height for img in images)

    sheet_w = cols * cell_w + (cols + 1) * _CELL_PAD
    sheet_h = rows * (cell_h + _LABEL_H) + (rows + 1) * _CELL_PAD
    sheet = Image.new("RGB", (sheet_w, sheet_h), (245, 246, 248))
    draw = ImageDraw.Draw(sheet)
    label_font = font_for_text("1", 20, bold=True)

    for i, img in enumerate(images):
        r, c = divmod(i, cols)
        x = _CELL_PAD + c * (cell_w + _CELL_PAD)
        y = _CELL_PAD + r * (cell_h + _LABEL_H + _CELL_PAD)
        # Center smaller-than-cell images (variations can differ slightly in size
        # if quality_mode/model changed between calls, which it doesn't today, but
        # keeps this robust rather than assuming uniform dimensions).
        ox, oy = (cell_w - img.width) // 2, (cell_h - img.height) // 2
        sheet.paste(img, (x + ox, y + oy))
        draw.rectangle([x, y, x + cell_w, y + cell_h], outline=(60, 70, 90), width=2)
        draw.text((x + 8, y + cell_h + 4), str(i + 1), font=label_font, fill=(40, 40, 50))

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(
            os.path.join(GENERATED_IMAGE_DIR, "variations_contact_sheet.png"), "variations"
        )
    sheet.save(output_path)

    return ImageGenerationResult(
        output_path, prompt, 0, sheet.width, sheet.height, 0, 0.0, variations[0].model_id,
    )
