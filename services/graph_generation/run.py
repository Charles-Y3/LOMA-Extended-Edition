# -*- coding: utf-8 -*-
"""Orchestrate profiling + chart export for capabilities and context bundle."""
from __future__ import annotations

import os

from services.graph_generation.dataset import resolve_tabular_paths
from services.graph_generation.models import GraphAnalysisResult
from services.graph_generation.plot import render_charts_for_dataset
from services.graph_generation.profile import profile_dataset
from services.graph_generation.report_layout import (
    assign_chart_slots,
    build_ordered_report_context,
)


def run_graph_analysis(
    *,
    query: str = "",
    parsed_sources: list | None = None,
    context_files: list | None = None,
    output_dir: str | None = None,
    max_charts_per_dataset: int = 4,
    layout_mode: str = "report",
    log_fn=None,
) -> GraphAnalysisResult:
    """
    Profile tabular uploads and emit PNG charts + markdown context for the LLM.
    Designed for large files: stats on a capped sample, never full sheet to the model.
    """
    log = log_fn or (lambda _m: None)
    paths = resolve_tabular_paths(parsed_sources, context_files)
    if not paths:
        return GraphAnalysisResult(error="No tabular dataset found (.csv, .xlsx, .xls).")

    out_dir = output_dir or os.path.join("data", "generated", "charts")
    os.makedirs(out_dir, exist_ok=True)

    profiles = []
    charts: list = []

    for name, path in paths[:3]:
        try:
            log(f"Profiling dataset: {name}")
            prof = profile_dataset(path, name=name)
            profiles.append(prof)
            log(f"Rendering charts for {name}…")
            chart_list: list = []
            if query.strip():
                from services.graph_generation.plan_charts import plan_charts_with_llm
                from services.graph_generation.plot import render_charts_from_specs

                specs = plan_charts_with_llm(prof, query=query)
                if specs:
                    chart_list = render_charts_from_specs(
                        path,
                        specs,
                        name=name,
                        output_dir=out_dir,
                    )
                    log(
                        f"LLM chart plan: {len(specs)} proposed → {len(chart_list)} rendered"
                    )
            if not chart_list:
                chart_list = render_charts_for_dataset(
                    path,
                    name=name,
                    query=query,
                    output_dir=out_dir,
                    max_charts=max_charts_per_dataset,
                )
            charts.extend(chart_list)
        except Exception as exc:
            log(f"Graph analysis failed for {name}: {exc}")

    if not profiles and not charts:
        return GraphAnalysisResult(error="Could not profile or chart tabular inputs.")

    assign_chart_slots(charts)
    layout_mode = (layout_mode or "report").strip().lower()
    if layout_mode == "chat":
        from services.graph_generation.report_layout import build_chat_graph_context

        context_md = build_chat_graph_context(profiles, charts, query=query)
    else:
        context_md = build_ordered_report_context(profiles, charts, query=query)
    if not charts and profiles:
        context_md = "\n\n".join(p.summary_md for p in profiles)

    return GraphAnalysisResult(
        profiles=profiles,
        charts=charts,
        context_md=context_md,
    )


def enrich_context_with_graph_analysis(
    bundle,
    query: str,
    *,
    layout_mode: str = "report",
    log_fn=None,
) -> GraphAnalysisResult | None:
    """Append graph service output; replace raw spreadsheet dumps with profile + chart layout."""
    parsed = getattr(bundle, "parsed_sources", None) or []
    result = run_graph_analysis(
        query=query,
        parsed_sources=parsed,
        log_fn=log_fn,
        layout_mode=layout_mode,
    )
    if not result.context_md:
        return result if result.error else None

    prefix_parts: list[str] = []
    for ps in parsed:
        if getattr(ps, "kind", "") == "spreadsheet":
            continue
        text = ps.context_text() if hasattr(ps, "context_text") else ""
        if text:
            prefix_parts.append(text)

    if result.charts or result.profiles:
        body = result.context_md
        if prefix_parts:
            sep = "\n\n"
            body = f"{sep.join(prefix_parts)}{sep}{body}".strip()
        bundle.unified_text = body
    else:
        existing = (getattr(bundle, "unified_text", None) or "").strip()
        bundle.unified_text = f"{existing}\n\n{result.context_md}".strip() if existing else result.context_md

    bundle.chart_artifacts = list(getattr(bundle, "chart_artifacts", None) or []) + result.charts
    return result
