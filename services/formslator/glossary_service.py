# -*- coding: utf-8 -*-
"""Glossary loading and configuration for Formslator."""
from __future__ import annotations

import json
import os
import re
from typing import Any

import pandas as pd

from services.formslator.paths import GLOSSARY_CONFIG_FILE, GLOSSARY_DIR, ensure_dirs

DEFAULT_GLOSSARY_CONFIG: dict[str, Any] = {
    "active_file": "",
    "sheet": "",
    "term_column": "",
    "translation_column": "",
    "tooltip_columns": [],
    "url_template": (
        "https://www.mdbg.net/chinese/dictionary?page=worddict&wdrst=0&wdqb={term}"
    ),
}


def load_config() -> dict[str, Any]:
    ensure_dirs()
    if not os.path.isfile(GLOSSARY_CONFIG_FILE):
        return dict(DEFAULT_GLOSSARY_CONFIG)
    try:
        with open(GLOSSARY_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_GLOSSARY_CONFIG)
        merged.update(data if isinstance(data, dict) else {})
        return merged
    except Exception:
        return dict(DEFAULT_GLOSSARY_CONFIG)


def save_config(config: dict[str, Any]) -> None:
    ensure_dirs()
    merged = dict(DEFAULT_GLOSSARY_CONFIG)
    merged.update(config)
    with open(GLOSSARY_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)


def list_glossary_files() -> list[str]:
    ensure_dirs()
    return sorted(
        f for f in os.listdir(GLOSSARY_DIR)
        if f.lower().endswith((".xlsx", ".xls")) and f != "glossary_config.json"
    )


def glossary_file_path(filename: str) -> str:
    return os.path.join(GLOSSARY_DIR, filename)


def load_excel(path: str, sheet: str | None = None) -> tuple[pd.DataFrame, list[str]]:
    xl = pd.ExcelFile(path)
    sheets = xl.sheet_names
    use_sheet = sheet if sheet and sheet in sheets else sheets[0]
    df = pd.read_excel(path, sheet_name=use_sheet).fillna("")
    return df, sheets


def detect_columns(df: pd.DataFrame) -> list[str]:
    return [str(c) for c in df.columns]


def _normalize_translation(tr: str) -> str:
    tr = re.sub(r"\([^)]*\)", "", tr)
    if ";" in tr:
        tr = tr.split(";", 1)[0]
    return re.sub(r"\s+", " ", tr).strip()


def build_glossary(config: dict[str, Any] | None = None) -> dict[str, str]:
    cfg = config or load_config()
    active = cfg.get("active_file") or ""
    if not active:
        return {}

    path = glossary_file_path(active)
    if not os.path.isfile(path):
        return {}

    term_col = cfg.get("term_column") or ""
    trans_col = cfg.get("translation_column") or ""
    if not term_col or not trans_col:
        return {}

    try:
        df, _ = load_excel(path, cfg.get("sheet") or None)
    except Exception:
        return {}

    if term_col not in df.columns or trans_col not in df.columns:
        return {}

    glossary: dict[str, str] = {}
    for _, row in df.iterrows():
        term = str(row.get(term_col, "")).strip()
        tr = _normalize_translation(str(row.get(trans_col, "")).strip())
        if term and term.lower() != "nan" and tr:
            glossary[term] = tr

    return dict(sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True))


