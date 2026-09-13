# -*- coding: utf-8 -*-
"""Deliverable specifications per output type and mode."""
from __future__ import annotations

import re
from dataclasses import dataclass

from pipeline.deliverables.contracts import DeliverableContract, get_deliverable_contract

PhaseName = str


@dataclass(frozen=True)
class DeliverableSpec:
    output_type: str
    mode: str | None
    phases: tuple[PhaseName, ...]
    final_contract_id: str
    compile_services: tuple[str, ...]
    preview_services: tuple[str, ...]
    mutation_services: tuple[str, ...] = ()
    stub: bool = False

    def contract_for_phase(self, phase: str) -> DeliverableContract:
        phase_key = (phase or "").strip().lower()
        if phase_key in ("research", "extract", "synthesis"):
            return get_deliverable_contract("research_notes")
        if phase_key == "outline":
            if self.output_type == "presentation":
                return get_deliverable_contract("presentation_deck_spec")
            return get_deliverable_contract("document_outline")
        if phase_key == "draft":
            if self.output_type == "presentation":
                return get_deliverable_contract("presentation_slide_copy")
            return get_deliverable_contract(self.final_contract_id)
        if phase_key == "assemble":
            return get_deliverable_contract(self.final_contract_id)
        if phase_key == "prompt" and self.output_type == "image":
            return get_deliverable_contract("image_prompt")
        return get_deliverable_contract(self.final_contract_id)

    def delivery_service_ids(self, required_services: list[str]) -> list[str]:
        """Services shown in plan and invoked at delivery (ordered, deduped)."""
        ordered: list[str] = []
        seen: set[str] = set()
        for group in (self.compile_services, self.mutation_services, self.preview_services):
            for sid in group:
                if sid not in seen:
                    seen.add(sid)
                    ordered.append(sid)
        for sid in required_services or []:
            if sid in ("artifact_store", "image_generation", "sandbox_runner", "renderer", "file_io"):
                if sid not in seen:
                    seen.add(sid)
                    ordered.append(sid)
        return ordered


def _generation_spec(output_type: str) -> DeliverableSpec:
    specs: dict[str, DeliverableSpec] = {
        "document": DeliverableSpec(
            output_type="document",
            mode="generation",
            phases=("research", "outline", "draft", "assemble"),
            final_contract_id="document_markdown",
            compile_services=("artifact_store",),
            preview_services=("renderer",),
        ),
        "presentation": DeliverableSpec(
            output_type="presentation",
            mode="generation",
            phases=("research", "outline", "draft", "assemble"),
            final_contract_id="presentation_markdown",
            compile_services=("artifact_store",),
            preview_services=("renderer",),
        ),
        "image": DeliverableSpec(
            output_type="image",
            mode="generation",
            phases=("research", "prompt"),
            final_contract_id="image_prompt",
            compile_services=("image_generation",),
            preview_services=("renderer",),
        ),
        "chat": DeliverableSpec(
            output_type="chat",
            mode="generation",
            phases=("draft",),
            final_contract_id="generic_stub",
            compile_services=(),
            preview_services=(),
        ),
        "video": DeliverableSpec(
            output_type="video",
            mode="generation",
            phases=("draft",),
            final_contract_id="generic_stub",
            compile_services=(),
            preview_services=(),
            stub=True,
        ),
    }
    return specs.get(output_type, DeliverableSpec(
        output_type=output_type,
        mode="generation",
        phases=("draft",),
        final_contract_id="generic_stub",
        compile_services=(),
        preview_services=(),
        stub=True,
    ))


def _mutation_spec(output_type: str) -> DeliverableSpec:
    if output_type == "presentation":
        return DeliverableSpec(
            output_type="presentation",
            mode="mutation",
            phases=("extract", "draft", "assemble"),
            final_contract_id="presentation_markdown",
            compile_services=(),
            preview_services=("renderer",),
            mutation_services=("artifact_store",),
        )
    if output_type == "document":
        return DeliverableSpec(
            output_type="document",
            mode="mutation",
            phases=("extract", "draft", "assemble"),
            final_contract_id="document_markdown",
            compile_services=(),
            preview_services=("renderer",),
            mutation_services=("artifact_store",),
        )
    if output_type == "image":
        return DeliverableSpec(
            output_type="image",
            mode="mutation",
            phases=("draft",),
            final_contract_id="generic_stub",
            compile_services=(),
            preview_services=(),
            mutation_services=("image_generation",),
        )
    return _generation_spec(output_type)


def get_deliverable_spec(output_type: str, mode: str | None = None) -> DeliverableSpec:
    out = (output_type or "chat").strip().lower()
    if (mode or "").strip().lower() == "mutation" and out in ("document", "presentation", "image"):
        return _mutation_spec(out)
    return _generation_spec(out)


def infer_slide_count(query: str) -> int | None:
    match = re.search(r"\b(\d+)\s*slides?\b", query or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def service_purpose(service_id: str) -> str:
    purposes = {
        "artifact_store": "Compile markdown source into office files (.docx, .pptx)",
        "renderer": "Render preview HTML in the workspace",
        "image_generation": "Generate image file from prompt",
        "sandbox_runner": "Execute generated code in sandbox",
        "file_io": "Parse uploaded source documents",
        "llm_bridge": "LLM text generation for worker steps",
    }
    return purposes.get(service_id, "LOMA atomic service")
