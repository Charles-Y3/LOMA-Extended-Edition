# -*- coding: utf-8 -*-
"""Attach chart figure markers to direct-pipeline chat and document outputs."""
from __future__ import annotations

from typing import Any


def apply_chart_appendix(
    *,
    content: str,
    charts: list,
    output_type: str,
    state: Any,
    sink: Any | None = None,
) -> str:
    """
    Chat: append [[FIGURE:n]] markers + chart_figures_ready for UI renderer.
    Document/presentation/spreadsheet: embed figures via finalize_report_markdown.
    """
    if not charts or not (content or "").strip():
        return content

    ot = (output_type or "chat").strip().lower()
    if ot == "chat":
        from services.graph_generation.report_layout import chart_marker_block, strip_figure_markers

        body, _ = strip_figure_markers(content)
        appendix = chart_marker_block(charts)
        if not appendix:
            return content
        merged = f"{body.rstrip()}\n\n{appendix}"
        if state.messages:
            state.messages[-1]["content"] = merged
            state.messages[-1].pop("images", None)
            state.messages[-1]["chart_figures_ready"] = True
        if sink is not None:
            sink.set_assistant_content(merged)
            sink.refresh_chat()
        return merged

    from services.graph_generation.report_layout import finalize_report_markdown

    finalized = finalize_report_markdown(content, charts)
    if state.messages:
        state.messages[-1]["content"] = finalized
    return finalized
