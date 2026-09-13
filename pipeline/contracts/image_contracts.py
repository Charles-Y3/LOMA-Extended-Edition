"""Contracts for image_generation capability outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ImageContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, ImageContract] = {
    "image_context": ImageContract(
        id="image_context",
        description="Visual brief extracted from workspace context.",
        output_rules=(
            "List only visual requirements implied by the user request and context.",
            "Use short bullets: subject, setting, style, colors, mood, constraints.",
            "Do not write the final diffusion prompt yet.",
            "Do not invent details not supported by the context.",
        ),
        max_chars=4000,
    ),
    "image_brief": ImageContract(
        id="image_brief",
        description="Composition brief before writing the diffusion prompt.",
        output_rules=(
            "Describe composition, focal subject, camera/framing, lighting, and style.",
            "Note intended use (standalone file, document figure, slide visual, or brand mark).",
            "Keep bullets concise; do not output the final prompt text.",
        ),
        max_chars=3000,
    ),
    "image_prompt_brand": ImageContract(
        id="image_prompt_brand",
        description="Diffusion prompt for logos, icons, and brand marks.",
        output_rules=(
            "Return ONE single-line image generation prompt only.",
            "Favor clean shapes, readable at small sizes, simple background.",
            "No quotes, labels, markdown, or explanation.",
        ),
        max_chars=600,
    ),
    "image_prompt_slide": ImageContract(
        id="image_prompt_slide",
        description="Diffusion prompt for presentation / slide visuals.",
        output_rules=(
            "Return ONE single-line image generation prompt only.",
            "Favor widescreen composition (landscape), clear focal subject, readable at a distance.",
            "Avoid dense text in the image unless explicitly requested.",
            "No quotes, labels, markdown, or explanation.",
        ),
        max_chars=800,
    ),
    "image_prompt_document": ImageContract(
        id="image_prompt_document",
        description="Diffusion prompt for document figures and report illustrations.",
        output_rules=(
            "Return ONE single-line image generation prompt only.",
            "Favor clear explanatory visuals suitable inline in a report or docx.",
            "Prefer clean diagrams, figures, or editorial illustrations as appropriate.",
            "No quotes, labels, markdown, or explanation.",
        ),
        max_chars=800,
    ),
    "image_prompt_standalone": ImageContract(
        id="image_prompt_standalone",
        description="Diffusion prompt for standalone generated images.",
        output_rules=(
            "Return ONE single-line image generation prompt only.",
            "Lead with subject + action + object when the user describes motion or interaction "
            "(e.g. lion chasing rabbits) before style or lighting clauses.",
            "Every animal or person the user named must appear visibly in the scene.",
            "Prefer one coherent foreground action; avoid duplicate quality boilerplate.",
            "Include setting and lighting briefly after the main subjects.",
            "No quotes, labels, markdown, or explanation.",
        ),
        max_chars=900,
    ),
    "image_action_scene": ImageContract(
        id="image_action_scene",
        description="One-line action beat before the final diffusion prompt.",
        output_rules=(
            "Return ONE short line: who is doing what to whom, in plain language.",
            "Name every subject from the user request.",
            "No style, lighting, or markdown.",
        ),
        max_chars=200,
    ),
    "image_mut_global": ImageContract(
        id="image_mut_global",
        description="Global img2img edit instruction.",
        output_rules=(
            "Produce a single diffusion prompt describing the desired whole-image change.",
            "No markdown or explanations.",
        ),
        max_chars=600,
    ),
    "image_mut_preserve": ImageContract(
        id="image_mut_preserve",
        description="Minimal local edit preserving unrelated pixels.",
        output_rules=(
            "Describe ONLY the region or attribute to change; state that all else must stay identical.",
            "Prefer inpainting-style localized edits over full-scene regeneration.",
            "No markdown or explanations.",
        ),
        max_chars=600,
    ),
}


def get_image_contract(contract_id: str) -> ImageContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["image_prompt_standalone"])


def get_image_contract_for_role(role_id: str) -> ImageContract:
    role_to_contract = {
        "img_context_synthesizer": "image_context",
        "img_brief_planner": "image_brief",
        "img_action_scene": "image_action_scene",
        "img_brand_composer": "image_prompt_brand",
        "img_slide_composer": "image_prompt_slide",
        "img_document_composer": "image_prompt_document",
        "img_standalone_composer": "image_prompt_standalone",
        "img_mut_global_editor": "image_mut_global",
        "img_mut_preserve_editor": "image_mut_preserve",
    }
    return get_image_contract(role_to_contract.get(role_id, "image_prompt_standalone"))
