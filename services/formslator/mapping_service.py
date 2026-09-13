# -*- coding: utf-8 -*-
"""Style mapping persistence for Formslator."""
from __future__ import annotations

import os
from typing import Any

from services.formslator.paths import MAPPING_DIR, ensure_dirs

HEADER_TEMPLATE = "TEMPLATE:"
HEADER_ORIGINAL_COLUMN = "ORIGINAL_COLUMN:"


def save_mapping(
    input_basename: str,
    template_name: str,
    mapping_dict: dict[str, tuple[str, str]],
    original_column: str = "left",
) -> str:
    ensure_dirs()
    template_suffix = os.path.splitext(template_name)[0] if template_name else "Default"
    filename = f"{input_basename}_style_mapping_{template_suffix}.txt"
    path = os.path.join(MAPPING_DIR, filename)

    with open(path, "w", encoding="utf-8") as f:
        f.write(f"{HEADER_TEMPLATE}|||{template_name}\n")
        f.write(f"{HEADER_ORIGINAL_COLUMN}|||{original_column}\n")
        for sig, (orig_style, trans_style) in mapping_dict.items():
            f.write(f"{sig}|||{orig_style}|||{trans_style}\n")
    return path


def load_mapping_for_file(input_path: str) -> tuple[dict[str, tuple[str, str]], str | None, dict[str, Any]]:
    ensure_dirs()
    mapping: dict[str, tuple[str, str]] = {}
    template_name: str | None = None
    metadata: dict[str, Any] = {"original_column": "left", "mapping_path": None}

    if not input_path:
        return mapping, template_name, metadata

    base = os.path.splitext(os.path.basename(input_path))[0]
    candidates: list[tuple[str, float]] = []
    for name in os.listdir(MAPPING_DIR):
        lower = name.lower()
        if lower.startswith(base.lower()) and "_style_mapping_" in lower and lower.endswith(".txt"):
            full = os.path.join(MAPPING_DIR, name)
            candidates.append((full, os.path.getmtime(full)))

    candidates.sort(key=lambda x: x[1], reverse=True)
    if not candidates:
        return mapping, template_name, metadata

    target_path = candidates[0][0]
    metadata["mapping_path"] = target_path

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if line.startswith(f"{HEADER_TEMPLATE}|||"):
                    template_name = line.split("|||", 1)[1]
                    continue
                if line.startswith(f"{HEADER_ORIGINAL_COLUMN}|||"):
                    metadata["original_column"] = line.split("|||", 1)[1].strip() or "left"
                    continue
                parts = line.split("|||")
                if len(parts) == 3:
                    sig, orig_style, trans_style = parts
                    mapping[sig] = (orig_style.strip(), trans_style.strip())
    except Exception:
        return {}, None, metadata

    return mapping, template_name, metadata


def list_mappings_for_file(input_path: str) -> list[str]:
    ensure_dirs()
    base = os.path.splitext(os.path.basename(input_path))[0]
    out: list[str] = []
    for name in os.listdir(MAPPING_DIR):
        lower = name.lower()
        if lower.startswith(base.lower()) and "_style_mapping_" in lower and lower.endswith(".txt"):
            out.append(name)
    return sorted(out)
