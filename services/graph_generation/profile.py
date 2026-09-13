# -*- coding: utf-8 -*-
"""Statistical profiling for large tabular datasets (no full dump to LLM)."""
from __future__ import annotations

import json
import os

from services.graph_generation.dataset import load_dataframe
from services.graph_generation.models import DatasetProfile

# Rows used for charts; stats computed on sample when larger.
_PROFILE_SAMPLE_ROWS = 50_000
_PREVIEW_ROWS = 8


def _render_group_aggregates_md(aggs: list[dict]) -> str:
    """Category breakdowns as literal markdown tables — the model copies these verbatim
    instead of reconstructing per-category numbers from memory (which is how a report ends up
    attaching the wrong count/average to the wrong category)."""
    if not aggs:
        return ""
    parts = [
        "### Category breakdowns (exact — copy the table(s) you use verbatim into the report; "
        "never restate or recompute these numbers from memory)\n"
    ]
    for agg in aggs:
        by, metric = agg["by"], agg["metric"]
        counts = agg.get("count") or {}
        cats = sorted(agg["mean"].keys(), key=lambda k: agg["mean"][k], reverse=True)
        parts.append(f"**{metric} by {by}**\n")
        parts.append(f"| {by} | Posts | Mean {metric} | Sum {metric} |")
        parts.append("| :--- | ---: | ---: | ---: |")
        for cat in cats:
            parts.append(
                f"| {cat} | {counts.get(cat, '')} | {agg['mean'][cat]} | {agg['sum'].get(cat, '')} |"
            )
        parts.append("")
    return "\n".join(parts)


def _render_citable_examples_md(examples: list[dict]) -> str:
    """The only specific rows safe to name in prose — an explicit whitelist rather than trusting
    the model not to invent or misattribute details about a row it half-remembers."""
    if not examples:
        return ""
    cols = list(examples[0].keys())
    lines = [
        "### Citable examples (the ONLY specific rows you may name in prose — never invent an "
        "ID or state details about any other row)\n",
        f"| {' | '.join(cols)} |",
        f"| {' | '.join(':---' for _ in cols)} |",
    ]
    for ex in examples:
        lines.append(f"| {' | '.join(str(ex.get(c, '')) for c in cols)} |")
    return "\n".join(lines)


def _render_date_ranges_md(ranges: dict) -> str:
    if not ranges:
        return ""
    lines = ["### Date ranges (exact)\n"]
    for col, r in ranges.items():
        lines.append(f"- **{col}**: {r['min']} to {r['max']} ({r['span_days']} days)")
    return "\n".join(lines)


def _render_outliers_md(outliers: dict) -> str:
    if not outliers:
        return ""
    lines = ["### Outlier counts (IQR method, exact)\n"]
    for col, o in outliers.items():
        bits = [f"{o['count']} outliers ({o['pct']}% of rows)"]
        if "high_count" in o:
            bits.append(f"{o['high_count']} above {o['upper_bound']}")
        if "low_count" in o:
            bits.append(f"{o['low_count']} below {o['lower_bound']}")
        lines.append(f"- **{col}**: " + "; ".join(bits))
    return "\n".join(lines)


def _render_correlations_md(pairs: list[dict]) -> str:
    if not pairs:
        return ""
    lines = ["### Strongest correlations (exact)\n"]
    for p in pairs:
        lines.append(f"- **{p['a']}** vs **{p['b']}**: r = {p['r']}")
    return "\n".join(lines)


def render_dataset_overview_md(entry: dict, *, display: str, row_count: int) -> str:
    """Deterministic 'Dataset Overview' prose, generated entirely from computed stats — the
    model never authors these specific facts, so it can't misstate them (e.g. calling a
    24-month span an 'eight-month period', which happened even when the correct span was
    already present in context). This section is inserted into the report by code, not
    written by the LLM, and is the authoritative reference for these facts."""
    sentences = [f"This report is based on **{row_count:,}** records from **{display}**."]

    for col, r in (entry.get("date_ranges") or {}).items():
        months = round(r["span_days"] / 30.44, 1)
        sentences.append(
            f"The **{col}** column spans **{r['min']}** to **{r['max']}** "
            f"({r['span_days']} days, approximately {months} months)."
        )

    corr = entry.get("top_correlations") or []
    if corr:
        top = corr[0]
        sentences.append(
            f"The strongest relationship in the data is between **{top['a']}** and "
            f"**{top['b']}** (r = {top['r']})."
        )

    outliers = entry.get("outliers") or {}
    if outliers:
        bits = [f"{o['count']} in **{col}** ({o['pct']}%)" for col, o in outliers.items()]
        sentences.append("Outlier counts (IQR method): " + "; ".join(bits) + ".")

    return "## Dataset Overview\n\n" + " ".join(sentences) + "\n"


def profile_dataset(path: str, *, name: str = "") -> DatasetProfile:
    display = name or os.path.basename(path)

    df = load_dataframe(path, max_rows=_PROFILE_SAMPLE_ROWS)
    row_count = len(df)
    sampled = row_count >= _PROFILE_SAMPLE_ROWS

    columns: list[dict] = []
    for col in df.columns:
        series = df[col]
        info: dict = {"name": str(col), "dtype": str(series.dtype)}
        non_null = int(series.notna().sum())
        info["non_null"] = non_null
        info["null_pct"] = round(100.0 * (1 - non_null / max(row_count, 1)), 2)
        if str(series.dtype) in ("int64", "float64", "int32", "float32"):
            desc = series.describe().to_dict()
            info["stats"] = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in desc.items()}
        else:
            vc = series.astype(str).value_counts().head(8)
            info["top_values"] = {str(k): int(v) for k, v in vc.items()}
        columns.append(info)

    preview = df.head(_PREVIEW_ROWS).to_dict(orient="records")

    from services.data_studio.insights import compute_stats

    computed = compute_stats({display: df})
    computed_entry = (computed.get("datasets") or [{}])[0]
    computed_sections = "\n\n".join(
        filter(
            None,
            [
                _render_date_ranges_md(computed_entry.get("date_ranges")),
                _render_outliers_md(computed_entry.get("outliers")),
                _render_correlations_md(computed_entry.get("top_correlations")),
                _render_group_aggregates_md(computed_entry.get("group_aggregates")),
                _render_citable_examples_md(computed_entry.get("citable_examples")),
            ],
        )
    )

    summary_md = (
        f"## Dataset: {display}\n\n"
        f"- Rows (profiled): **{row_count:,}**"
        + (" (sample cap applied)" if sampled else "")
        + f"\n- Columns: **{len(columns)}**\n\n"
        f"### Column summary\n```json\n{json.dumps(columns, indent=2, default=str)[:12000]}\n```\n\n"
        + (f"{computed_sections}\n\n" if computed_sections else "")
        + "### Sample rows (context only — for named/cited examples use 'Citable examples' above, "
        "never a row from here)\n"
        f"```json\n{json.dumps(preview, indent=2, default=str)[:6000]}\n```\n"
    )

    overview_md = render_dataset_overview_md(computed_entry, display=display, row_count=row_count)

    return DatasetProfile(
        name=display,
        path=path,
        row_count=row_count,
        column_count=len(columns),
        columns=columns,
        summary_md=summary_md,
        sampled=sampled,
        overview_md=overview_md,
        computed_stats=computed_entry,
    )
