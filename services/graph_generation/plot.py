# -*- coding: utf-8 -*-
"""Render chart PNGs with matplotlib (local, no LLM)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from pipeline.i18n import t as _tr
from services.graph_generation.dataset import load_dataframe
from services.graph_generation.models import ChartArtifact

_PROFILE_SAMPLE_ROWS = 50_000


def _safe_filename(title: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (title or "chart").lower()).strip("_")
    return (s[:48] or "chart")


def _norm_col(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (name or "").lower()).strip()


def _pick_column(columns: list[str], *hint_groups: tuple[str, ...]) -> str | None:
    for hints in hint_groups:
        for col in columns:
            n = _norm_col(col)
            if any(h in n for h in hints):
                return col
    return None


def _fmt_num(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    if f == int(f) and abs(f) < 1_000_000:
        return f"{int(f):,}"
    return f"{f:,.2f}"


def _grouped_summary(grp, *, unit: str = "") -> str:
    """Real top values from a groupby Series, e.g. 'TikTok: 12.4, Instagram: 6.8, ...'."""
    parts = [f"{idx}: {_fmt_num(val)}{unit}" for idx, val in grp.head(5).items()]
    return ", ".join(parts)


def _distribution_summary(series) -> str:
    return (
        f"min {_fmt_num(series.min())}, median {_fmt_num(series.median())}, "
        f"mean {_fmt_num(series.mean())}, max {_fmt_num(series.max())}, n={len(series)}"
    )


def _scatter_summary(sub, x_col: str, y_col: str) -> str:
    try:
        corr = sub[x_col].astype(float).corr(sub[y_col].astype(float))
        return f"correlation r={corr:.2f} across {len(sub)} points"
    except Exception:
        return f"{len(sub)} points plotted"


def _timeseries_summary(sub, date_col: str, metric_col: str) -> str:
    first_v, last_v = sub[metric_col].iloc[0], sub[metric_col].iloc[-1]
    direction = "up" if last_v > first_v else "down" if last_v < first_v else "flat"
    return (
        f"{_fmt_num(first_v)} → {_fmt_num(last_v)} ({direction}) from "
        f"{sub[date_col].iloc[0].date()} to {sub[date_col].iloc[-1].date()}"
    )


def _coerce_numeric_frame(df):
    import pandas as pd

    out = df.copy()
    for col in out.columns:
        if str(out[col].dtype) in ("int64", "float64", "int32", "float32"):
            continue
        sample = out[col].dropna().astype(str).head(200)
        if sample.empty:
            continue
        if sample.str.contains(r"^\s*[\d,]+\.?\d*\s*%?\s*$", regex=True).mean() > 0.5:
            cleaned = (
                out[col]
                .astype(str)
                .str.replace(",", "", regex=False)
                .str.replace("%", "", regex=False)
                .str.strip()
            )
            converted = pd.to_numeric(cleaned, errors="coerce")
            if converted.notna().sum() >= max(3, int(0.4 * len(out))):
                out[col] = converted
    return out


def render_charts_for_dataset(
    path: str,
    *,
    name: str = "",
    query: str = "",
    output_dir: str,
    max_charts: int = 4,
) -> list[ChartArtifact]:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from services.graph_generation.fonts import configure_chart_fonts

        configure_chart_fonts()
    except ImportError:
        return []

    os.makedirs(output_dir, exist_ok=True)
    df = _coerce_numeric_frame(load_dataframe(path, max_rows=_PROFILE_SAMPLE_ROWS))
    display = name or os.path.basename(path)
    ql = (query or "").lower()
    all_cols = [str(c) for c in df.columns if not str(c).lower().startswith("unnamed")]

    numeric = [
        c
        for c in all_cols
        if str(df[c].dtype) in ("int64", "float64", "int32", "float32")
        and df[c].notna().sum() >= 3
        and df[c].nunique(dropna=True) > 1
    ]
    object_cols = [c for c in all_cols if c not in numeric]

    platform_col = _pick_column(all_cols, ("platform", "channel", "source", "network"))
    metric_col = _pick_column(
        numeric,
        ("engagement rate", "engagement"),
        ("likes", "reactions"),
        ("impressions", "views"),
        ("reach",),
        ("comments",),
        ("shares",),
    )
    count_metric = _pick_column(
        numeric,
        ("impressions", "views"),
        ("likes",),
        ("reach",),
    ) or (numeric[0] if numeric else None)
    date_col = _pick_column(all_cols, ("post date", "date", "time", "month", "year"))

    artifacts: list[ChartArtifact] = []
    stem = Path(display).stem

    def _save(fig, title: str, chart_type: str, caption: str = "", data_summary: str = "") -> None:
        if len(artifacts) >= max_charts:
            plt.close(fig)
            return
        fname = f"{stem}_{_safe_filename(title)}.png"
        out = os.path.join(output_dir, fname)
        fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        artifacts.append(
            ChartArtifact(
                path=out,
                title=title,
                chart_type=chart_type,
                caption=caption,
                data_summary=data_summary,
            )
        )

    # 1) Metric by platform (most useful for social / marketing sheets)
    if platform_col and metric_col and len(artifacts) < max_charts:
        try:
            grp = (
                df.groupby(platform_col, dropna=True)[metric_col]
                .mean()
                .sort_values(ascending=False)
                .head(12)
            )
            fig, ax = plt.subplots(figsize=(9, 4.5))
            grp.plot(kind="bar", ax=ax, color="#2563eb")
            ax.set_title(_tr("chart.avg_by", metric=metric_col, group=platform_col))
            ax.set_ylabel(metric_col)
            ax.tick_params(axis="x", rotation=30)
            fig.tight_layout()
            _save(
                fig,
                f"avg_{metric_col}_by_{platform_col}",
                "bar_grouped",
                caption=_tr("chart.avg_caption", metric=metric_col, group=platform_col),
                data_summary=_grouped_summary(grp),
            )
        except Exception:
            pass

    # 2) Distribution of a key volume metric
    if count_metric and len(artifacts) < max_charts and (
        "histogram" in ql or "distribution" in ql or "analyse" in ql or "analyze" in ql or not ql
    ):
        fig, ax = plt.subplots(figsize=(8, 4))
        series = df[count_metric].dropna()
        if len(series) > 0:
            ax.hist(series, bins=min(30, max(10, len(series) // 40)), color="#0d9488", edgecolor="white")
            ax.set_title(_tr("chart.distribution", metric=count_metric))
            ax.set_xlabel(count_metric)
            _save(
                fig,
                f"distribution_{count_metric}",
                "histogram",
                caption=_tr("chart.distribution_caption", metric=count_metric),
                data_summary=_distribution_summary(series),
            )
        else:
            plt.close(fig)

    # 3) Pie or share breakdown when a category splits a metric
    cat_col = platform_col or _pick_column(
        object_cols, ("category", "type", "status", "region", "department", "sku", "product")
    ) or (object_cols[0] if object_cols else None)
    share_metric = metric_col or count_metric
    if cat_col and share_metric and len(artifacts) < max_charts:
        try:
            grp = df.groupby(cat_col, dropna=True)[share_metric].sum().sort_values(ascending=False).head(8)
            if 2 <= len(grp) <= 8:
                fig, ax = plt.subplots(figsize=(7, 5))
                grp.plot(
                    kind="pie",
                    ax=ax,
                    autopct="%1.0f%%",
                    ylabel="",
                    legend=False,
                    colormap="tab20",
                )
                ax.set_title(_tr("chart.share", metric=share_metric, cat=cat_col))
                ax.set_ylabel("")
                total = grp.sum()
                pct_grp = (grp / total * 100) if total else grp
                _save(
                    fig,
                    f"pie_{share_metric}_by_{cat_col}",
                    "pie",
                    caption=_tr("chart.share_caption", metric=share_metric, cat=cat_col),
                    data_summary=_grouped_summary(pct_grp, unit="%"),
                )
        except Exception:
            pass

    # 4) Scatter (+ regression) for two numeric columns
    if len(numeric) >= 2 and len(artifacts) < max_charts:
        try:
            import numpy as np

            x_col = _pick_column(numeric, ("impressions", "views", "reach", "quantity", "stock", "price"))
            y_col = _pick_column(
                [c for c in numeric if c != x_col],
                ("engagement", "likes", "sales", "revenue", "cost", "value"),
            ) or [c for c in numeric if c != x_col][0]
            if not x_col:
                x_col, y_col = numeric[0], numeric[1]
            sub = df[[x_col, y_col]].dropna().head(2500)
            if len(sub) >= 8:
                fig, ax = plt.subplots(figsize=(8, 5))
                ax.scatter(sub[x_col], sub[y_col], alpha=0.45, s=14, color="#0d9488")
                if len(sub) >= 10:
                    coeffs = np.polyfit(sub[x_col].astype(float), sub[y_col].astype(float), 1)
                    xs = np.linspace(sub[x_col].min(), sub[x_col].max(), 50)
                    ax.plot(xs, coeffs[0] * xs + coeffs[1], color="#dc2626", linewidth=2, label=_tr("chart.trend_legend"))
                    ax.legend(loc="best", fontsize=8)
                ax.set_xlabel(x_col)
                ax.set_ylabel(y_col)
                ax.set_title(_tr("chart.vs", y=y_col, x=x_col))
                _save(
                    fig,
                    f"scatter_{y_col}_vs_{x_col}",
                    "scatter",
                    caption=_tr("chart.relationship_caption", x=x_col, y=y_col),
                    data_summary=_scatter_summary(sub, x_col, y_col),
                )
        except Exception:
            pass

    # 5) Time series for a metric when a date-like column exists
    if date_col and metric_col and len(artifacts) < max_charts:
        try:
            import pandas as pd

            sub = df[[date_col, metric_col]].copy()
            sub[date_col] = pd.to_datetime(sub[date_col], errors="coerce")
            sub = sub.dropna().sort_values(date_col).tail(500)
            if len(sub) >= 3:
                fig, ax = plt.subplots(figsize=(9, 4))
                ax.plot(sub[date_col], sub[metric_col], color="#dc2626", linewidth=1.5)
                ax.set_title(_tr("chart.over_time", metric=metric_col))
                ax.tick_params(axis="x", rotation=25)
                fig.tight_layout()
                _save(
                    fig,
                    f"{metric_col}_over_time",
                    "line",
                    caption=_tr("chart.trend_caption", metric=metric_col, date=date_col),
                    data_summary=_timeseries_summary(sub, date_col, metric_col),
                )
        except Exception:
            pass

    # Fallbacks when heuristics found nothing useful
    if not artifacts and numeric:
        col = numeric[0]
        fig, ax = plt.subplots(figsize=(8, 4))
        series = df[col].dropna()
        ax.hist(series, bins=min(30, max(10, len(series) // 50)), color="#2563eb", edgecolor="white")
        ax.set_title(_tr("chart.column_distribution", name=display, col=col))
        _save(fig, f"distribution_{col}", "histogram", data_summary=_distribution_summary(series))

    return artifacts


def render_charts_from_specs(
    path: str,
    specs: list[dict],
    *,
    name: str = "",
    output_dir: str,
) -> list[ChartArtifact]:
    """Render charts from LLM-planned specs (exact column names)."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from services.graph_generation.fonts import configure_chart_fonts

        configure_chart_fonts()
    except ImportError:
        return []

    if not specs:
        return []

    os.makedirs(output_dir, exist_ok=True)
    df = _coerce_numeric_frame(load_dataframe(path, max_rows=_PROFILE_SAMPLE_ROWS))
    display = name or os.path.basename(path)
    stem = Path(display).stem
    artifacts: list[ChartArtifact] = []

    def _save(fig, title: str, chart_type: str, caption: str = "") -> None:
        fname = f"{stem}_{_safe_filename(title)}.png"
        out = os.path.join(output_dir, fname)
        fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="white")
        plt.close(fig)
        artifacts.append(
            ChartArtifact(path=out, title=title, chart_type=chart_type, caption=caption)
        )

    for spec in specs[:6]:
        chart_type = str(spec.get("chart_type") or "bar").lower()
        x_col = str(spec.get("x_col") or "")
        y_col = str(spec.get("y_col") or "")
        title = str(spec.get("title") or "Chart")
        caption = str(spec.get("caption") or "")
        try:
            if chart_type == "histogram" and y_col and y_col in df.columns:
                fig, ax = plt.subplots(figsize=(8, 4))
                series = df[y_col].dropna()
                ax.hist(series, bins=min(30, max(10, len(series) // 40)), color="#2563eb")
                ax.set_title(title)
                ax.set_xlabel(spec.get("x_label") or y_col)
                _save(fig, title, "histogram", caption)
            elif chart_type == "pie" and x_col and y_col and x_col in df.columns and y_col in df.columns:
                grp = df.groupby(x_col, dropna=True)[y_col].sum().sort_values(ascending=False).head(8)
                if len(grp) >= 2:
                    fig, ax = plt.subplots(figsize=(7, 5))
                    grp.plot(kind="pie", ax=ax, autopct="%1.0f%%", ylabel="")
                    ax.set_title(title)
                    _save(fig, title, "pie", caption)
            elif chart_type == "scatter" and x_col and y_col and x_col in df.columns and y_col in df.columns:
                sub = df[[x_col, y_col]].dropna().head(2500)
                if len(sub) >= 5:
                    fig, ax = plt.subplots(figsize=(8, 5))
                    ax.scatter(sub[x_col], sub[y_col], alpha=0.45, s=14, color="#0d9488")
                    ax.set_xlabel(spec.get("x_label") or x_col)
                    ax.set_ylabel(spec.get("y_label") or y_col)
                    ax.set_title(title)
                    _save(fig, title, "scatter", caption)
            elif chart_type == "line" and x_col and y_col and x_col in df.columns and y_col in df.columns:
                import pandas as pd

                sub = df[[x_col, y_col]].copy()
                sub[x_col] = pd.to_datetime(sub[x_col], errors="coerce")
                sub = sub.dropna().sort_values(x_col).tail(500)
                if len(sub) >= 3:
                    fig, ax = plt.subplots(figsize=(9, 4))
                    ax.plot(sub[x_col], sub[y_col], color="#dc2626", linewidth=1.5)
                    ax.set_title(title)
                    ax.tick_params(axis="x", rotation=25)
                    fig.tight_layout()
                    _save(fig, title, "line", caption)
            elif x_col and y_col and x_col in df.columns and y_col in df.columns:
                grp = (
                    df.groupby(x_col, dropna=True)[y_col]
                    .mean()
                    .sort_values(ascending=False)
                    .head(12)
                )
                fig, ax = plt.subplots(figsize=(9, 4.5))
                grp.plot(kind="bar", ax=ax, color="#2563eb")
                ax.set_title(title)
                ax.set_xlabel(spec.get("x_label") or x_col)
                ax.set_ylabel(spec.get("y_label") or y_col)
                ax.tick_params(axis="x", rotation=30)
                fig.tight_layout()
                _save(fig, title, "bar", caption)
        except Exception:
            continue

    return artifacts
