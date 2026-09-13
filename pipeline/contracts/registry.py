"""Contract registry entrypoints."""
from __future__ import annotations

from pipeline.contracts.chat_contracts import (
    ChatContract,
    get_chat_contract,
    get_chat_contract_for_role,
)
from pipeline.contracts.deliverable_generation_contracts import (
    DeliverableGenerationContract,
    get_deliverable_generation_contract,
    get_deliverable_generation_contract_for_role,
)
from pipeline.contracts.document_contracts import (
    DocumentContract,
    get_document_contract,
    get_document_contract_for_role,
)
from pipeline.contracts.image_contracts import (
    ImageContract,
    get_image_contract,
    get_image_contract_for_role,
)
from pipeline.contracts.media_transcription_contracts import (
    MediaTranscriptionContract,
    get_media_transcription_contract,
    get_media_transcription_contract_for_role,
)
from pipeline.contracts.mutation_contracts import (
    MutationContract,
    get_mutation_contract,
    get_mutation_contract_for_role,
)
from pipeline.contracts.presentation_generation_contracts import (
    PresentationGenerationContract,
    get_presentation_generation_contract,
    get_presentation_generation_contract_for_role,
)
from pipeline.contracts.software_contracts import (
    SoftwareContract,
    get_software_contract,
    get_software_contract_for_role,
)
from pipeline.roles.deliverable_generation_roles import is_deliverable_generation_capability
from pipeline.roles.mutation_roles import is_mutation_capability

Contract = (
    ChatContract
    | DocumentContract
    | ImageContract
    | MutationContract
    | MediaTranscriptionContract
    | DeliverableGenerationContract
    | PresentationGenerationContract
    | SoftwareContract
)


def get_contract_for_role(role_id: str, capability_id: str = "chat") -> Contract:
    cap = (capability_id or "chat").strip().lower()
    if cap in ("document_generator", "selection_revision"):
        return get_document_contract_for_role(role_id)
    if cap == "image_generation":
        return get_image_contract_for_role(role_id)
    if cap == "image_mutation":
        return get_image_contract_for_role(role_id)
    if cap == "media_transcription":
        return get_media_transcription_contract_for_role(role_id)
    if is_mutation_capability(cap):
        return get_mutation_contract_for_role(role_id)
    if cap == "presentation_generation":
        if role_id.startswith("pres_"):
            return get_presentation_generation_contract_for_role(role_id)
        return get_deliverable_generation_contract_for_role(role_id, capability_id=cap)
    if is_deliverable_generation_capability(cap):
        return get_deliverable_generation_contract_for_role(role_id, capability_id=cap)
    if cap == "software":
        return get_software_contract_for_role(role_id)
    if cap == "direct_pipeline":
        if role_id.startswith("mutation_"):
            return get_mutation_contract_for_role(role_id)
        from pipeline.direct.task_contracts import get_task_contract_for_role

        return get_task_contract_for_role(role_id)
    return get_chat_contract_for_role(role_id)


__all__ = [
    "ChatContract",
    "Contract",
    "DeliverableGenerationContract",
    "DocumentContract",
    "ImageContract",
    "MediaTranscriptionContract",
    "MutationContract",
    "get_chat_contract",
    "get_chat_contract_for_role",
    "get_contract_for_role",
    "get_deliverable_generation_contract",
    "get_deliverable_generation_contract_for_role",
    "get_document_contract",
    "get_document_contract_for_role",
    "get_image_contract",
    "get_image_contract_for_role",
    "get_media_transcription_contract",
    "get_media_transcription_contract_for_role",
    "get_mutation_contract",
    "get_mutation_contract_for_role",
    "SoftwareContract",
    "get_software_contract",
    "get_software_contract_for_role",
]
