# -*- coding: utf-8 -*-
"""Real data-driven charts (bar/line/pie) for arbitrary requests with no backing
spreadsheet — e.g. "line chart of unemployment over the last decade" from a web
search, or "bar chart comparing X and Y" from an attached document.

services/graph_generation/plot.py already renders real charts, but only against an
actual dataframe loaded from a file on disk (render_charts_for_dataset/
render_charts_from_specs both call load_dataframe(path, ...)) — there's no path
for a handful of LLM-extracted label/value pairs with no file behind them. This
module fills that gap with matplotlib directly, styled to match the app's theme
rather than matplotlib defaults, following the same author-then-render pattern as
services/diagram_generation.py and services/infographic_generation.py (including
grounding via pipeline/base/grounding.py — a chart states facts, same as an
infographic)."""
from __future__ import annotations

import json
import re

from services.image_generation import GENERATED_IMAGE_DIR, ImageGenerationResult, unique_output_path

_CHART_AUTHOR_SYSTEM = """You are LOMA's Chart Author. Return ONLY valid JSON (no markdown fences).

Given the user's chart/graph/plot request, produce:
{
  "title": "short chart title, in the SAME language as the user's request",
  "chart_type": "bar" | "line" | "pie",
  "x_label": "x-axis label, or empty string if not applicable (e.g. pie charts)",
  "y_label": "y-axis label, or empty string if not applicable",
  "series": [
    {"label": "short category/point label", "value": <number>}
  ]
}

Rules:
- 3 to 12 data points. Use ONLY real figures from the grounding material given
  below — every value must be directly supported by it. Never invent, estimate,
  round, or extrapolate a number that isn't stated there.
- chart_type: "line" for a trend over time/an ordered sequence, "bar" for
  comparing discrete categories, "pie" for parts of a whole (values should
  roughly sum to a meaningful total, e.g. percentages).
- title/labels must be in the user's own language, not translated to English.
"""


def _parse_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


_VALID_CHART_TYPES = {"bar", "line", "pie"}

_NUMBER_TOKEN_RE = re.compile(r"\d[\d,]*\.?\d*")


def _context_numbers(context: str) -> set[float]:
    numbers: set[float] = set()
    for tok in _NUMBER_TOKEN_RE.findall(context or ""):
        try:
            numbers.add(float(tok.replace(",", "")))
        except ValueError:
            continue
    return numbers


def _values_supported_by_context(series: list[dict], context: str, *, min_fraction: float = 0.5) -> bool:
    """At least `min_fraction` of the authored values must appear as a real
    number somewhere in the grounding text (tolerating float/int display and
    small rounding differences) — otherwise the model filled the JSON schema
    with plausible-looking numbers instead of transcribing real ones. Note:
    this check is weak for small values (single/double digits coincidentally
    appear almost anywhere — dates, list markers, section numbers) — it's a
    second layer, not a substitute for keeping irrelevant pages out of
    `context` in the first place (see pipeline/base/source_relevance.py's
    min_keyword_overlap)."""
    if not series:
        return False
    context_numbers = _context_numbers(context)
    if not context_numbers:
        return False
    supported = 0
    for pt in series:
        value = pt.get("value")
        if not isinstance(value, (int, float)):
            continue
        if any(
            abs(value - n) < 0.05 or (n != 0 and abs(value - n) / abs(n) < 0.02)
            for n in context_numbers
        ):
            supported += 1
    return (supported / len(series)) >= min_fraction


def _looks_synthetic_sequence(series: list[dict]) -> bool:
    """A perfectly arithmetic sequence (identical step between every
    consecutive value, e.g. 1, 2, 3, ... 11) is a strong tell the model filled
    the schema with a plausible-looking progression instead of transcribing
    real, messy numbers — confirmed via a real run: a "disaster frequency"
    chart rendered exactly 1 through 11 across 2014-2024, sourced (per its own
    citation) from a page about daily world temperature records that never
    mentioned disaster counts at all. Real-world data essentially never lands
    on a perfect straight line across many points."""
    values = [pt.get("value") for pt in series if isinstance(pt.get("value"), (int, float))]
    if len(values) < 4:
        return False
    diffs = {round(values[i + 1] - values[i], 6) for i in range(len(values) - 1)}
    return len(diffs) == 1 and next(iter(diffs)) != 0


