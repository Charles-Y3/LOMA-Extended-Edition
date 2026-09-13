# -*- coding: utf-8 -*-
"""Extract translation units from PDF text."""
from __future__ import annotations

import re


def extract_pdf_units(source_path: str) -> list[dict]:
    """Split parsed PDF text into paragraph-level mutation units."""
    from services.file_io import parse_uploaded_file

    raw = parse_uploaded_file(source_path)
    if raw.get("type") == "error":
        return []
    text = (raw.get("content") or "").strip()
    if not text:
        return []

    text = re.sub(r"(?im)^source:\s*[^\n]+\n*", "", text)
    blocks = re.split(r"(?m)^---\s*Page\s+\d+\s*---\s*", text)
    units: list[dict] = []
    idx = 0
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        paras = [p.strip() for p in re.split(r"\n\s*\n", block) if p.strip()]
        if not paras:
            paras = [block]
        for p in paras:
            units.append(
                {
                    "id": f"p{idx}",
                    "text": p,
                    "value": p,
                    "format": "document",
                    "ref": ("pdf_para", idx),
                }
            )
            idx += 1
    return units
