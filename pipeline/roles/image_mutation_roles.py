# -*- coding: utf-8 -*-
"""Roles for image_mutation — global edit vs pixel-preserving local edit."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole

_IMAGE_MUTATION_ROLES: dict[str, ChatRole] = {
    "img_mut_global_editor": ChatRole(
        id="img_mut_global_editor",
        description="Whole-image img2img edit guided by user instruction.",
        system_prompt=(
            "Role: Global Image Editor.\n"
            "Apply the user instruction across the image using img2img.\n"
            "Use when the user wants a broad restyle or scene change."
        ),
        default_contract_id="image_mut_global",
    ),
    "img_mut_preserve_editor": ChatRole(
        id="img_mut_preserve_editor",
        description="Minimal-change edit — preserve pixels outside the requested region.",
        system_prompt=(
            "Role: Preserve Image Editor.\n"
            "Change only what the user names (object, region, background, text).\n"
            "Keep composition, faces, and unrelated areas as close to the source as possible.\n"
            "Prefer low diffusion strength and localized edits."
        ),
        default_contract_id="image_mut_preserve",
    ),
}

def classify_image_mutation_roles(user_query: str) -> list[str]:
    from pipeline.query_intent_i18n import matches

    q = user_query or ""
    if matches(q, "preserve_edit_hints"):
        return ["img_mut_preserve_editor"]
    return ["img_mut_global_editor"]


def get_image_mutation_role(role_id: str) -> ChatRole:
    return _IMAGE_MUTATION_ROLES.get(role_id, _IMAGE_MUTATION_ROLES["img_mut_global_editor"])
