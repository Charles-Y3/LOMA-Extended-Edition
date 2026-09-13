"""Validation engine dispatchers."""
from __future__ import annotations

from pipeline.contracts.chat_contracts import ChatContract
from pipeline.contracts.document_contracts import DocumentContract
from pipeline.contracts.image_contracts import ImageContract
from pipeline.validate.chat_validators import validate_chat_contract
from pipeline.validate.document_validators import validate_document_contract
from pipeline.validate.image_validators import validate_image_contract
from pipeline.validate.result import ValidationResult

# Contract ids with a structural check (slide markers, deck JSON parsing, etc.)
# already implemented in pipeline.deliverables.validators for Plan mode. Direct
# mode resolves the same ids (via pipeline.direct.output_constraints' alias
# chain), so route them through the same check instead of falling through to
# generic chat validation, which doesn't know about slide/deck structure.
_STRUCTURAL_DELIVERABLE_IDS = frozenset(
    {
        "research_notes",
        "document_outline",
        "document_markdown",
        "presentation_outline",
        "presentation_deck_spec",
        "presentation_slide_copy",
        "presentation_markdown",
        "image_prompt",
    }
)


def validate_output(
    text: str, contract: ChatContract | DocumentContract | ImageContract
) -> ValidationResult:
    if isinstance(contract, ImageContract):
        return validate_image_contract(text, contract)
    if isinstance(contract, DocumentContract):
        return validate_document_contract(text, contract)
    if getattr(contract, "id", "") in _STRUCTURAL_DELIVERABLE_IDS:
        from pipeline.deliverables.validators import validate_deliverable_contract

        return validate_deliverable_contract(text, contract)
    return validate_chat_contract(text, contract)


def validate_chat_output(text: str, contract: ChatContract) -> ValidationResult:
    return validate_chat_contract(text, contract)
