"""Deterministic validators for chat contracts."""
from __future__ import annotations

from pipeline.contracts.chat_contracts import ChatContract
from pipeline.validate.result import ValidationResult


def validate_chat_contract(text: str, contract: ChatContract) -> ValidationResult:
    body = (text or "").strip()
    errors: list[str] = []
    if not body:
        errors.append("response is empty")
        return ValidationResult(valid=False, errors=errors)

    if contract.max_chars and len(body) > contract.max_chars:
        errors.append(f"response exceeds max length ({len(body)}>{contract.max_chars})")

    lower = body.lower()
    if contract.id == "chat_translation":
        banned = (
            "here is",
            "translation:",
            "translated text:",
            "i translated",
        )
        if any(tok in lower for tok in banned):
            errors.append("translation includes meta preamble")
    elif contract.id == "chat_outline":
        if "\n" not in body:
            errors.append("outline should contain multiple lines")
    elif contract.id == "chat_extract":
        if len(body) < 20:
            errors.append("extraction appears too short")

    return ValidationResult(valid=(len(errors) == 0), errors=errors)

