# -*- coding: utf-8 -*-
"""Capture and restore Office run-level font styling during mutation apply."""
from __future__ import annotations


def _rgb_triplet(value) -> tuple[int, int, int] | None:
    if value is None:
        return None
    try:
        if isinstance(value, (tuple, list)) and len(value) >= 3:
            return (int(value[0]), int(value[1]), int(value[2]))
        from docx.shared import RGBColor as DocxRGB

        if isinstance(value, DocxRGB):
            return (int(value[0]), int(value[1]), int(value[2]))
        from pptx.dml.color import RGBColor as PptxRGB

        if isinstance(value, PptxRGB):
            return (int(value[0]), int(value[1]), int(value[2]))
    except Exception:
        pass
    return None


def snapshot_run_font(run) -> dict:
    font = run.font
    snap: dict = {"bold": font.bold, "italic": font.italic, "underline": font.underline}
    try:
        if font.size is not None:
            snap["size"] = font.size
        if font.color and font.color.rgb:
            triplet = _rgb_triplet(font.color.rgb)
            if triplet:
                snap["color_rgb"] = triplet
        if font.name:
            snap["name"] = font.name
    except Exception:
        pass
    return snap


def apply_run_font_snapshot(run, snap: dict) -> None:
    if not snap:
        return
    font = run.font
    try:
        if "bold" in snap:
            font.bold = snap["bold"]
        if "italic" in snap:
            font.italic = snap["italic"]
        if "underline" in snap:
            font.underline = snap["underline"]
        if snap.get("size") is not None:
            font.size = snap["size"]
        triplet = _rgb_triplet(snap.get("color_rgb"))
        if triplet is not None:
            try:
                from pptx.dml.color import RGBColor as PptxRGB

                font.color.rgb = PptxRGB(*triplet)
            except Exception:
                try:
                    from docx.shared import RGBColor as DocxRGB

                    font.color.rgb = DocxRGB(*triplet)
                except Exception:
                    pass
        if snap.get("name"):
            font.name = snap["name"]
    except Exception:
        pass


def paragraph_run_styles(paragraph, *, limit: int = 4) -> list[dict]:
    """Sample run styles from a paragraph (read-only metadata for extraction)."""
    out: list[dict] = []
    for run in paragraph.runs:
        if not (run.text or "").strip():
            continue
        out.append(snapshot_run_font(run))
        if len(out) >= limit:
            break
    return out
