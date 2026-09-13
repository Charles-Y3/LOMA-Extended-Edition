"""Deterministic validators for image_generation contracts."""
from __future__ import annotations

import re

from pipeline.contracts.image_contracts import ImageContract
from pipeline.validate.result import ValidationResult

_FENCE_RE = re.compile(r"^```|```$", re.MULTILINE)
_META_RE = re.compile(
    r"^(here is|prompt:|image prompt:|stable diffusion:)",
    re.IGNORECASE,
)


def validate_image_contract(text: str, contract: ImageContract) -> ValidationResult:
    body = (text or "").strip()
    errors: list[str] = []
    if not body:
        errors.append("output is empty")
        return ValidationResult(valid=False, errors=errors)

    if contract.max_chars and len(body) > contract.max_chars:
        errors.append(f"output exceeds max length ({len(body)}>{contract.max_chars})")

    if _FENCE_RE.search(body):
        errors.append("output must not use markdown fences")

    if contract.id.startswith("image_prompt_"):
        if "\n\n" in body and len(body.splitlines()) > 3:
            errors.append("diffusion prompt should be a single line or short block")
        if _META_RE.search(body.strip()):
            errors.append("diffusion prompt includes meta preamble")
        if body.startswith('"') and body.endswith('"'):
            errors.append("do not wrap prompt in quotes")
        if len(body) < 12:
            errors.append("diffusion prompt is too short")
    elif contract.id == "image_brief":
        if "\n" not in body and len(body) < 40:
            errors.append("brief should include multiple composition points")
    elif contract.id == "image_context":
        if len(body) < 20:
            errors.append("context synthesis appears too short")
    elif contract.id == "image_action_scene":
        if len(body) > 200:
            errors.append("action scene line is too long")
        if len(body) < 8:
            errors.append("action scene line is too short")

    return ValidationResult(valid=(len(errors) == 0), errors=errors)