def _author_chart(user_query: str, prof: dict, model: str, context: str = "") -> dict:
    if not (context or "").strip():
        # No attached-source excerpt or web search result to draw real numbers from
        # — refuse rather than let the model invent a plausible-looking series (see
        # marker_visual._try_chart's caller, which falls back to a plain qualitative
        # photo on any exception here instead of rendering fabricated data).
        raise ValueError("No grounding material available — refusing to fabricate chart data.")

    from pipeline.capability_runtime.chat_runner import generate_text_sync

    user_content = user_query
    if context:
        user_content = (
            f"{user_query}\n\nGround your data in this material — do not state a "
            f"value that isn't supported by it:\n{context}"
        )

    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _CHART_AUTHOR_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        disable_thinking=True,
    )
    data = _parse_json(raw) or {}
    series = []
    for pt in (data.get("series") or [])[:12]:
        if not isinstance(pt, dict):
            continue
        label = str(pt.get("label") or "").strip()
        try:
            value = float(pt.get("value"))
        except (TypeError, ValueError):
            continue
        if not label:
            continue
        series.append({"label": label, "value": value})

    chart_type = str(data.get("chart_type") or "bar").strip().lower()
    if chart_type not in _VALID_CHART_TYPES:
        chart_type = "bar"
    if not series:
        raise ValueError("Chart author returned no usable data points — refusing to render an empty/fabricated chart.")
    if _looks_synthetic_sequence(series):
        raise ValueError(
            "Authored series is a perfectly arithmetic sequence — refusing as likely fabricated rather than transcribed."
        )
    if not _values_supported_by_context(series, context):
        raise ValueError(
            "Authored chart values don't trace to any real number in the grounding material — "
            "refusing to fabricate chart data."
        )

    return {
        "title": str(data.get("title") or "").strip(),
        "chart_type": chart_type,
        "x_label": str(data.get("x_label") or "").strip(),
        "y_label": str(data.get("y_label") or "").strip(),
        "series": series,
    }


def generate_chart(
    user_query: str, *, prof: dict, model: str, output_path: str | None = None, context: str = "",
    sources: list[dict[str, str]] | None = None,
) -> ImageGenerationResult:
    import os

    from pipeline.deliverables.presentation_theme import infer_palette_from_query, resolve_theme

    spec = _author_chart(user_query, prof, model, context)
    theme = resolve_theme(query=user_query, design={"palette": infer_palette_from_query(user_query)})

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        stem = re.sub(r"[^a-z0-9]+", "_", (user_query or "chart").lower())[:40].strip("_") or "chart"
        output_path = unique_output_path(stem + ".png", "chart")

    _render_ad_hoc_chart(spec, theme, output_path, sources=sources)

    from PIL import Image

    with Image.open(output_path) as img:
        w, h = img.size
    return ImageGenerationResult(output_path, user_query, 0, w, h, 0, 0.0, "chart-render")


def _mpl_font_prop(text: str, *, bold: bool = False):
    """A matplotlib FontProperties for `text`, picking the same bundled CJK/Latin
    Noto Sans face services/poster_fonts.py uses elsewhere, so chart text renders
    correctly regardless of UI locale instead of falling back to matplotlib's
    default (Latin-only, tofu boxes on CJK)."""
    from matplotlib.font_manager import FontProperties

    from services.poster_fonts import _font_path_for_text

    path = _font_path_for_text(text)
    if not path:
        return FontProperties(weight="bold" if bold else "normal")
    return FontProperties(fname=path, weight="bold" if bold else "normal")


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(rgb)


