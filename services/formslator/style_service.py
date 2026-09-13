# -*- coding: utf-8 -*-
"""Style detection for Formslator."""
from __future__ import annotations

import os
from typing import Any

from docx import Document

from services.formslator.format_engine import extract_content, normalize_size, parse_styles_xml

AUTO_MAP = {
    "C1": "E1",
    "C2a": "E2a",
    "C2b": "E2b",
    "C3a": "E3ab",
    "C3b": "E3ab",
    "C3c": "E3cd",
    "C3d": "E3cd",
    "C4a": "E4ab",
    "C4b": "E4ab",
    "C4c": "E4cd",
    "C4d": "E4cd",
    "C4e": "E4e",
    "C5": "E5",
    "C6": "E6",
    "C7": "E7",
    "页眉": "Header",
    "页脚": "Footer",
}


def _style_prefix(style_name: str) -> str:
    if not style_name:
        return ""
    name = style_name.strip()
    for prefix in sorted(AUTO_MAP.keys(), key=len, reverse=True):
        if name.startswith(prefix):
            return prefix
    return name.split()[0] if name else ""


def _default_cn_en_styles(template_styles: list[str]) -> tuple[str, str]:
    cn = next((s for s in template_styles if s.startswith("C3")), "")
    if not cn:
        cn = next((s for s in template_styles if s.startswith("C1")), "Normal")
    en = next((s for s in template_styles if s.startswith("E3")), "")
    if not en:
        en = next((s for s in template_styles if s.startswith("E1")), "Normal")
    return cn, en


def translation_for_original(orig_style: str, template_styles: list[str]) -> str:
    """Return the template E-style matching a selected C-style (AUTO_MAP rules)."""
    if not orig_style or not template_styles:
        return ""
    name = orig_style.strip()
    prefix = _style_prefix(name)
    if name.startswith("Alt"):
        prefix = "C1"
    elif name.startswith("B"):
        prefix = "C2a"
    target_prefix = AUTO_MAP.get(prefix) or AUTO_MAP.get(_style_prefix(name))
    if not target_prefix:
        return ""
    for ts in template_styles:
        if ts.startswith(target_prefix):
            return ts
    return ""


def _guess_from_signature(sig: str, template_styles: list[str]) -> tuple[str, str]:
    """Guess C/E template styles from a run signature (run_style|font|size)."""
    parts = sig.split("|")
    run_style = parts[0].strip() if parts else ""
    font_name = parts[1].strip() if len(parts) > 1 else ""
    size_str = parts[2].strip() if len(parts) > 2 else ""

    default_cn, default_en = _default_cn_en_styles(template_styles)

    if run_style:
        prefix = _style_prefix(run_style)
        if run_style.startswith("Alt"):
            prefix = "C1"
        elif run_style.startswith("B"):
            prefix = "C2a"
        target_prefix = AUTO_MAP.get(prefix)
        orig_guess = ""
        trans_guess = ""
        if prefix:
            for ts in template_styles:
                if ts.startswith(prefix) or prefix in ts:
                    orig_guess = ts
                    break
        if target_prefix:
            for ts in template_styles:
                if ts.startswith(target_prefix):
                    trans_guess = ts
                    break
        if orig_guess and not trans_guess:
            tp = AUTO_MAP.get(_style_prefix(orig_guess))
            if tp:
                for ts in template_styles:
                    if ts.startswith(tp):
                        trans_guess = ts
                        break
        return orig_guess or default_cn, trans_guess or default_en

    # No run style — infer from font / size
    orig_guess = default_cn
    if "隸書" in font_name or "魏碑" in font_name:
        orig_guess = next((s for s in template_styles if "C3c" in s or "魏碑" in s), default_cn)
    elif "仿宋" in font_name or "楷" in font_name:
        orig_guess = next((s for s in template_styles if s.startswith("C3")), default_cn)

    if size_str in ("18", "20", "22"):
        orig_guess = next((s for s in template_styles if s.startswith("C1")), orig_guess)
    elif size_str in ("16", "17"):
        orig_guess = next((s for s in template_styles if s.startswith("C2")), orig_guess)

    tp = AUTO_MAP.get(_style_prefix(orig_guess), "")
    trans_guess = default_en
    if tp:
        for ts in template_styles:
            if ts.startswith(tp):
                trans_guess = ts
                break
    return orig_guess, trans_guess