def build_hyperlink_index(config: dict[str, Any] | None = None) -> dict[str, str]:
    """Return term -> tooltip text for hyperlink insertion."""
    cfg = config or load_config()
    active = cfg.get("active_file") or ""
    if not active:
        return {}

    path = glossary_file_path(active)
    if not os.path.isfile(path):
        return {}

    term_col = cfg.get("term_column") or ""
    trans_col = cfg.get("translation_column") or ""
    tooltip_cols = cfg.get("tooltip_columns") or []

    try:
        df, _ = load_excel(path, cfg.get("sheet") or None)
    except Exception:
        return {}

    if term_col not in df.columns:
        return {}

    index: dict[str, str] = {}
    for _, row in df.iterrows():
        term = str(row.get(term_col, "")).strip()
        if not term or term.lower() == "nan":
            continue
        parts: list[str] = []
        cols_to_show = tooltip_cols if tooltip_cols else ([trans_col] if trans_col in df.columns else [])
        for col in cols_to_show:
            if col not in df.columns:
                continue
            val = str(row.get(col, "")).strip()
            if not val or val.lower() == "nan":
                continue
            if col == trans_col:
                parts.append(f"Translation: {val}")
            else:
                parts.append(f"{col}: {val}")
        if parts:
            index[term] = "\n".join(parts)
    return index


def _search_match_score(term_text: str, query: str, record: dict[str, str], term_col: str) -> int:
    """Higher score = better match (for sorting preview / search results)."""
    term = (term_text or "").lower()
    q = (query or "").strip().lower()
    if not q:
        return 1
    if term == q:
        return 10000
    if term.startswith(q):
        return 9000 - min(len(term), 500)
    if q in term:
        return 8000 - min(len(term), 500)
    words = [w for w in re.split(r"\s+", q) if w]
    if words and all(w in term for w in words):
        return 7000 - min(len(term), 500)
    hay = " ".join(v.lower() for k, v in record.items() if k not in ("_row", "_term", "_translation"))
    if q in hay:
        return 1000
    for w in words:
        if w in term:
            return 500
    return 0


def preview_rows(
    config: dict[str, Any] | None = None,
    search: str = "",
    *,
    limit: int = 80,
    offset: int = 0,
) -> list[dict[str, str]]:
    cfg = config or load_config()
    active = cfg.get("active_file") or ""
    if not active:
        return []

    path = glossary_file_path(active)
    if not os.path.isfile(path):
        return []

    try:
        df, _ = load_excel(path, cfg.get("sheet") or None)
    except Exception:
        return []

    term_col = cfg.get("term_column") or ""
    trans_col = cfg.get("translation_column") or ""
    if not term_col and len(df.columns):
        term_col = str(df.columns[0])
    if not trans_col and len(df.columns) > 1:
        trans_col = str(df.columns[1])

    q = (search or "").strip()
    scored: list[tuple[int, dict[str, str]]] = []
    for i, row in df.iterrows():
        record = {str(c): str(row.get(c, "")).strip() for c in df.columns}
        record["_row"] = str(i)
        term_text = record.get(term_col, "") if term_col else ""
        if q:
            score = _search_match_score(term_text, q, record, term_col)
            if score <= 0:
                continue
        else:
            score = 0
        if term_col:
            record["_term"] = record.get(term_col, "")
        if trans_col:
            record["_translation"] = record.get(trans_col, "")
        record["_match_score"] = str(score)
        scored.append((score, record))

    if q:
        scored.sort(key=lambda x: x[0], reverse=True)
    sliced = scored[offset : offset + max(1, limit)]
    return [r for _, r in sliced]


def preview_row_count(config: dict[str, Any] | None = None, search: str = "") -> int:
    """Total rows matching search (for pagination UI) without building full table."""
    cfg = config or load_config()
    active = cfg.get("active_file") or ""
    if not active:
        return 0
    path = glossary_file_path(active)
    if not os.path.isfile(path):
        return 0
    try:
        df, _ = load_excel(path, cfg.get("sheet") or None)
    except Exception:
        return 0
    term_col = cfg.get("term_column") or ""
    if not term_col and len(df.columns):
        term_col = str(df.columns[0])
    q = (search or "").strip()
    if not q:
        return len(df)
    count = 0
    for _, row in df.iterrows():
        record = {str(c): str(row.get(c, "")).strip() for c in df.columns}
        term_text = record.get(term_col, "") if term_col else ""
        if _search_match_score(term_text, q, record, term_col) > 0:
            count += 1
    return count
