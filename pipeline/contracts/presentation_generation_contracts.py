# -*- coding: utf-8 -*-
"""Contracts for layered presentation generation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PresentationGenerationContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, PresentationGenerationContract] = {
    "presentation_metadata_json": PresentationGenerationContract(
        id="presentation_metadata_json",
        description="Presentation-level metadata JSON.",
        output_rules=(
            "Return ONLY valid JSON object (no markdown fences).",
            "Required keys: title, subtitle, topic, audience, purpose, tone, "
            "duration_minutes, number_of_slides, include_images.",
            "tone must be one of: formal, casual, academic, persuasive, technical.",
        ),
        max_chars=4000,
    ),
    "presentation_narrative_json": PresentationGenerationContract(
        id="presentation_narrative_json",
        description="Story arc JSON.",
        output_rules=(
            "Return ONLY valid JSON with key story_arc containing: "
            "opening_hook, problem_statement, key_idea, main_argument, resolution_or_conclusion.",
        ),
        max_chars=6000,
    ),
    "presentation_slide_plan_json": PresentationGenerationContract(
        id="presentation_slide_plan_json",
        description="Per-slide plan JSON.",
        output_rules=(
            "Return ONLY valid JSON with key slides (array).",
            "Each slide: slide_number, slide_type, title, key_message, bullet_points, "
            "speaker_notes, visual (type, description), layout_hint.",
            "bullet_points MUST be a JSON array of 3-5 complete sentences (never empty).",
            "max 5 bullets per slide; one idea per slide.",
        ),
        max_chars=12000,
    ),
    "presentation_markdown": PresentationGenerationContract(
        id="presentation_markdown",
        description="Final slide markdown that compiles to PPTX slides.",
        output_rules=(
            "Return markdown only (no JSON, no code fences).",
            "Each slide MUST start with --- Slide N --- on its own line (N = 1, 2, 3, …).",
            "Slide 1: # deck title only; optional single subtitle line (no bullet list).",
            'Slide 2: ## Agenda (or Outline) then 3–6 "- " bullets naming main sections.',
            "Slides 3 through second-to-last: ## topic title then 3–5 concise bullets.",
            "Final slide: ## Conclusion (or Key Takeaways) then 3–5 bullets.",
            "Never use Slide N, Title:, or Content: as visible text.",
            "Do not include implementation notes or meta commentary.",
        ),
        max_chars=16000,
    ),
}


def get_presentation_generation_contract(contract_id: str) -> PresentationGenerationContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["presentation_metadata_json"])


def get_presentation_generation_contract_for_role(role_id: str) -> PresentationGenerationContract:
    mapping = {
        "pres_metadata": "presentation_metadata_json",
        "pres_narrative": "presentation_narrative_json",
        "pres_slide_planner": "presentation_slide_plan_json",
        "pres_author": "presentation_markdown",
    }
    return get_presentation_generation_contract(mapping.get(role_id, "presentation_metadata_json"))
