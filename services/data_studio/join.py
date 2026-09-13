# -*- coding: utf-8 -*-
"""Merge workspace datasets on confirmed join-key mappings into one analysis frame.

Shared by Dashboard (joined charts), Ask (pre-joined Q&A frame) and Insights
(cross-dataset stats). Service layer: no UI imports; pandas imported lazily.
Warnings are machine codes ("row_explosion", "no_join_keys") — the UI translates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.data_studio.correlate import ColumnRef, MappingPair


@dataclass
class JoinInfo:
    left: ColumnRef
    right: ColumnRef
    cardinality: str          # "1:1" | "1:N" | "N:1" | "N:M"
    match_rate_left: float    # share of distinct left keys found in right
    match_rate_right: float


@dataclass
class JoinResult:
    frame: Any | None                         # merged df; plain column names, prefixed only on collision
    joins: list[JoinInfo] = field(default_factory=list)
    unjoined: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    origin: dict[str, str] = field(default_factory=dict)   # merged column -> source dataset key


def _key_str(series):
    """Keys as trimmed strings with blanks/NaN masked out, so dtypes never block a match."""
    out = series.astype(str).str.strip()
    return out.where(series.notna() & (out != "") & (out.str.lower() != "nan"))


def key_stats(left_series, right_series) -> tuple[str, float, float]:
    """(cardinality, match_rate_left, match_rate_right) for two key columns.

    A duplicated side means that table is transactional for this key (many rows
    per entity); match rates are the share of one side's distinct keys present
    in the other.
    """
    la = _key_str(left_series).dropna()
    rb = _key_str(right_series).dropna()
    sa, sb = set(la), set(rb)
    if not sa or not sb:
        return "N:M", 0.0, 0.0
    match_l = len(sa & sb) / len(sa)
    match_r = len(sa & sb) / len(sb)
    left_unique = la.is_unique
    right_unique = rb.is_unique
    left_c = "1" if left_unique else "N"
    right_c = "1" if right_unique else ("N" if left_unique else "M")
    return f"{left_c}:{right_c}", round(match_l, 3), round(match_r, 3)


def _dedupe_joins(mappings: list[MappingPair]) -> list[MappingPair]:
    """Best join-key pair per unordered dataset pair (highest confidence wins)."""
    best: dict[tuple[str, str], MappingPair] = {}
    for m in mappings:
        if m.role != "join_key" or m.left.dataset == m.right.dataset:
            continue
        key = tuple(sorted((m.left.dataset, m.right.dataset)))
        cur = best.get(key)
        if cur is None or m.confidence > cur.confidence:
            best[key] = m
    return sorted(best.values(), key=lambda m: m.confidence, reverse=True)


def build_workspace_frame(
    frames: dict[str, Any],
    mappings: list[MappingPair],
    *,
    how: str = "outer",
    max_rows: int = 200_000,
) -> JoinResult:
    """Chain-merge all datasets reachable through confirmed join keys.

    Spanning-tree merge starting from the largest joined table. Columns keep
    their plain names; only names that collide across datasets get a
    "<dataset>." prefix (a coalesced join key reclaims the plain name). Key
    values are matched as trimmed strings. Aborts with "row_explosion" if an
    N:M merge blows past max_rows.
    """
    usable = [
        m for m in _dedupe_joins(mappings or [])
        if m.left.dataset in frames and m.right.dataset in frames
        and m.left.column in frames[m.left.dataset].columns
        and m.right.column in frames[m.right.dataset].columns
    ]
    if not usable:
        return JoinResult(
            frame=None,
            unjoined=list(frames),
            warnings=["no_join_keys"] if len(frames) > 1 else [],
        )

    all_names: dict[str, set] = {}
    for k, df in frames.items():
        for c in df.columns:
            all_names.setdefault(str(c), set()).add(k)
    collide = {c for c, ks in all_names.items() if len(ks) > 1}

    def _prep(ds: str):
        """Copy of the frame with collision-only prefixes + orig->final name map."""
        df = frames[ds].copy()
        ren = {c: (f"{ds}.{c}" if str(c) in collide else str(c)) for c in map(str, df.columns)}
        return df.rename(columns=ren), ren

    joined_ds = {m.left.dataset for m in usable} | {m.right.dataset for m in usable}
    start = max(joined_ds, key=lambda k: len(frames[k]))
    merged, ren = _prep(start)
    colmap: dict[tuple[str, str], str] = {(start, orig): new for orig, new in ren.items()}
    result = JoinResult(frame=None, origin={new: start for new in ren.values()})
    merged_datasets = {start}
    pending = list(usable)

    progress = True
    while progress:
        progress = False
        for m in list(pending):
            lds, rds = m.left.dataset, m.right.dataset
            if lds in merged_datasets and rds in merged_datasets:
                pending.remove(m)
                continue
            if lds in merged_datasets:
                old_ds, old_col = lds, str(m.left.column)
                new_ds, new_col = rds, str(m.right.column)
            elif rds in merged_datasets:
                old_ds, old_col = rds, str(m.right.column)
                new_ds, new_col = lds, str(m.left.column)
            else:
                continue

            card, ml, mr = key_stats(
                frames[m.left.dataset][m.left.column],
                frames[m.right.dataset][m.right.column],
            )
            old_key = colmap[(old_ds, old_col)]
            right_df, rren = _prep(new_ds)
            for orig, new in rren.items():
                colmap[(new_ds, orig)] = new
            new_key = rren[new_col]
            merged["__jk_l__"] = _key_str(merged[old_key])
            right_df["__jk_r__"] = _key_str(right_df[new_key])
            merged = merged.merge(
                right_df,
                left_on="__jk_l__",
                right_on="__jk_r__",
                how=how,
                suffixes=("", "_dup"),
            )
            # Coalesce the two key columns into the existing one.
            merged[old_key] = merged[old_key].fillna(merged[new_key])
            merged = merged.drop(columns=["__jk_l__", "__jk_r__", new_key], errors="ignore")
            colmap[(new_ds, new_col)] = old_key
            for orig, new in rren.items():
                if new != new_key:
                    result.origin[new] = new_ds
            # A same-named join key reclaims its plain name once coalesced.
            if old_col == new_col and old_key != old_col and old_col not in merged.columns:
                merged = merged.rename(columns={old_key: old_col})
                for pair, final in list(colmap.items()):
                    if final == old_key:
                        colmap[pair] = old_col
                result.origin[old_col] = result.origin.pop(old_key, old_ds)
                old_key = old_col
            merged_datasets.add(new_ds)
            result.joins.append(
                JoinInfo(left=m.left, right=m.right, cardinality=card,
                         match_rate_left=ml, match_rate_right=mr)
            )
            pending.remove(m)
            progress = True
            if len(merged) > max_rows:
                result.warnings.append("row_explosion")
                result.unjoined = [k for k in frames if k not in merged_datasets]
                return result

    result.frame = merged
    result.unjoined = [k for k in frames if k not in merged_datasets]
    return result


def _bucket_datetime(series, bucket: str = "auto"):
    """Bucket a datetime column to period strings sized to its span."""
    if bucket == "auto":
        span = series.max() - series.min()
        days = getattr(span, "days", 0) or 0
        bucket = "Y" if days > 1100 else ("M" if days > 92 else "D")
    return series.dt.to_period(bucket).astype(str)


def aggregate_frame(
    df,
    x: str,
    y: str | None,
    agg: str,
    *,
    group: str | None = None,
    datetime_bucket: str = "auto",
):
    """Tidy aggregation: columns (x[, group], value), sorted by x.

    Datetime x/group columns are bucketed to period labels; y is coerced numeric
    (except for count). Pass ``y=None`` to count rows per group — this needs no
    numeric column, so it works on all-text data.
    """
    import pandas as pd

    if group and group == x:
        group = None

    def _one_col(frame, name: str):
        if not name:
            return None
        if name not in frame.columns:
            return None
        s = frame[name]
        if isinstance(s, pd.DataFrame):
            s = s.iloc[:, 0]
        return s

    keys = [x] + ([group] if group else [])
    pick = [c for c in (x, group, y) if c]
    sub = df[pick].copy()
    if sub.columns.duplicated().any():
        sub = sub.loc[:, ~sub.columns.duplicated()]
    for dim in (x, group):
        if not dim:
            continue
        col = _one_col(sub, dim)
        if col is None:
            continue
        if str(col.dtype).startswith("datetime"):
            sub[dim] = _bucket_datetime(col, datetime_bucket)
    sub = sub.dropna(subset=[x])

    if y is None:
        # Count rows per group — no numeric metric required.
        out = sub.groupby(keys, dropna=True).size().reset_index(name="value")
    else:
        if agg != "count":
            sub[y] = pd.to_numeric(sub[y], errors="coerce")
        grouped = sub.groupby(keys, dropna=True)[y]
        fn = agg if agg in ("sum", "mean", "count", "max", "min") else "sum"
        out = getattr(grouped, fn)().reset_index().rename(columns={y: "value"})
    try:
        out = out.sort_values(keys).reset_index(drop=True)
    except TypeError:
        pass
    return out