def _render_ad_hoc_chart(
    spec: dict, theme, output_path: str, *, sources: list[dict[str, str]] | None = None
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bg = tuple(theme.background_rgb)
    primary = tuple(theme.primary_rgb)
    accent = tuple(theme.accent_rgb)
    title_color = tuple(theme.title_rgb)
    body_color = tuple(theme.body_rgb)

    labels = [pt["label"] for pt in spec["series"]]
    values = [pt["value"] for pt in spec["series"]]
    title = spec["title"]

    fig, ax = plt.subplots(figsize=(9, 5.5), dpi=150)
    fig.patch.set_facecolor(_hex(bg))
    ax.set_facecolor(_hex(bg))

    chart_type = spec["chart_type"]
    if chart_type == "pie":
        colors = [_hex(primary), _hex(accent)] + [
            _hex(tuple(min(255, c + 40 * i) for c in primary)) for i in range(1, max(1, len(values)))
        ]
        wedge_colors = colors[: len(values)]
        wedges, _texts, autotexts = ax.pie(
            values, labels=labels, autopct="%1.0f%%", colors=wedge_colors,
            textprops={"color": _hex(body_color)}, wedgeprops={"edgecolor": _hex(bg), "linewidth": 2},
        )
        for lbl in _texts:
            lbl.set_fontproperties(_mpl_font_prop(lbl.get_text()))
        # The percentage label sits ON the wedge fill, not the page background, so
        # it needs its own contrast check per wedge instead of the page's fixed
        # body_color — a light label on a light wedge (or dark-on-dark) is
        # otherwise unreadable, same adaptive-contrast idea used for posters.
        for at, wedge_hex in zip(autotexts, wedge_colors):
            r, g, b = int(wedge_hex[1:3], 16), int(wedge_hex[3:5], 16), int(wedge_hex[5:7], 16)
            luminance = 0.299 * r + 0.587 * g + 0.114 * b
            at.set_color("#1a1a1a" if luminance > 150 else "#ffffff")
            at.set_fontproperties(_mpl_font_prop("0"))
        ax.axis("equal")
    elif chart_type == "line":
        ax.plot(labels, values, color=_hex(primary), linewidth=3, marker="o", markerfacecolor=_hex(accent),
                 markeredgecolor=_hex(accent), markersize=7)
        ax.fill_between(range(len(labels)), values, color=_hex(primary), alpha=0.08)
    else:
        ax.bar(labels, values, color=_hex(primary), edgecolor=_hex(accent), linewidth=1.2)

    if chart_type != "pie":
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            ax.spines[spine].set_color(_hex(body_color))
        ax.tick_params(colors=_hex(body_color))
        for tick_label in ax.get_xticklabels() + ax.get_yticklabels():
            tick_label.set_fontproperties(_mpl_font_prop(tick_label.get_text()))
            tick_label.set_color(_hex(body_color))
        if spec["x_label"]:
            ax.set_xlabel(spec["x_label"], fontproperties=_mpl_font_prop(spec["x_label"]), color=_hex(body_color))
        if spec["y_label"]:
            ax.set_ylabel(spec["y_label"], fontproperties=_mpl_font_prop(spec["y_label"]), color=_hex(body_color))
        ax.grid(axis="y", color=_hex(body_color), alpha=0.15)
        if len(labels) > 6:
            plt.setp(ax.get_xticklabels(), rotation=30, ha="right")

    if title:
        ax.set_title(title, fontproperties=_mpl_font_prop(title, bold=True), color=_hex(title_color), fontsize=16, pad=14)

    fig.tight_layout()
    if sources:
        # Real data deserves a visible citation when it came from a web search, not
        # the authoring LLM's own unverified knowledge — matches how a print chart
        # would credit its data source. Domain only (not the full URL/title), kept
        # short since this is a footer, not a bibliography.
        from urllib.parse import urlparse

        domains: list[str] = []
        for src in sources[:3]:
            url = (src.get("url") or "").strip()
            if not url:
                continue
            domain = urlparse(url).netloc.removeprefix("www.")
            if domain and domain not in domains:
                domains.append(domain)
        if domains:
            fig.text(
                0.99, 0.01, f"Source: {', '.join(domains)}",
                ha="right", va="bottom", fontsize=7, color=_hex(body_color), alpha=0.7,
                fontproperties=_mpl_font_prop("Source"),
            )
    fig.savefig(output_path, facecolor=fig.get_facecolor())
    plt.close(fig)
