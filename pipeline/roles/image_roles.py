"""Role definitions for image_generation capability."""
from __future__ import annotations

import re

from pipeline.roles.chat_roles import ChatRole

_ACTION_VERBS = re.compile(
    r"\b(chas(e|ing)|run(ning)?|hunt(ing)?|attack(ing)?|pursu(e|ing)|fight(ing)?)\b",
    re.IGNORECASE,
)

_IMAGE_ROLES: dict[str, ChatRole] = {
    "img_context_synthesizer": ChatRole(
        id="img_context_synthesizer",
        description="Extract visual requirements from attached workspace context.",
        system_prompt=(
            "Role: Image Context Synthesizer.\n"
            "Identify visual facts and constraints from provided sources for the requested image.\n"
            "Stay grounded in context; do not invent unsupported details."
        ),
        default_contract_id="image_context",
    ),
    "img_brief_planner": ChatRole(
        id="img_brief_planner",
        description="Plan composition, framing, and style before prompt writing.",
        system_prompt=(
            "Role: Image Brief Planner.\n"
            "Plan composition, subject focus, lighting, palette, and intended use "
            "(standalone, document figure, slide visual, or brand mark).\n"
            "Do not write the final diffusion prompt."
        ),
        default_contract_id="image_brief",
    ),
    "img_brand_composer": ChatRole(
        id="img_brand_composer",
        description="Write a diffusion prompt for logos, icons, and brand marks.",
        system_prompt=(
            "Role: Brand Image Composer.\n"
            "Produce a single Stable Diffusion prompt for a logo, icon, or brand mark.\n"
            "Emphasize clarity, simplicity, and scalability."
        ),
        default_contract_id="image_prompt_brand",
    ),
    "img_slide_composer": ChatRole(
        id="img_slide_composer",
        description="Write a diffusion prompt for presentation slide visuals.",
        system_prompt=(
            "Role: Slide Visual Composer.\n"
            "Produce a single Stable Diffusion prompt for a widescreen slide or deck visual.\n"
            "Emphasize legibility, focal hierarchy, and landscape composition."
        ),
        default_contract_id="image_prompt_slide",
    ),
    "img_document_composer": ChatRole(
        id="img_document_composer",
        description="Write a diffusion prompt for document figures and report art.",
        system_prompt=(
            "Role: Document Figure Composer.\n"
            "Produce a single Stable Diffusion prompt for a figure or illustration in a report.\n"
            "Emphasize explanatory clarity suitable for inline document use."
        ),
        default_contract_id="image_prompt_document",
    ),
    "img_action_scene": ChatRole(
        id="img_action_scene",
        description="Summarize who does what to whom before prompt writing.",
        system_prompt=(
            "Role: Action Scene Planner.\n"
            "Write one short line describing the main action between subjects "
            "(predator/prey, chase, fight, run, etc.).\n"
            "Name every subject the user mentioned. Do not add style or lighting."
        ),
        default_contract_id="image_action_scene",
    ),
    "img_standalone_composer": ChatRole(
        id="img_standalone_composer",
        description="Write a diffusion prompt for standalone artwork or photos.",
        system_prompt=(
            "Role: Standalone Image Composer.\n"
            "Produce a single vivid Stable Diffusion prompt from the user request and prior steps.\n"
            "Foreground action scene: all requested animals or people visible in frame.\n"
            "For chases or hunts: lead with subject + verb + target, then setting and lighting."
        ),
        default_contract_id="image_prompt_standalone",
    ),
}

ImageUseCase = str  # standalone | document | presentation | branding


def infer_image_use_case(user_query: str, *, output_type: str | None = None) -> ImageUseCase:
    """Classify how the image will be used in LOMA."""
    q = (user_query or "").lower()
    if any(w in q for w in ("logo", "icon", "favicon", "brand mark", "app icon", "wordmark")):
        return "branding"
    if any(
        w in q
        for w in (
            "slide",
            "slides",
            "deck",
            "powerpoint",
            "ppt",
            "presentation",
            "keynote",
            "16:9",
            "widescreen",
        )
    ):
        return "presentation"
    if any(
        w in q
        for w in (
            "document",
            "report",
            "docx",
            "figure",
            "diagram",
            "chart",
            "illustration for",
            "in the report",
            "for the paper",
        )
    ):
        return "document"
    if (output_type or "").strip().lower() in ("presentation",):
        return "presentation"
    if (output_type or "").strip().lower() in ("document",):
        return "document"
    return "standalone"


def classify_image_generation_roles(
    user_query: str,
    *,
    has_context: bool = False,
    image_use: ImageUseCase | None = None,
) -> list[str]:
    use = image_use or infer_image_use_case(user_query)
    roles: list[str] = []
    if has_context:
        roles.append("img_context_synthesizer")
    q = (user_query or "").lower()
    needs_brief = use in ("document", "presentation", "branding") or any(
        w in q for w in ("plan", "storyboard", "composition", "layout", "series", "multiple")
    )
    if needs_brief:
        roles.append("img_brief_planner")
    if _ACTION_VERBS.search(q):
        roles.append("img_action_scene")
    composer = {
        "branding": "img_brand_composer",
        "presentation": "img_slide_composer",
        "document": "img_document_composer",
    }.get(use, "img_standalone_composer")
    roles.append(composer)
    return roles


def get_image_role(role_id: str) -> ChatRole:
    return _IMAGE_ROLES.get(role_id, _IMAGE_ROLES["img_standalone_composer"])
