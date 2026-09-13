# -*- coding: utf-8 -*-
"""Export Data Studio dashboard charts as PNG for workspace chat."""
from __future__ import annotations

import os
import re
import time
from typing import Any


def _safe_stem(title: str) -> str:
    s = re.sub(r"[^\w\-]+", "_", (title or "chart").lower()).strip("_")
    return (s[:48] or "chart")


def export_echart_png(opts: dict[str, Any], *, title: str = "") -> str | None:
    """Render an ECharts option dict to a PNG file. Returns the path or None."""
    if not opts:
        return None
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    os.makedirs(os.path.join("data", "charts"), exist_ok=True)
    out = os.path.join("data", "charts", f"ds_{_safe_stem(title)}_{int(time.time())}.png")
    chart_title = title or (opts.get("title") or {}).get("text") or "Chart"
    series = opts.get("series") or []
    if not series:
        return None

    fig, ax = plt.subplots(figsize=(12, 6.5))
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#0f172a")
    ax.tick_params(colors="#94a3b8", labelsize=10)
    ax.title.set_color("#94a3b8")
    ax.title.set_fontsize(13)
    ax.set_title(chart_title)
    for spine in ax.spines.values():
        spine.set_color((1, 1, 1, 0.15))

    first = series[0]
    ctype = str(first.get("type") or "line")

    if ctype == "pie":
        data = first.get("data") or []
        names = [str(d.get("name", "")) for d in data]
        values = [float(d.get("value") or 0) for d in data]
        if not names:
            plt.close(fig)
            return None
        ax.pie(values, labels=names, textprops={"color": "#94a3b8", "fontsize": 10})
    elif ctype == "scatter":
        pts = first.get("data") or []
        xs = [float(p[0]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        ys = [float(p[1]) for p in pts if isinstance(p, (list, tuple)) and len(p) >= 2]
        if not xs:
            plt.close(fig)
            return None
        ax.scatter(xs, ys, color="#38bdf8", alpha=0.65, s=18)
        x_axis = opts.get("xAxis") or {}
        y_axis = opts.get("yAxis") or {}
        if x_axis.get("name"):
            ax.set_xlabel(str(x_axis["name"]), color="#94a3b8", fontsize=11)
        if y_axis.get("name"):
            ax.set_ylabel(str(y_axis["name"]), color="#94a3b8", fontsize=11)
    else:
        labels = list((opts.get("xAxis") or {}).get("data") or [])
        if not labels:
            plt.close(fig)
            return None
        x = range(len(labels))
        width = 0.8 / max(len(series), 1)
        for i, s in enumerate(series):
            vals = [0 if v is None else float(v) for v in (s.get("data") or [])]
            offset = (i - (len(series) - 1) / 2) * width
            if ctype == "bar":
                ax.bar([xi + offset for xi in x], vals, width=width, label=str(s.get("name") or ""))
            else:
                ax.plot(x, vals, marker="o", markersize=3, linewidth=1.5, label=str(s.get("name") or ""))
        ax.set_xticks(list(x))
        ax.set_xticklabels(labels, rotation=35 if len(labels) > 8 else 0, ha="right")
        if len(series) > 1:
            ax.legend(fontsize=10, facecolor="#0f172a", edgecolor=(1, 1, 1, 0.15), labelcolor="#94a3b8")

    ax.grid(True, color=(1, 1, 1, 0.06), linewidth=0.6)
    fig.tight_layout()
    fig.savefig(out, dpi=140, bbox_inches="tight", facecolor="#0f172a")
    plt.close(fig)
    return out
