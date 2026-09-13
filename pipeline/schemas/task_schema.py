# -*- coding: utf-8 -*-
"""Task and workflow domain types for LOMA pipelines."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Mode = Optional[Literal["generation", "mutation"]]
QuerySource = Literal["workspace", "highlight", "revision"]


@dataclass
class InputMetadata:
    query: str
    profile_id: str
    preferred_output_format: str
    file_count: int
    link_count: int
    message_count: int
    files: list[dict]
    links: list[str]
    has_image: bool
    has_docs: bool
    has_excerpt: bool
    has_preview_selection: bool
    has_valid_preview_selection: bool
    query_source: QuerySource
    total_source_chars: int = 0
    needs_context_retrieval: bool = False


@dataclass
class RoutingDecision:
    required_services: list[str]
    output_type: str
    mode: Mode
    confidence: str
    reason: str
    llm_type: str = "text"


@dataclass
class PlanOption:
    id: str
    label: str
    mission: str = "compose"


@dataclass
class PlanStep:
    role: str
    description: str
    action: str = "execute"
    step_id: str = ""
    artifact_type: str = ""
    acceptance_criteria: list[str] = field(default_factory=list)
    options: list[PlanOption] = field(default_factory=list)
    instruction_md: str = ""
    depends_on: list[str] = field(default_factory=list)
    phase: str = ""
    deliverable_contract: str = ""

    def normalized_role(self) -> str:
        from pipeline.deliverables.plan_helpers import normalize_worker_role

        role = (self.role or "General").strip()
        return normalize_worker_role(role)


@dataclass
class TaskResult:
    task: str
    output: str
    status: str


@dataclass
class ContextBundle:
    query: str
    profile_id: str
    profile: dict
    unified_text: str = ""
    images: list = field(default_factory=list)
    web_links: list = field(default_factory=list)
    recent_chat: str = ""
    original_filename: str = "document"
    template_filename: Optional[str] = None
    context_was_retrieved: bool = False
    context_selection_mode: str = ""
    parsed_sources: list = field(default_factory=list)
    chart_artifacts: list = field(default_factory=list)
    # Shared ingest (oversize context)
    source_digests: list = field(default_factory=list)
    context_strategy: str = "fit"
    digest_index_md: str = ""
    total_source_chars: int = 0
    context_char_budget: int = 0


@dataclass
class LomaRequest:
    user_input: str
    profile_id: str
    messages: list
    context_files: list
    web_links: list
    settings: dict


@dataclass
class AgenticPlan:
    objective: str
    plan_md: str
    steps: list[PlanStep]
    success_criteria: list[str] = field(default_factory=list)
    delivery_pipeline: list[str] = field(default_factory=list)
    deliverable_type: str = ""
    services_required: list[str] = field(default_factory=list)
