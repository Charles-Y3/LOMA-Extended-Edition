"""Role registry entrypoints."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole, classify_chat_roles, get_chat_role
from pipeline.roles.deliverable_generation_roles import (
    classify_deliverable_generation_roles,
    get_deliverable_generation_role,
    is_deliverable_generation_capability,
)
from pipeline.roles.document_roles import (
    classify_document_generator_roles,
    classify_selection_revision_roles,
    get_document_role,
    get_selection_role,
)
from pipeline.roles.image_roles import classify_image_generation_roles, get_image_role
from pipeline.roles.image_mutation_roles import (
    classify_image_mutation_roles,
    get_image_mutation_role,
)
from pipeline.roles.media_transcription_roles import (
    classify_media_transcription_roles,
    get_media_transcription_role,
)
from pipeline.roles.mutation_roles import (
    classify_mutation_roles,
    get_mutation_role,
    is_mutation_capability,
)
from pipeline.roles.software_roles import classify_software_roles, get_software_role
from pipeline.roles.mutation_roles import get_mutation_role as _get_mutation_role


def classify_capability_roles(user_query: str, capability_id: str, **kwargs) -> list[str]:
    """Role chain by capability type."""
    cap = (capability_id or "").strip().lower()
    if cap == "chat":
        return classify_chat_roles(user_query, has_charts=bool(kwargs.get("has_charts")))
    if cap == "document_generator":
        return classify_document_generator_roles(
            user_query,
            has_context=bool(kwargs.get("has_context")),
            has_charts=bool(kwargs.get("has_charts")),
        )
    if cap == "selection_revision":
        return classify_selection_revision_roles(user_query)
    if cap == "image_generation":
        return classify_image_generation_roles(
            user_query,
            has_context=bool(kwargs.get("has_context")),
            image_use=kwargs.get("image_use"),
        )
    if cap == "image_mutation":
        return classify_image_mutation_roles(user_query)
    if cap == "media_transcription":
        return classify_media_transcription_roles(user_query)
    if is_mutation_capability(cap):
        return classify_mutation_roles(user_query, capability_id=cap)
    if cap == "presentation_generation":
        from pipeline.roles.presentation_generation_roles import classify_presentation_generation_roles

        return classify_presentation_generation_roles(
            user_query,
            has_context=bool(kwargs.get("has_context")),
        )
    if is_deliverable_generation_capability(cap):
        return classify_deliverable_generation_roles(
            user_query,
            capability_id=cap,
            has_context=bool(kwargs.get("has_context")),
        )
    if cap == "software":
        return classify_software_roles(user_query)
    if cap == "direct_pipeline":
        from pipeline.direct.query_planner import _infer_roles

        return _infer_roles(user_query, has_charts=bool(kwargs.get("has_charts")))
    return ["general_answer"]


def get_role(role_id: str, capability_id: str = "chat") -> ChatRole:
    cap = (capability_id or "chat").strip().lower()
    if cap == "document_generator":
        return get_document_role(role_id)
    if cap == "selection_revision":
        return get_selection_role(role_id)
    if cap == "image_generation":
        return get_image_role(role_id)
    if cap == "image_mutation":
        return get_image_mutation_role(role_id)
    if cap == "media_transcription":
        return get_media_transcription_role(role_id)
    if is_mutation_capability(cap):
        return get_mutation_role(role_id, capability_id=cap)
    if cap == "presentation_generation":
        from pipeline.roles.presentation_generation_roles import get_presentation_generation_role

        return get_presentation_generation_role(role_id)
    if is_deliverable_generation_capability(cap):
        return get_deliverable_generation_role(role_id, capability_id=cap)
    if cap == "software":
        return get_software_role(role_id)
    if cap == "direct_pipeline":
        if role_id.startswith("mutation_"):
            return _get_mutation_role(role_id, capability_id="document_mutation")
        from pipeline.direct.task_roles import get_task_role

        return get_task_role(role_id)
    return get_chat_role(role_id)


def resolve_chat_role(user_query: str) -> ChatRole:
    from pipeline.roles.chat_roles import classify_chat_role

    role_id = classify_chat_role(user_query)
    return get_chat_role(role_id)


def resolve_chat_roles(
    user_query: str,
    capability_id: str = "chat",
    **kwargs,
) -> list[ChatRole]:
    ids = classify_capability_roles(user_query, capability_id, **kwargs)
    return [get_role(rid, capability_id) for rid in ids]


def resolve_roles(
    user_query: str,
    capability_id: str = "chat",
    **kwargs,
) -> list[ChatRole]:
    return resolve_chat_roles(user_query, capability_id, **kwargs)
