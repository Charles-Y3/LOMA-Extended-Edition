# -*- coding: utf-8 -*-
"""Tabular analysis and chart generation — atomic service for any capability."""
from __future__ import annotations

from services.graph_generation.models import ChartArtifact, DatasetProfile, GraphAnalysisResult
from services.graph_generation.report_layout import finalize_report_markdown
from services.graph_generation.run import enrich_context_with_graph_analysis, run_graph_analysis

__all__ = [
    "ChartArtifact",
    "DatasetProfile",
    "GraphAnalysisResult",
    "enrich_context_with_graph_analysis",
    "finalize_report_markdown",
    "run_graph_analysis",
]
