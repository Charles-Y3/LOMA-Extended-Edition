# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DatasetProfile:
    name: str
    path: str
    row_count: int
    column_count: int
    columns: list[dict]
    summary_md: str
    sampled: bool = False
    overview_md: str = ""
    computed_stats: dict = field(default_factory=dict)


@dataclass
class ChartArtifact:
    path: str
    title: str
    chart_type: str
    caption: str = ""
    slot: int = 0
    data_summary: str = ""


@dataclass
class GraphAnalysisResult:
    profiles: list[DatasetProfile] = field(default_factory=list)
    charts: list[ChartArtifact] = field(default_factory=list)
    context_md: str = ""
    error: str = ""
