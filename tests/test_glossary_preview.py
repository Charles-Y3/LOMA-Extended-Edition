# -*- coding: utf-8 -*-
"""Glossary preview data tests (no UI)."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from services.formslator.glossary_service import load_config, preview_rows, load_excel, detect_columns, glossary_file_path


def _column_id(label: str) -> str:
    safe = re.sub(r"[^\w]+", "_", str(label).strip()).strip("_")
    return safe or "col"


def test_preview_rows_loaded():
    cfg = load_config()
    assert cfg.get("active_file"), "set active_file in glossary_config.json for this test"
    path = glossary_file_path(cfg["active_file"])
    assert __import__("os").path.isfile(path)

    df, sheets = load_excel(path, cfg.get("sheet"))
    cols = detect_columns(df)
    assert len(df) > 0, "sheet should have rows"
    assert "Terms" in cols or len(cols) >= 2

    all_rows = preview_rows(cfg, "")
    assert len(all_rows) == len(df), f"expected {len(df)} rows, got {len(all_rows)}"

    hits = preview_rows(cfg, "乘法")
    assert len(hits) >= 1
    scores = [int(r.get("_match_score", 0)) for r in hits]
    assert scores == sorted(scores, reverse=True), "search results should be best-match first"

    # Quasar-safe column names (UI fix)
    for c in cols:
        name = _column_id(c)
        assert " " not in name, f"column name must be safe: {c!r} -> {name!r}"


if __name__ == "__main__":
    test_preview_rows_loaded()
    print("OK: glossary preview tests passed")
