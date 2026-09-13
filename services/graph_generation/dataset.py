# -*- coding: utf-8 -*-
"""Load tabular uploads for profiling and plotting."""
from __future__ import annotations

import os
from pathlib import Path

_TABULAR_EXT = frozenset({".csv", ".xlsx", ".xls", ".tsv"})


def is_tabular_path(path: str) -> bool:
    return Path(path or "").suffix.lower() in _TABULAR_EXT


def resolve_tabular_paths(
    parsed_sources: list | None = None,
    context_files: list | None = None,
) -> list[tuple[str, str]]:
    """Return (display_name, absolute_path) for tabular inputs."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    for ps in parsed_sources or []:
        path = getattr(ps, "path", "") or ""
        name = getattr(ps, "name", "") or os.path.basename(path)
        kind = getattr(ps, "kind", "") or ""
        if not path and kind == "spreadsheet" and name:
            candidate = os.path.join("data", "uploads", name)
            if os.path.isfile(candidate):
                path = os.path.abspath(candidate)
        if not path and getattr(ps, "media_path", ""):
            mp = ps.media_path
            if is_tabular_path(mp) and os.path.isfile(mp):
                path = os.path.abspath(mp)
        if path and is_tabular_path(path) and os.path.isfile(path):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                out.append((name, path))

    for entry in context_files or []:
        if not isinstance(entry, dict):
            continue
        name = entry.get("filename") or "data"
        path = entry.get("source_path") or entry.get("content") or ""
        if isinstance(path, str) and not os.path.isfile(path):
            path = os.path.join("data", "uploads", name)
        if path and is_tabular_path(path) and os.path.isfile(path):
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                out.append((name, os.path.abspath(path)))

    return out


def _drop_junk_columns(df):
    """Remove empty Unnamed index columns and integer row-index columns."""
    import pandas as pd

    out = df.copy()
    drop: list[str] = []
    for col in out.columns:
        name = str(col)
        if name.lower().startswith("unnamed"):
            if out[col].isna().all() or (out[col].astype(str).str.strip() == "").all():
                drop.append(col)
                continue
            series = pd.to_numeric(out[col], errors="coerce")
            if series.notna().sum() >= max(3, int(0.85 * len(out))):
                vals = series.dropna()
                if len(vals) >= 3 and vals.min() == 0 and vals.max() <= len(out) + 2:
                    if (vals.diff().dropna() == 1).mean() > 0.85:
                        drop.append(col)
    if drop:
        out = out.drop(columns=drop, errors="ignore")
    return out


def load_dataframe(path: str, *, max_rows: int | None = None):
    """Load pandas DataFrame with sensible limits for large files."""
    import pandas as pd

    ext = Path(path).suffix.lower()
    if ext == ".csv":
        df = pd.read_csv(
            path,
            nrows=max_rows,
            low_memory=False,
            thousands=",",
            encoding="utf-8",
            encoding_errors="replace",
        )
    elif ext == ".tsv":
        df = pd.read_csv(path, sep="\t", nrows=max_rows, low_memory=False)
    elif ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, nrows=max_rows)
    else:
        raise ValueError(f"Unsupported tabular format: {ext}")
    return _drop_junk_columns(df)


def load_all_sheets(path: str, *, max_rows: int | None = None) -> dict:
    """Load every sheet of a workbook; CSV/TSV yield a single entry keyed by filename."""
    ext = Path(path).suffix.lower()
    if ext in (".xlsx", ".xls"):
        import pandas as pd

        raw = pd.read_excel(path, sheet_name=None, nrows=max_rows)
        return {str(name): _drop_junk_columns(df) for name, df in raw.items()}
    return {os.path.basename(path): load_dataframe(path, max_rows=max_rows)}
