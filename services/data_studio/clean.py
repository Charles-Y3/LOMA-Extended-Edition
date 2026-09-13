# -*- coding: utf-8 -*-
"""Deterministic cleaning / type-conversion / scaling for heterogeneous sheets.

No LLM in this path — everything here is reproducible pandas so the "what we changed"
audit shown to the user is exact. Source frames are never mutated in place; every
operation returns a new frame plus an audit list the UI can display and selectively
revert.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Tokens stripped before attempting numeric coercion.
_NUM_STRIP = re.compile(r"[,\s%$€£¥]")
_PERCENT = re.compile(r"%\s*$")
_WS = re.compile(r"\s+")


def _normalize_header(name: str) -> str:
    return _WS.sub(" ", str(name).replace("\n", " ").replace("\r", " ")).strip()


@dataclass
class ColumnAudit:
    """One recorded transformation on a single column."""

    column: str
    action: str          # coerce_numeric | parse_date | strip_text | (scaling actions)
    detail: str
    reverted: bool = False


@dataclass
class CleanResult:
    frame: Any                                   # cleaned pandas DataFrame
    audit: list[ColumnAudit] = field(default_factory=list)
    dropped_columns: list[str] = field(default_factory=list)
    duplicate_rows: int = 0


def _coerce_numeric_series(series):
    """Return (numeric_series, ok) where ok means the column is confidently numeric."""
    import pandas as pd

    if series.dtype.kind in "if":
        return series, False  # already numeric, nothing to record
    as_str = series.astype(str).str.strip()
    had_percent = as_str.str.contains(_PERCENT, regex=True, na=False).mean() > 0.5
    stripped = as_str.str.replace(_NUM_STRIP, "", regex=True).replace({"": None, "nan": None})
    num = pd.to_numeric(stripped, errors="coerce")
    non_null = series.notna().sum()
    if non_null == 0:
        return series, False
    # Confident only if most originally-present values parsed cleanly.
    if num.notna().sum() >= max(2, int(0.8 * non_null)):
        if had_percent:
            num = num / 100.0
        return num, True
    return series, False


def _parse_date_series(series):
    import pandas as pd

    if series.dtype.kind in "if" or str(series.dtype).startswith("datetime"):
        return series, False
    sample = series.dropna().astype(str).head(50)
    if sample.empty:
        return series, False
    # Only attempt if values look date-ish (contain a separator + digits).
    looks_datey = sample.str.contains(r"\d{1,4}[-/.\s]\d{1,2}", regex=True, na=False).mean()
    if looks_datey < 0.6:
        return series, False
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    non_null = series.notna().sum()
    if non_null and parsed.notna().sum() >= max(2, int(0.8 * non_null)):
        return parsed, True
    return series, False


def clean_frame(df, *, drop_duplicates: bool = True) -> CleanResult:
    """Auto-clean a frame and record every change for review/revert."""
    import pandas as pd

    from services.graph_generation.dataset import _drop_junk_columns

    before_cols = list(df.columns)
    out = _drop_junk_columns(df).copy()
    dropped = [str(c) for c in before_cols if c not in out.columns]

    audit: list[ColumnAudit] = []
    rename = {
        str(c): _normalize_header(c)
        for c in out.columns
        if _normalize_header(c) != str(c)
    }
    if rename:
        out = out.rename(columns=rename)
        for old, new in rename.items():
            audit.append(ColumnAudit(new, "normalize_header", f'"{old}" → "{new}"'))

    for col in list(out.columns):
        series = out[col]
        if series.dtype == object:
            stripped = series.astype(str).str.strip()
            # Restore genuine NaNs that str() turned into "nan"/"None".
            stripped = stripped.replace({"nan": None, "None": None, "": None})
            if not stripped.equals(series.astype(str)):
                out[col] = stripped
                audit.append(ColumnAudit(str(col), "strip_text", "trimmed whitespace"))
                series = out[col]

        num, ok = _coerce_numeric_series(series)
        if ok:
            out[col] = num
            audit.append(ColumnAudit(str(col), "coerce_numeric", "text → number"))
            continue

        dts, ok = _parse_date_series(series)
        if ok:
            out[col] = dts
            audit.append(ColumnAudit(str(col), "parse_date", "text → datetime"))

    dup_count = 0
    if drop_duplicates:
        dup_count = int(out.duplicated().sum())
        if dup_count:
            out = out.drop_duplicates().reset_index(drop=True)

    return CleanResult(frame=out, audit=audit, dropped_columns=dropped, duplicate_rows=dup_count)


def revert_action(original_df, cleaned: CleanResult, audit_index: int) -> CleanResult:
    """Re-run cleaning skipping one audited action (by index) and return a fresh result.

    Simplest correct approach: recompute from the original frame, then restore the one
    reverted column from the original data. Keeps the audit consistent for the UI.
    """
    if not (0 <= audit_index < len(cleaned.audit)):
        return cleaned
    target = cleaned.audit[audit_index]
    target.reverted = not target.reverted
    if target.reverted and target.column in original_df.columns:
        cleaned.frame[target.column] = original_df[target.column].values
    elif not target.reverted:
        # Re-apply by cleaning just that column again.
        single = clean_frame(original_df[[target.column]], drop_duplicates=False)
        if target.column in single.frame.columns:
            cleaned.frame[target.column] = single.frame[target.column].values
    return cleaned


# ── Scaling (applied lazily as derived series, never mutating source) ───────────

def scale_series(series, method: str):
    """Return a normalized copy of a numeric series. Unknown method → unchanged."""
    import pandas as pd

    num = pd.to_numeric(series, errors="coerce")
    if method == "min_max":
        lo, hi = num.min(), num.max()
        if pd.isna(lo) or pd.isna(hi) or hi == lo:
            return num
        return (num - lo) / (hi - lo)
    if method == "z_score":
        mu, sd = num.mean(), num.std()
        if pd.isna(sd) or sd == 0:
            return num
        return (num - mu) / sd
    if method == "index_100":
        base = num.dropna().iloc[0] if num.notna().any() else None
        if not base:
            return num
        return num / base * 100.0
    return num


SCALING_METHODS = ("none", "min_max", "z_score", "index_100")
