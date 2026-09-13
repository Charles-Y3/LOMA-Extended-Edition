# -*- coding: utf-8 -*-
"""Data Studio services — multi-spreadsheet cleaning, correlation, overlay, insights.

Pure logic only (no UI). The data_studio extension calls into these; they reuse the
existing tabular loaders (services.graph_generation.dataset), profiler
(services.graph_generation.profile) and the pandas sandbox
(services.spreadsheet_query.sandbox) rather than duplicating them.
"""
from __future__ import annotations

__all__ = [
    "clean",
    "correlate",
    "overlay",
    "insights",
]
