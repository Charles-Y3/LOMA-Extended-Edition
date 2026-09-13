# -*- coding: utf-8 -*-
"""Roles for presentation_generation capability."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole
from pipeline.roles.deliverable_generation_roles import (
    get_deliverable_generation_role,
)


def classify_presentation_generation_roles(
    user_query: str,
    *,
    has_context: bool = False,
) -> list[str]:
    """Single slide author (optionally synthesize sources first)."""
    roles: list[str] = []
    if has_context:
        roles.append("synthesizer")
    roles.append("slide_author")
    return roles


def get_presentation_generation_role(role_id: str) -> ChatRole:
    from pipeline.direct.task_roles import get_task_role

    if role_id in ("slide_author", "deck_planner", "synthesizer"):
        return get_task_role(role_id)
    return get_deliverable_generation_role(role_id, "presentation_generation")
