"""Deterministic validators for document capability contracts."""
from __future__ import annotations

from pipeline.contracts.document_contracts import DocumentContract
from pipeline.validate.result import ValidationResult


def validate_document_contract(text: str, contract: DocumentContract) -> ValidationResult:
    body = (text or "").strip()
    errors: list[str] = []
    if not body:
        errors.append("response is empty")
        return ValidationResult(valid=False, errors=errors)

    if contract.max_chars and len(body) > contract.max_chars:
        errors.append(f"response exceeds max length ({len(body)}>{contract.max_chars})")

    if body.startswith("```"):
        errors.append("output must not be wrapped in markdown fences")

    lower = body.lower()
    if contract.id == "document_outline":
        if "\n" not in body:
            errors.append("outline should contain multiple lines")
    elif contract.id == "document_markdown":
        banned = (
            "save as word",
            "save to word",
            "export as docx",
            "here is the document",
            "below is the document",
        )
        if any(tok in lower for tok in banned):
            errors.append("document body includes meta instructions or preamble")
    elif contract.id.startswith("excerpt_"):
        banned = (
            "here is",
            "revised excerpt:",
            "translation:",
            "translated text:",
        )
        if any(tok in lower for tok in banned):
            errors.append("excerpt revision includes meta preamble")

    return ValidationResult(valid=(len(errors) == 0), errors=errors)
