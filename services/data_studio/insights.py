# -*- coding: utf-8 -*-
"""In-depth analysis: compute stats deterministically, then render a template report.

Stats (describe, correlations, null/outlier flags, joined aggregates) are computed in
code. The markdown report is built from those numbers so the model cannot invent
figures. An optional short LLM pass may add 2–4 highlight bullets only.
A second LLM pass writes an Analysis section grounded in auto-generated charts
(same spirit as direct-flow spreadsheet → analyse report).
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Callable

_MAX_GROUP_AGGS = 15
_MAX_GROUP_VALUES = 20
_MAX_EXTREME_COLS = 6
_MAX_CITABLE_EXAMPLES = 16


def _datetime_ranges(df: Any) -> dict[str, dict]:
    """Min/max/span for any datetime column — including date-like text columns (CSV dates
    load as strings, not datetime64). Without this, a report has no grounding for date-range
    claims and will guess, e.g. calling a 24-month spread an '8-month period'."""
    from services.data_studio.clean import _parse_date_series

    out: dict[str, dict] = {}
    for col in df.columns:
        series = df[col]
        is_native = str(series.dtype).startswith("datetime")
        parsed, changed = (series, False) if is_native else _parse_date_series(series)
        if not (is_native or changed):
            continue
        non_null = parsed.dropna()
        if non_null.empty:
            continue
        span_days = int((non_null.max() - non_null.min()).days)
        out[str(col)] = {
            "min": str(non_null.min().date()),
            "max": str(non_null.max().date()),
            "span_days": span_days,
        }
    return out


def _outlier_stats(num_df: Any) -> dict[str, dict]:
    """IQR-based outlier counts per numeric column — computed, not eyeballed by the model."""
    import pandas as pd

    out: dict[str, dict] = {}
    for col in num_df.columns:
        series = num_df[col].dropna()
        if len(series) < 8:
            continue
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1
        if not iqr or pd.isna(iqr):
            continue
        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        n_low = int((series < lower).sum())
        n_high = int((series > upper).sum())
        n_out = n_low + n_high
        if n_out == 0:
            continue
        entry: dict[str, Any] = {"count": n_out, "pct": round(100.0 * n_out / len(series), 2)}
        # Only report a bound on the side that actually has outliers — an unused IQR bound
        # can land below zero for right-skewed count data (impressions, likes, ...) and reads
        # as a nonsensical negative threshold if included regardless.
        if n_low:
            entry["low_count"] = n_low
            entry["lower_bound"] = round(float(lower), 4)
        if n_high:
            entry["high_count"] = n_high
            entry["upper_bound"] = round(float(upper), 4)
        out[str(col)] = entry
    return out


def _citable_examples(df: Any, num_df: Any) -> list[dict]:
    """The row with the max and min value of each numeric column (deduped) — the ONLY specific
    rows safe for a narrative to name. Without an explicit whitelist like this, a model asked
    for a concrete example can fabricate one (invent an ID, or attach wrong numbers to a real
    ID) since it isn't shown enough raw rows to know it's wrong."""
    examples: list[dict] = []
    seen_idx: set = set()
    for col in list(num_df.columns)[:_MAX_EXTREME_COLS]:
        series = num_df[col].dropna()
        if series.empty:
            continue
        for idx in (series.idxmax(), series.idxmin()):
            if idx in seen_idx or len(examples) >= _MAX_CITABLE_EXAMPLES:
                continue
            seen_idx.add(idx)
            row = df.loc[idx]
            examples.append({str(c): row[c] for c in df.columns})
    return examples


def _dataset_kinds(frames: dict[str, Any], mappings: list) -> dict[str, str]:
    """entity (join key unique) vs transactional (many rows per key) per dataset."""
    kinds: dict[str, str] = {}
    for m in mappings or []:
        if getattr(m, "role", "") != "join_key":
            continue
        for ref in (m.left, m.right):
            df = frames.get(ref.dataset)
            if df is None or ref.column not in df.columns or ref.dataset in kinds:
                continue
            keys = df[ref.column].dropna()
            kinds[ref.dataset] = "entity" if keys.is_unique else "transactional"
    return kinds


def _group_aggregates(df: Any, num_df: Any) -> list[dict[str, Any]]:
    """Each low-cardinality categorical column x each numeric column: mean/sum per category.
    This is the exact breakdown a 'compare X by category' narrative needs — without it, a model
    asked to describe per-category numbers has nothing to copy and will reconstruct/misattribute
    them from memory, e.g. scrambling which count belongs to which platform."""
    import pandas as pd

    # Skip identifier-like columns (nearly one distinct value per row — no grouping value).
    cat_cols = [
        str(c) for c in df.columns
        if df[c].dtype == object
        and 0 < df[c].nunique(dropna=True) <= _MAX_GROUP_VALUES
        and df[c].nunique(dropna=True) < 0.8 * max(1, df[c].notna().sum())
    ]
    cat_cols.sort(key=lambda c: df[c].nunique(dropna=True))
    aggs: list[dict[str, Any]] = []
    for cat in cat_cols:
        for num in num_df.columns:
            if len(aggs) >= _MAX_GROUP_AGGS:
                return aggs
            grouped = df.dropna(subset=[cat]).groupby(cat)[num]
            mean = {str(k): round(float(v), 4) for k, v in grouped.mean().items() if pd.notna(v)}
            if not mean:
                continue
            count = {str(k): int(v) for k, v in grouped.count().items()}
            aggs.append({
                "by": cat,
                "metric": str(num),
                "count": count,
                "mean": mean,
                "sum": {str(k): round(float(v), 4) for k, v in grouped.sum().items() if pd.notna(v)},
            })
    return aggs


def _joined_stats(frames: dict[str, Any], mappings: list, *, top_corr: int = 8) -> dict | None:
    """Cross-dataset correlations + group aggregates over the merged frame."""
    import pandas as pd

    from services.data_studio.join import build_workspace_frame

    jr = build_workspace_frame(frames, mappings)
    if jr.frame is None:
        return None
    df = jr.frame
    out: dict[str, Any] = {
        "join_on": [
            {
                "left": f"{j.left.dataset}::{j.left.column}",
                "right": f"{j.right.dataset}::{j.right.column}",
                "cardinality": j.cardinality,
                "key_match_rate_left": j.match_rate_left,
                "key_match_rate_right": j.match_rate_right,
            }
            for j in jr.joins
        ],
        "rows": int(len(df)),
    }
    if jr.warnings:
        out["warnings"] = list(jr.warnings)

    # Correlations between numeric columns that come from DIFFERENT datasets.
    num_df = df.select_dtypes(include="number")
    if num_df.shape[1] >= 2:
        corr = num_df.corr(numeric_only=True)
        pairs = []
        cols = list(corr.columns)
        for i in range(len(cols)):
            for j in range(i + 1, len(cols)):
                if jr.origin.get(str(cols[i])) == jr.origin.get(str(cols[j])):
                    continue
                v = corr.iloc[i, j]
                if pd.notna(v):
                    pairs.append((str(cols[i]), str(cols[j]), round(float(v), 3)))
        pairs.sort(key=lambda p: abs(p[2]), reverse=True)
        if pairs:
            out["cross_dataset_correlations"] = [
                {"a": a, "b": b, "r": r} for a, b, r in pairs[:top_corr]
            ]

    aggs = _group_aggregates(df, num_df)
    if aggs:
        out["group_aggregates"] = aggs
    return out


def compute_stats(frames: dict[str, Any], *, mappings: list | None = None, top_corr: int = 8) -> dict:
    """Deterministic per-dataset stats, plus joined cross-dataset stats when join keys exist."""
    import pandas as pd

    kinds = _dataset_kinds(frames, mappings or [])
    out: dict[str, Any] = {
        "note": "Datasets are separate files; row counts are per dataset and must never be summed.",
        "datasets": [],
    }
    for name, df in frames.items():
        num_df = df.select_dtypes(include="number")
        entry: dict[str, Any] = {
            "name": name,
            "rows": int(len(df)),
            "columns": int(df.shape[1]),
            "numeric_columns": [str(c) for c in num_df.columns],
        }
        if name in kinds:
            entry["kind"] = kinds[name]  # entity = one row per key; transactional = many
        # Describe (rounded, compact).
        if not num_df.empty:
            desc = num_df.describe().round(4).to_dict()
            entry["describe"] = {
                str(c): {k: (None if pd.isna(v) else v) for k, v in stats.items()}
                for c, stats in desc.items()
            }
        # Null percentages.
        nulls = {
            str(c): round(100.0 * float(df[c].isna().mean()), 2)
            for c in df.columns
            if df[c].isna().any()
        }
        if nulls:
            entry["null_pct"] = nulls
        # Date ranges (native datetime64 or date-like text columns).
        date_ranges = _datetime_ranges(df)
        if date_ranges:
            entry["date_ranges"] = date_ranges
        # IQR outlier counts per numeric column.
        outliers = _outlier_stats(num_df)
        if outliers:
            entry["outliers"] = outliers
        # Whitelist of specific rows safe to cite by name/ID in the narrative.
        citable = _citable_examples(df, num_df)
        if citable:
            entry["citable_examples"] = citable
        # Category breakdowns: e.g. avg/sum of a metric per Platform, within this one dataset —
        # previously only computed for cross-dataset joins, missing for the common single-file case.
        if not num_df.empty:
            aggs = _group_aggregates(df, num_df)
            if aggs:
                entry["group_aggregates"] = aggs
        # Strongest correlations within the dataset.
        if num_df.shape[1] >= 2:
            corr = num_df.corr(numeric_only=True).abs()
            pairs = []
            cols = list(corr.columns)
            for i in range(len(cols)):
                for j in range(i + 1, len(cols)):
                    v = corr.iloc[i, j]
                    if pd.notna(v):
                        pairs.append((cols[i], cols[j], round(float(v), 3)))
            pairs.sort(key=lambda p: p[2], reverse=True)
            entry["top_correlations"] = [
                {"a": str(a), "b": str(b), "r": r} for a, b, r in pairs[:top_corr]
            ]
        out["datasets"].append(entry)

    if mappings:
        out["mappings"] = [
            {
                "left": f"{m.left.dataset}::{m.left.column}",
                "right": f"{m.right.dataset}::{m.right.column}",
                "role": m.role,
                "confidence": m.confidence,
                "value_overlap_pct": int(getattr(m, "overlap", 0.0) * 100),
            }
            for m in mappings
        ]
        joined = _joined_stats(frames, mappings, top_corr=top_corr)
        if joined:
            out["joined"] = joined
    return out


def _metric_bucket(name: str) -> str:
    n = str(name).lower()
    if any(t in n for t in ("salary", "wage", "pay", "income", "revenue", "sales", "price", "cost", "amount", "fee")):
        return "money"
    if any(t in n for t in ("rating", "score", "performance", "grade", "rank")):
        return "rating"
    return "other"


def _fmt_num(v: Any) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if abs(f - round(f)) < 1e-9:
        return f"{int(round(f)):,}"
    return f"{f:,.2f}"


def _top_bottom_from_agg(agg: dict, *, n: int = 3) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    mean = agg.get("mean") or {}
    items = [(str(k), float(v)) for k, v in mean.items()]
    items.sort(key=lambda t: t[1], reverse=True)
    top = items[:n]
    bottom = list(reversed(items[-n:])) if len(items) > 1 else []
    return top, bottom


def render_insights_markdown(stats: dict) -> str:
    """Deterministic report from compute_stats — no LLM invention."""
    lines: list[str] = ["## Dataset Overview", ""]
    datasets = stats.get("datasets") or []
    for ds in datasets:
        kind = ds.get("kind")
        kind_bit = f" ({kind})" if kind else ""
        lines.append(
            f"- **{ds.get('name')}**{kind_bit}: {ds.get('rows')} rows, {ds.get('columns')} columns"
        )
        for col, dr in (ds.get("date_ranges") or {}).items():
            lines.append(
                f"  - `{col}`: {dr.get('min')} → {dr.get('max')} ({dr.get('span_days')} days)"
            )
        nulls = ds.get("null_pct") or {}
        if nulls:
            bits = ", ".join(f"{c} {p}%" for c, p in list(nulls.items())[:6])
            lines.append(f"  - Nulls: {bits}")
    lines.append("")
    lines.append("_Row counts are per dataset and must not be added together._")
    lines.append("")

    joined = stats.get("joined") or {}
    if joined.get("join_on"):
        lines.append("### Join")
        for j in joined["join_on"]:
            lines.append(
                f"- `{j.get('left')}` ↔ `{j.get('right')}` — cardinality **{j.get('cardinality')}**, "
                f"match left {j.get('key_match_rate_left')}, right {j.get('key_match_rate_right')}"
            )
        if joined.get("rows") is not None:
            lines.append(f"- Joined frame rows: {joined['rows']}")
        lines.append("")

    all_aggs: list[dict] = []
    for ds in datasets:
        for a in ds.get("group_aggregates") or []:
            all_aggs.append(a)
    for a in joined.get("group_aggregates") or []:
        all_aggs.append(a)

    money_aggs = [a for a in all_aggs if _metric_bucket(a.get("metric", "")) == "money"]
    rating_aggs = [a for a in all_aggs if _metric_bucket(a.get("metric", "")) == "rating"]
    other_aggs = [a for a in all_aggs if _metric_bucket(a.get("metric", "")) == "other"]

    def _section(title: str, aggs: list[dict], *, money: bool) -> None:
        has_desc = any(
            _metric_bucket(c) == ("money" if money else "rating")
            for ds in datasets
            for c in (ds.get("numeric_columns") or [])
        )
        if not aggs and not has_desc:
            return
        lines.append(f"## {title}")
        lines.append("")
        for ds in datasets:
            desc = ds.get("describe") or {}
            for col, st in desc.items():
                if money and _metric_bucket(col) != "money":
                    continue
                if not money and _metric_bucket(col) != "rating":
                    continue
                lines.append(
                    f"- **{col}** (dataset `{ds.get('name')}`): "
                    f"mean {_fmt_num(st.get('mean'))}, min {_fmt_num(st.get('min'))}, "
                    f"max {_fmt_num(st.get('max'))}, count {_fmt_num(st.get('count'))}"
                )
        def _by_rank(by: str) -> int:
            b = by.lower().replace(" ", "_")
            # IDs before substring "employee"/"name" matches (Employee_ID contains both).
            if b == "id" or (b.endswith("_id") and not any(
                t in b for t in ("department", "dept", "region", "team", "category", "status")
            )):
                return 2
            if any(t in b for t in ("full_name", "display_name", "customer_name", "product_name", "employee_name")):
                return 0
            if b.endswith("_name") or b == "name":
                return 0
            if any(t in b for t in ("department", "dept", "region", "team", "category")):
                return 1
            return 9  # skip noisy dims (email, status, period) unless nothing else

        preferred = [a for a in aggs if _by_rank(str(a.get("by", ""))) < 9]
        if not preferred:
            preferred = aggs[:2]
        preferred = sorted(preferred, key=lambda a: (_by_rank(str(a.get("by", ""))), str(a.get("by", ""))))
        seen_metrics: set[str] = set()
        for agg in preferred:
            metric = str(agg.get("metric"))
            by = str(agg.get("by"))
            # One breakdown per metric for the best grouping key.
            if metric in seen_metrics:
                continue
            seen_metrics.add(metric)
            top, bottom = _top_bottom_from_agg(agg)
            if top:
                lines.append(f"- Top by mean **{metric}** (grouped by `{by}`):")
                for k, v in top:
                    lines.append(f"  - {k}: {_fmt_num(v)}")
            if bottom and bottom != top:
                lines.append(f"- Lowest by mean **{metric}** (grouped by `{by}`):")
                for k, v in bottom:
                    lines.append(f"  - {k}: {_fmt_num(v)}")
        lines.append("")

    _section("Money / pay metrics", money_aggs, money=True)
    _section("Ratings / performance metrics", rating_aggs, money=False)

    cat_aggs = [
        a for a in all_aggs
        if any(t in str(a.get("by", "")).lower() for t in ("department", "dept", "region", "team", "category"))
    ]
    if cat_aggs:
        lines.append("## Category / department summary")
        lines.append("")
        seen: set[str] = set()
        for agg in cat_aggs:
            key = f"{agg.get('by')}::{agg.get('metric')}"
            if key in seen:
                continue
            seen.add(key)
            top, _ = _top_bottom_from_agg(agg, n=5)
            counts = agg.get("count") or {}
            lines.append(f"- By `{agg.get('by')}` on **{agg.get('metric')}**:")
            for k, v in top:
                cnt = counts.get(k)
                extra = f" (n={cnt})" if cnt is not None else ""
                lines.append(f"  - {k}: mean {_fmt_num(v)}{extra}")
        lines.append("")

    if other_aggs and not money_aggs and not rating_aggs:
        lines.append("## Other numeric metrics")
        lines.append("")
        for agg in other_aggs[:6]:
            top, _ = _top_bottom_from_agg(agg)
            if top:
                lines.append(f"- **{agg.get('metric')}** by `{agg.get('by')}`:")
                for k, v in top:
                    lines.append(f"  - {k}: {_fmt_num(v)}")
        lines.append("")

    corr_lines: list[str] = []
    for ds in datasets:
        for pair in (ds.get("top_correlations") or [])[:4]:
            corr_lines.append(
                f"- `{ds.get('name')}`: {pair.get('a')} ↔ {pair.get('b')} (r={pair.get('r')})"
            )
    for pair in (joined.get("cross_dataset_correlations") or [])[:4]:
        corr_lines.append(
            f"- Cross-dataset: {pair.get('a')} ↔ {pair.get('b')} (r={pair.get('r')})"
        )
    if corr_lines:
        lines.append("## Correlations")
        lines.append("")
        lines.extend(corr_lines)
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _highlights_messages(digest: str) -> list[dict[str, str]]:
    system = (
        "Write 2-4 short bullet highlights from the analysis digest below. "
        "Use ONLY facts present in the digest. No new numbers, no apologies, "
        "no 'wait/re-reading', no currency symbols on ratings. "
        "Output markdown bullets only."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": digest[:4000]},
    ]


def _clean_highlights(text: str) -> str:
    import re

    if not text:
        return ""
    if re.search(r"wait,?\s*re-?reading", text, re.I):
        return ""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.search(r"wait,?\s*re-?reading", s, re.I):
            continue
        lines.append(line)
    out = "\n".join(lines).strip()
    return out if len(out) < 1200 else ""


def _office_word_available() -> bool:
    import sys

    if sys.platform != "win32":
        return False
    try:
        import winreg

        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CurVer")
        return True
    except OSError:
        return False


def _markdown_to_plain(md: str) -> str:
    import re

    lines: list[str] = []
    for line in (md or "").splitlines():
        s = re.sub(r"^#{1,6}\s+", "", line)
        s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
        s = re.sub(r"\*(.+?)\*", r"\1", s)
        s = re.sub(r"^\|[-: |]+\|$", "", s)
        if s.strip():
            lines.append(s)
    return "\n".join(lines)


def export_insights_report(markdown_text: str, *, out_dir: str, basename: str) -> tuple[str, str]:
    """Write insights report. Returns (path, format) where format is 'docx' or 'txt'."""
    os.makedirs(out_dir, exist_ok=True)
    if _office_word_available():
        docx_path = os.path.join(out_dir, f"{basename}.docx")
        try:
            from services.artifact_build import build_docx_from_markdown

            written = build_docx_from_markdown(
                markdown_text,
                docx_path,
                want_cover=False,
                want_toc=False,
            )
            if written.lower().endswith(".docx"):
                return written, "docx"
        except Exception:
            pass
    txt_path = os.path.join(out_dir, f"{basename}.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(_markdown_to_plain(markdown_text))
    return txt_path, "txt"


def _chart_source_frames(
    frames: dict[str, Any],
    mappings: list | None,
) -> list[tuple[str, Any]]:
    """Prefer joined frame when join keys exist; otherwise each selected frame."""
    out: list[tuple[str, Any]] = []
    if mappings:
        try:
            from services.data_studio.join import build_workspace_frame

            jr = build_workspace_frame(frames, mappings)
            if jr is not None and jr.frame is not None and not jr.frame.empty:
                out.append(("Joined datasets", jr.frame))
                return out
        except Exception:
            pass
    for name, df in list(frames.items())[:2]:
        if df is not None and not getattr(df, "empty", True):
            out.append((str(name), df))
    return out


def _friendly_chart_title(title: str) -> str:
    t = (title or "").replace("_", " ").strip()
    low = t.lower()
    if low.startswith("distribution "):
        return "Distribution of " + t[len("distribution "):]
    if low.startswith("avg "):
        return "Average " + t[len("avg "):]
    if " vs " in low:
        return t
    if low.startswith("pie "):
        return "Share of " + t[len("pie "):]
    return t[:1].upper() + t[1:] if t else "Chart"


def _auto_generate_charts(
    frames: dict[str, Any],
    *,
    mappings: list | None = None,
    out_dir: str | None = None,
    max_charts: int = 4,
    log_fn: Callable[[str], None] | None = None,
) -> list:
    """Render PNG charts from workspace frames (same heuristics as direct graph analysis)."""
    from services.graph_generation.plot import render_charts_for_dataset
    from services.graph_generation.report_layout import assign_chart_slots

    chart_dir = out_dir or os.path.join("data", "data_studio", "charts")
    os.makedirs(chart_dir, exist_ok=True)
    sources = _chart_source_frames(frames, mappings)
    if not sources:
        return []

    charts: list = []
    tmp_paths: list[str] = []
    try:
        for name, df in sources:
            if len(charts) >= max_charts:
                break
            fd, tmp = tempfile.mkstemp(suffix=".csv", prefix="ds_insights_")
            os.close(fd)
            tmp_paths.append(tmp)
            try:
                df.to_csv(tmp, index=False)
            except Exception:
                continue
            if log_fn:
                log_fn(f"Rendering charts for {name}…")
            remaining = max_charts - len(charts)
            charts.extend(
                render_charts_for_dataset(
                    tmp,
                    name=name,
                    query="analyse report",
                    output_dir=chart_dir,
                    max_charts=remaining,
                )
            )
    finally:
        for p in tmp_paths:
            try:
                os.remove(p)
            except OSError:
                pass

    for ch in charts:
        ch.title = _friendly_chart_title(ch.title)
    return assign_chart_slots(charts[:max_charts])


def _analysis_messages(facts_md: str, charts: list) -> list[dict[str, str]]:
    from services.graph_generation.report_layout import (
        analysis_hint_for_chart,
        assign_chart_slots,
        build_ordered_report_context,
    )

    charts = assign_chart_slots(list(charts or []))
    layout = build_ordered_report_context([], charts, query="Produce an analysis report from the spreadsheet")
    system = (
        "Role: Data Analyst.\n"
        "Write a prose Analysis section for a business insights report.\n"
        "Use ONLY numbers and facts from the digest below — do not invent values.\n"
        "Write with specific numbers, comparisons, and trends — never a bare data dump.\n"
        "Do NOT re-list raw per-record rows; summarize with aggregates.\n"
        "When figures are listed, reference each by number (Figure 1, Figure 2, …) "
        "and explain what it shows before moving to the next point.\n"
        "Follow the mandatory report layout for the Analysis body: "
        "Executive summary, then each Figure section with the image markdown line "
        "copied verbatim, then Conclusions.\n"
        "Start with `## Executive summary` (do not repeat Dataset Overview).\n"
        "Output markdown only."
    )
    figure_notes = "\n".join(
        f"- Figure {ch.slot}: {ch.title} ({ch.chart_type}) — {analysis_hint_for_chart(ch)}"
        for ch in charts
    ) or "- (no charts; write analysis from the digest alone)"
    user = (
        f"{layout}\n\n"
        f"### Figure focus notes\n{figure_notes}\n\n"
        f"### Facts digest (ground truth — do not contradict)\n{facts_md[:6000]}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _friendly_chart_title(title: str) -> str:
    t = (title or "").replace("_", " ").strip()
    low = t.lower()
    if low.startswith("distribution "):
        return "Distribution of " + t[len("distribution "):]
    if low.startswith("avg "):
        return "Average " + t[len("avg "):]
    if low.startswith("scatter "):
        return t[len("scatter "):]
    if low.startswith("pie "):
        rest = t[len("pie "):]
        if " by " in rest.lower():
            return "Share of " + rest
        return "Share of " + rest
    if low.startswith("share of "):
        return t[:1].upper() + t[1:]
    return t[:1].upper() + t[1:] if t else "Chart"


def _strip_markdown_images(md: str) -> str:
    import re

    return re.sub(r"!\[[^\]]*\]\([^)]+\)\s*", "", md or "")


def _demote_headings(md: str, *, levels: int = 1) -> str:
    """Shift markdown headings down so they nest under a parent ## section."""
    out: list[str] = []
    for line in (md or "").splitlines():
        if line.startswith("#"):
            hashes = len(line) - len(line.lstrip("#"))
            rest = line[hashes:]
            if rest.startswith(" "):
                out.append("#" * min(hashes + levels, 6) + rest)
                continue
        out.append(line)
    return "\n".join(out)


