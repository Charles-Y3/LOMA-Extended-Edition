# -*- coding: utf-8 -*-
"""Ordered report layout: chart slots for LLM context and markdown/docx embedding."""
from __future__ import annotations

import os
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.graph_generation.models import ChartArtifact, DatasetProfile

_CHART_TYPE_HINTS = {
    "histogram": "Describe the distribution shape, central tendency, outliers, and what it implies.",
    "bar": "Summarize the leading categories, gaps between groups, and practical takeaways.",
    "bar_grouped": "Explain how the metric varies across categories and which groups stand out.",
    "line": "Comment on trends, turning points, and whether the relationship looks stable or shifting.",
    "pie": "Explain the largest shares, imbalances, and what they mean operationally.",
    "scatter": "Describe correlation, clusters, outliers, and whether the trend line fits.",
}


def _norm_path(path: str) -> str:
    return os.path.abspath(path or "").replace("\\", "/")


def _image_md(slot: int, title: str, path: str) -> str:
    p = _norm_path(path)
    return f"![Figure {slot}: {title}]({p})"


def analysis_hint_for_chart(chart: ChartArtifact) -> str:
    base = chart.caption or _CHART_TYPE_HINTS.get(
        chart.chart_type, "Interpret the visual patterns and tie them to the dataset."
    )
    if chart.data_summary:
        # Real computed values from the chart's own data — without this, the writer has
        # no numbers to draw from and invents figures that don't match what's plotted.
        return f"{base} Actual values: {chart.data_summary}."
    return base


def assign_chart_slots(charts: list[ChartArtifact]) -> list[ChartArtifact]:
    for i, ch in enumerate(charts, start=1):
        ch.slot = i
        if not ch.caption:
            ch.caption = analysis_hint_for_chart(ch)
    return charts


def build_ordered_report_context(
    profiles: list[DatasetProfile],
    charts: list[ChartArtifact],
    *,
    query: str = "",
) -> str:
    """Markdown block: report outline + exact figure slots the writer must follow in order."""
    charts = assign_chart_slots(list(charts))
    lines = [
        "## Report layout (mandatory order)",
        "",
        "Write the final Markdown document using **exactly** this structure and figure order.",
        "For each figure: (1) the heading, (2) the image line on its own line (copy verbatim), "
        "(3) 2–4 paragraphs of analysis that reference that figure.",
        "",
        "1. **Title** — `# Report title` matching the user request",
        "2. **Executive summary** — `## Executive summary` (no images)",
    ]
    slot = 3
    for ch in charts:
        lines.append(
            f"{slot}. **Figure {ch.slot}: {ch.title}** — `## Figure {ch.slot}: {ch.title}` "
            f"then this image line, then analysis:"
        )
        lines.append(f"   `{_image_md(ch.slot, ch.title, ch.path)}`")
        lines.append(f"   - Focus: {analysis_hint_for_chart(ch)}")
        slot += 1
    lines.append(f"{slot}. **Conclusions and recommendations** — `## Conclusions` (no new images)")
    lines.append("")
    if profiles:
        lines.append("### Dataset profiles (for analysis; do not paste raw tables)")
        for prof in profiles[:2]:
            lines.append(prof.summary_md[:8000])
    if query.strip():
        lines.append(f"\n### User request\n{query.strip()}")
    return "\n".join(lines)


_FIGURE_HEADING_RE = re.compile(r"^##\s+Figure\s+(\d+)\s*:", re.IGNORECASE | re.MULTILINE)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _default_figure_section(slot: int, chart: ChartArtifact) -> str:
    hint = analysis_hint_for_chart(chart)
    return (
        f"## Figure {slot}: {chart.title}\n\n"
        f"{_image_md(slot, chart.title, chart.path)}\n\n"
        f"{hint}"
    )


