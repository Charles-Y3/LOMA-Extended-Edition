# -*- coding: utf-8 -*-
"""Roles for presentation generation capabilities."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole

_GENERATION_CAPABILITY_IDS = frozenset(
    {
        "presentation_generation",
    }
)

_BASE_ROLES: dict[str, ChatRole] = {
    "gen_outliner": ChatRole(
        id="gen_outliner",
        description="Plan structure before drafting the deliverable body.",
        system_prompt=(
            "Role: Deliverable Outliner.\n"
            "Produce a clear outline (sections, slides, or sheets) for the requested output.\n"
            "Do not write the full final body yet."
        ),
        default_contract_id="deliverable_outline",
    ),
    "gen_synthesizer": ChatRole(
        id="gen_synthesizer",
        description="Synthesize facts from workspace context for drafting.",
        system_prompt=(
            "Role: Deliverable Synthesizer.\n"
            "Organize relevant facts from workspace context for the requested deliverable.\n"
            "Stay grounded in provided sources."
        ),
        default_contract_id="deliverable_synthesis",
    ),
    "gen_author": ChatRole(
        id="gen_author",
        description="Draft the full markdown body for compile/export.",
        system_prompt=(
            "Role: Deliverable Author.\n"
            "Write the complete markdown body directly.\n"
            "Follow the output shape implied by the user request."
        ),
        default_contract_id="deliverable_markdown",
    ),
}

_PRESENTATION_AUTHOR = ChatRole(
    id="gen_author",
    description="Draft slide-oriented markdown for .pptx compile.",
    system_prompt=(
        "Role: Presentation Author.\n"
        "Write markdown with --- between slides.\n"
        "Headings must be meaningful topics, never 'Slide N' or meta labels like Title/Subtitle.\n"
        "Use concise bullets (max 5 per slide, short phrases). Follow tone and slide count from the brief."
    ),
    default_contract_id="presentation_markdown",
)


def classify_deliverable_generation_roles(
    user_query: str,
    capability_id: str = "presentation_generation",
    *,
    has_context: bool = False,
) -> list[str]:
    q = (user_query or "").lower()
    roles: list[str] = []
    if has_context:
        roles.append("gen_synthesizer")
    if any(w in q for w in ("outline", "plan", "structure", "sections", "slides")):
        roles.append("gen_outliner")
    roles.append("gen_author")
    return roles


def get_deliverable_generation_role(role_id: str, capability_id: str = "presentation_generation") -> ChatRole:
    cap = (capability_id or "").strip().lower()
    if role_id == "gen_author":
        if cap == "presentation_generation":
            return _PRESENTATION_AUTHOR
    return _BASE_ROLES.get(role_id, _BASE_ROLES["gen_author"])


def is_deliverable_generation_capability(capability_id: str) -> bool:
    return (capability_id or "").strip().lower() in _GENERATION_CAPABILITY_IDS
