# -*- coding: utf-8 -*-
"""Contracts for presentation generation capabilities."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliverableGenerationContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, DeliverableGenerationContract] = {
    "deliverable_outline": DeliverableGenerationContract(
        id="deliverable_outline",
        description="Outline before full deliverable draft.",
        output_rules=(
            "Use a structured outline with short numbered sections.",
            "Do not write the full deliverable body yet.",
        ),
        max_chars=7000,
    ),
    "deliverable_synthesis": DeliverableGenerationContract(
        id="deliverable_synthesis",
        description="Context synthesis notes for generation.",
        output_rules=(
            "Extract and organize facts from workspace context only.",
            "Use concise bullets grouped by theme.",
            "Do not produce the final deliverable body yet.",
        ),
        max_chars=12000,
    ),
    "deliverable_markdown": DeliverableGenerationContract(
        id="deliverable_markdown",
        description="Generic deliverable markdown body.",
        output_rules=(
            "Output ONLY the markdown body.",
            "Do not wrap output in fences or add meta commentary.",
        ),
        max_chars=24000,
    ),
    "presentation_markdown": DeliverableGenerationContract(
        id="presentation_markdown",
        description="Slide markdown for .pptx compile.",
        output_rules=(
            "Output ONLY slide markdown with --- between slides.",
            "Slide 1 MUST be title slide: # deck title, optional single - subtitle line (no bullet list).",
            "Slide 2 MUST be Outline or Agenda: ## Outline with - bullets for main sections.",
            "Slides 3+: ## real topic headings — never 'Slide N' or field labels.",
            "Max 5 bullets per content slide; max ~12 words per bullet.",
            "Do not include save/export instructions or meta commentary.",
        ),
        max_chars=24000,
    ),
}


def get_deliverable_generation_contract(contract_id: str) -> DeliverableGenerationContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["deliverable_markdown"])


def get_deliverable_generation_contract_for_role(
    role_id: str,
    capability_id: str = "presentation_generation",
) -> DeliverableGenerationContract:
    cap = (capability_id or "").strip().lower()
    if role_id == "gen_author":
        if cap == "presentation_generation":
            return _CONTRACTS["presentation_markdown"]
    role_to_contract = {
        "gen_outliner": "deliverable_outline",
        "gen_synthesizer": "deliverable_synthesis",
        "gen_author": "deliverable_markdown",
    }
    return get_deliverable_generation_contract(role_to_contract.get(role_id, "deliverable_markdown"))