def _extract_section(markdown: str, heading_prefix: str) -> str:
    """Return body under first ## heading that startswith prefix (case-insensitive)."""
    pattern = re.compile(
        rf"^##\s+{re.escape(heading_prefix)}[^\n]*\n(.*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(markdown or "")
    return m.group(1).strip() if m else ""


def _extract_figure_body(markdown: str, slot: int) -> str:
    pattern = re.compile(
        rf"^##\s+Figure\s+{slot}\s*:[^\n]*\n(.*?)(?=^##\s+|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(markdown or "")
    return m.group(1).strip() if m else ""


def _ensure_image_in_section(body: str, slot: int, chart: ChartArtifact) -> str:
    path = _norm_path(chart.path)
    if not os.path.isfile(chart.path):
        return body
    basename = os.path.basename(chart.path)
    if path in body or basename in body:
        return body
    img = _image_md(slot, chart.title, chart.path)
    return f"{img}\n\n{body}".strip() if body else img


def finalize_report_markdown(markdown: str, charts: list[ChartArtifact] | None) -> str:
    """
    Reassemble document so figures appear in slot order with embedded image paths.
    Preserves executive summary / conclusions when present; fills missing figure sections.
    """
    charts = assign_chart_slots([c for c in (charts or []) if c.path and os.path.isfile(c.path)])
    if not charts:
        return markdown or ""

    md = (markdown or "").strip()
    title_block = ""
    if md.startswith("#"):
        first = md.split("\n", 1)
        title_block = first[0].strip()
        md = first[1].strip() if len(first) > 1 else ""

    exec_summary = _extract_section(md, "Executive summary") or _extract_section(md, "Summary")
    conclusions = _extract_section(md, "Conclusions") or _extract_section(md, "Conclusion")

    parts: list[str] = []
    if title_block:
        parts.append(title_block)
    if exec_summary:
        parts.append(f"## Executive summary\n\n{exec_summary}")
    else:
        intro = _FIGURE_HEADING_RE.split(md)[0].strip()
        if intro and not intro.lower().startswith("## figure"):
            parts.append(intro)

    for ch in charts:
        body = _extract_figure_body(md, ch.slot)
        body = _ensure_image_in_section(body, ch.slot, ch)
        if not body or len(body) < 80:
            body = _ensure_image_in_section(analysis_hint_for_chart(ch), ch.slot, ch)
        parts.append(f"## Figure {ch.slot}: {ch.title}\n\n{body}")

    if conclusions:
        parts.append(f"## Conclusions\n\n{conclusions}")

    return "\n\n".join(parts).strip()


FIGURE_MARKER_RE = re.compile(r"\[\[FIGURE:(\d+)(?::([^\]]+))?\]\]", re.IGNORECASE)


def chart_marker_block(charts: list) -> str:
    """Append-only figure markers for chat UI (images rendered once at end)."""
    if not charts:
        return ""
    lines = ["", "---", "**Appendix**"]
    for ch in charts:
        slot = getattr(ch, "slot", 0) or 0
        title = getattr(ch, "title", "chart")
        if slot:
            lines.append(f"[[FIGURE:{slot}:{title}]]")
    return "\n".join(lines).strip() if len(lines) > 2 else ""


def chart_appendix_markdown(charts: list) -> str:
    """Legacy name — chat uses markers, not inline markdown images."""
    return chart_marker_block(charts)


def strip_figure_markers(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Remove [[FIGURE:n:title]] lines from display text; return slots for UI."""
    slots: list[tuple[int, str]] = []
    cleaned_lines: list[str] = []
    for line in (text or "").splitlines():
        m = FIGURE_MARKER_RE.search(line)
        if m and line.strip().startswith("[[FIGURE:"):
            slots.append((int(m.group(1)), (m.group(2) or "").strip()))
            continue
        cleaned_lines.append(line)
    body = "\n".join(cleaned_lines).strip()
    body = re.sub(r"\n---\s*\n\*\*Appendix\*\*\s*$", "", body, flags=re.IGNORECASE).strip()
    return body, slots


def build_chat_graph_context(
    profiles: list,
    charts: list,
    *,
    query: str = "",
) -> str:
    """LLM context for chat tabular analysis — substantive analysis, not formal report layout."""
    charts = assign_chart_slots(list(charts))
    lines = [
        "## Data analysis task",
        "",
        "The user uploaded tabular data. A statistical profile and charts are provided below.",
        "Write a **substantive analysis** that answers their question using the profile numbers.",
        "- Do **not** ask the user to provide data or paste tables — everything needed is below.",
        "- Do **not** use markdown image syntax (`![...]()`).",
        "- Refer to charts in prose as **Figure 1**, **Figure 2**, etc. (matching the list below).",
        "- Charts are shown in the UI after your answer; do not repeat placeholder greetings.",
        "",
    ]
    if charts:
        lines.append("### Charts generated (reference by figure number in your analysis)")
        for ch in charts:
            lines.append(
                f"- **Figure {ch.slot}: {ch.title}** ({ch.chart_type}) — {analysis_hint_for_chart(ch)}"
            )
        lines.append("")
    if profiles:
        lines.append("### Dataset profile")
        for prof in profiles[:2]:
            lines.append(prof.summary_md[:12000])
    if query.strip():
        lines.append(f"\n### User request\n{query.strip()}")
    return "\n".join(lines)
