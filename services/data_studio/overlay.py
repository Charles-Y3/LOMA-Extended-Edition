# -*- coding: utf-8 -*-
"""Combine correlated columns from different sheets onto one shared axis.

Two shapes:
  - build_overlay_series: for charts — align each (dataset, value column) against a
    shared axis (a join-key column or a common category/time column), optionally scaled
    so different-magnitude series are visually comparable.
  - build_merged_frame: outer-merge two frames on a confirmed join-key pair (for
    tabular inspection / downstream Q&A).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.data_studio.clean import scale_series


@dataclass
class OverlaySeries:
    label: str            # "dataset — column"
    axis: list[Any] = field(default_factory=list)
    values: list[Any] = field(default_factory=list)


@dataclass
class OverlayResult:
    axis_labels: list[str] = field(default_factory=list)
    series: list[OverlaySeries] = field(default_factory=list)


def _aggregate(df, axis_col: str, value_col: str, agg: str):
    """Group value_col by axis_col; return an ordered (axis, value) Series."""
    import pandas as pd

    sub = df[[axis_col, value_col]].copy()
    sub[value_col] = pd.to_numeric(sub[value_col], errors="coerce")
    grouped = sub.groupby(axis_col, dropna=True)[value_col]
    func = {"sum": grouped.sum, "mean": grouped.mean, "count": grouped.count,
            "max": grouped.max, "min": grouped.min}.get(agg, grouped.sum)
    return func()


def build_overlay_series(
    frames: dict[str, Any],
    specs: list[dict],
    *,
    axis_by_dataset: dict[str, str],
    agg: str = "sum",
    scale: str = "none",
) -> OverlayResult:
    """
    specs: [{"dataset": key, "column": value_col, "label": optional}]
    axis_by_dataset: dataset key -> the axis column to align on within that dataset.
    All series are reindexed onto the union of axis values so ECharts can plot them
    on one category axis.
    """
    import pandas as pd

    computed: list[tuple[str, "pd.Series"]] = []
    axis_union: list[Any] = []
    seen: set = set()

    for spec in specs:
        ds = spec.get("dataset")
        col = spec.get("column")
        if ds not in frames or not col:
            continue
        df = frames[ds]
        axis_col = axis_by_dataset.get(ds)
        if not axis_col or axis_col not in df.columns or col not in df.columns:
            continue
        s = _aggregate(df, axis_col, col, agg)
        try:
            s = s.sort_index()
        except TypeError:
            pass
        if scale != "none":
            s = pd.Series(scale_series(s, scale).values, index=s.index)
        label = spec.get("label") or f"{ds} — {col}"
        computed.append((label, s))
        for k in s.index.tolist():
            if k not in seen:
                seen.add(k)
                axis_union.append(k)

    try:
        axis_union = sorted(axis_union)
    except TypeError:
        pass

    axis_labels = [str(k) for k in axis_union]
    result = OverlayResult(axis_labels=axis_labels)
    for label, s in computed:
        lookup = s.to_dict()
        values = [
            (None if k not in lookup or pd.isna(lookup[k]) else round(float(lookup[k]), 6))
            for k in axis_union
        ]
        result.series.append(OverlaySeries(label=label, axis=axis_labels, values=values))
    return result


def build_merged_frame(frames: dict[str, Any], left_key, right_key, *, how: str = "outer"):
    """Outer-merge two frames on a confirmed join-key pair. Returns a DataFrame or None."""
    ld = getattr(left_key, "dataset", None)
    rd = getattr(right_key, "dataset", None)
    if ld not in frames or rd not in frames:
        return None
    left = frames[ld].copy()
    right = frames[rd].copy()
    lc, rc = left_key.column, right_key.column
    if lc not in left.columns or rc not in right.columns:
        return None
    left = left.add_prefix(f"{ld}.")
    right = right.add_prefix(f"{rd}.")
    return left.merge(
        right,
        left_on=f"{ld}.{lc}",
        right_on=f"{rd}.{rc}",
        how=how,
        suffixes=("", "_dup"),
    )