def suggest_mapping(
    detected: list[dict[str, Any]],
    template_styles: list[str],
) -> dict[str, tuple[str, str]]:
    """Suggest original/translated style pairs using AUTO_MAP prefix rules."""
    suggestions: dict[str, tuple[str, str]] = {}
    for item in detected:
        sig = item["signature"]
        orig_guess, trans_guess = _guess_from_signature(sig, template_styles)
        if orig_guess or trans_guess:
            suggestions[sig] = (orig_guess, trans_guess)
    return suggestions


def _sample_as_markdown(text: str, run_style: str, run: dict) -> str:
    """Render a short sample with basic markdown from run formatting hints."""
    sample = (text or "").strip()[:120]
    if not sample:
        return ""
    style = (run_style or "").lower()
    if run.get("bold") or "bold" in style or "heading" in style or "title" in style:
        return f"**{sample}**"
    if run.get("italic"):
        return f"*{sample}*"
    return sample


def format_signature_display(sig: str) -> str:
    """Human label for a run signature (omit empty style / ? size noise)."""
    parts = [(p or "").strip() for p in (sig or "").split("|")]
    while parts and not parts[0]:
        parts.pop(0)
    # Drop trailing unknown size when font is present
    if len(parts) >= 2 and parts[-1] in ("", "?"):
        parts = parts[:-1]
    label = " · ".join(p for p in parts if p and p != "?")
    return label or (sig or "").replace("|", " · ").strip(" ·") or "?"


def signature_for_run(
    run: dict,
    para_style: str,
    doc_default_east: str | None = None,
) -> str:
    """Style key for detection/mapping UI (matches saved .txt mapping files)."""
    text = (run.get("text") or "").strip()
    if not text or text.strip().rstrip(".").isdigit():
        return ""
    run_style = run.get("run_style") or para_style
    f_name = run.get("font_name") or doc_default_east or "?"
    size_str = normalize_size(run.get("font_size"))
    return f"{run_style}|{f_name}|{size_str}"


def detect_styles(docx_path: str) -> list[dict[str, Any]]:
    if not docx_path or not os.path.isfile(docx_path):
        return []

    doc = Document(docx_path)
    items = extract_content(doc, docx_path)
    styles_data = parse_styles_xml(docx_path)
    doc_default_east = styles_data.get("document_defaults", {}).get("eastAsia")

    input_signatures: dict[str, dict[str, Any]] = {}
    order_counter = 0

    for it in items:
        para_style = (it.get("style") or "").strip()
        runs = it.get("runs") or []
        full_text = "".join(r.get("text", "") for r in runs).strip()

        for r in runs:
            text = (r.get("text") or "").strip()
            if not text or text.strip().rstrip(".").isdigit():
                continue

            run_style = r.get("run_style") or para_style
            sig = signature_for_run(r, para_style, doc_default_east)
            if not sig:
                continue

            if sig not in input_signatures:
                input_signatures[sig] = {
                    "signature": sig,
                    "display": format_signature_display(sig),
                    "sample_text": full_text[:120] if full_text else text[:120],
                    "sample_markdown": _sample_as_markdown(full_text or text, run_style, r),
                    "count": 1,
                    "first_index": order_counter,
                }
                order_counter += 1
            else:
                input_signatures[sig]["count"] += 1
                if full_text and len(full_text) > len(input_signatures[sig].get("sample_text", "")):
                    input_signatures[sig]["sample_text"] = full_text[:120]
                    input_signatures[sig]["sample_markdown"] = _sample_as_markdown(
                        full_text, run_style, r
                    )

    # Prefer sized signatures over the same font with unknown size ("?").
    sized_fonts: set[str] = set()
    for sig in input_signatures:
        parts = sig.split("|")
        font = parts[1] if len(parts) > 1 else ""
        size = parts[2] if len(parts) > 2 else ""
        if font and size and size != "?":
            sized_fonts.add(font)
    pruned = {
        sig: meta
        for sig, meta in input_signatures.items()
        if not (
            len(sig.split("|")) > 2
            and sig.split("|")[2] == "?"
            and (sig.split("|")[1] in sized_fonts)
        )
    }
    return sorted(pruned.values(), key=lambda e: e.get("first_index", 0))