def _append_analysis_section(
    facts_md: str,
    *,
    chat_fn: Callable[[list[dict[str, str]]], str],
    charts: list,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    """LLM analysis grounded in facts + charts; returns facts + ## Analysis block."""
    from services.graph_generation.report_layout import finalize_report_markdown

    if log_fn:
        log_fn("Writing analysis report…")
    try:
        raw = (chat_fn(_analysis_messages(facts_md, charts)) or "").strip()
    except Exception:  # noqa: BLE001 — analysis is optional if LLM fails
        raw = ""
    if not raw:
        if not charts:
            return facts_md
        stub_parts = ["### Executive summary", "", "See the factual digest above.", ""]
        for ch in charts:
            stub_parts.append(f"### Figure {ch.slot}: {ch.title}")
            stub_parts.append("")
            stub_parts.append(
                f"![Figure {ch.slot}: {ch.title}]({os.path.abspath(ch.path).replace(chr(92), '/')})"
            )
            stub_parts.append("")
        stub_parts.extend([
            "### Conclusions",
            "",
            "Generate insights again with a chat model for full narrative analysis.",
        ])
        return facts_md.rstrip() + "\n\n## Analysis\n\n" + "\n".join(stub_parts).strip() + "\n"

    lines = raw.splitlines()
    if lines and lines[0].startswith("# ") and not lines[0].startswith("## "):
        raw = "\n".join(lines[1:]).strip()
    # Drop model-invented image paths; finalize_report_markdown injects real PNGs.
    raw = _strip_markdown_images(raw)
    analysis_body = finalize_report_markdown(raw, charts) if charts else raw
    analysis_body = _demote_headings(analysis_body, levels=1)
    return facts_md.rstrip() + "\n\n## Analysis\n\n" + analysis_body.strip() + "\n"


def generate_insights(
    frames: dict[str, Any],
    *,
    chat_fn: Callable[[list[dict[str, str]]], str],
    mappings: list | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[str, dict]:
    """Return (markdown_report, raw_stats).

    Template facts first, then an LLM Analysis section supported by auto-generated charts.
    """
    if not frames:
        return "", {}
    if log_fn:
        log_fn("Computing dataset statistics…")
    stats = compute_stats(frames, mappings=mappings)
    report = render_insights_markdown(stats)
    if log_fn:
        log_fn("Adding short highlights…")
    try:
        raw = chat_fn(_highlights_messages(report))
        highlights = _clean_highlights(raw or "")
        if highlights:
            report = "## Highlights\n\n" + highlights + "\n\n" + report
    except Exception:  # noqa: BLE001 — highlights are optional
        pass

    charts = _auto_generate_charts(
        frames, mappings=mappings, max_charts=4, log_fn=log_fn
    )
    report = _append_analysis_section(
        report, chat_fn=chat_fn, charts=charts, log_fn=log_fn
    )
    return report.strip() + "\n", stats
