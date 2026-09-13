# -*- coding: utf-8 -*-
"""Deterministic validators for agentic deliverable contracts."""
from __future__ import annotations

import re

from pipeline.deliverables.contracts import DeliverableContract
from pipeline.direct.prompt_hygiene import looks_like_refusal
from pipeline.validate.result import ValidationResult

_SLIDE_MARKER = re.compile(r"^---\s*Slide\s+\d+\s*---\s*$", re.MULTILINE | re.IGNORECASE)
_SCRIPT_SLIDE = re.compile(r"^(?:Final\s+)?Slide\s+\d+\s*:", re.MULTILINE | re.IGNORECASE)


def validate_deliverable_contract(
    text: str,
    contract: DeliverableContract,
    *,
    slide_count: int | None = None,
) -> ValidationResult:
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
    cid = contract.id

    if cid == "research_notes":
        if _SLIDE_MARKER.search(body):
            errors.append("research step must not include --- Slide N --- markers")
        if body.count("\n\n") > 40 and len(body) > 3000:
            errors.append("research is too long — use concise bullets and slide theme lines")

    if cid == "document_markdown":
        banned = ("save as word", "save to word", "export as docx", "here is the document")
        if any(tok in lower for tok in banned) or looks_like_refusal(body):
            errors.append("document includes meta instructions, a refusal, or preamble")
        if not re.search(r"^#{1,3}\s+", body, re.MULTILINE) and len(body) > 200:
            errors.append("document should include section headings (##)")

    elif cid == "presentation_markdown":
        if _SCRIPT_SLIDE.search(body):
            errors.append("use --- Slide N --- markdown, not Slide N: script format")
        markers = _SLIDE_MARKER.findall(body)
        if not markers:
            errors.append("presentation must include --- Slide N --- markers")
        elif slide_count is not None and len(markers) != slide_count:
            errors.append(f"expected {slide_count} slides, found {len(markers)}")

    elif cid == "presentation_outline":
        if _SLIDE_MARKER.search(body):
            errors.append("outline step should not include --- Slide N --- markers yet")

    elif cid == "presentation_deck_spec":
        from pipeline.deliverables.presentation_deck import parse_deck_spec, validate_deck_spec

        spec, parse_errors = parse_deck_spec(body)
        if parse_errors:
            errors.extend(parse_errors[:5])
        elif spec:
            v = validate_deck_spec(spec, slide_count=slide_count)
            errors.extend(v.errors)

    elif cid == "presentation_slide_copy":
        if _SLIDE_MARKER.search(body):
            errors.append("slide copy step must not include --- Slide N --- markers")
        if not body.lstrip().startswith("{"):
            errors.append("slide copy must be JSON with slides array")

    elif cid == "image_prompt":
        banned = ("here is", "image prompt:", "stable diffusion:", "```")
        if any(tok in lower for tok in banned):
            errors.append("image prompt includes meta preamble")
        if len(body) < 12:
            errors.append("image prompt is too short")

    elif cid == "document_outline":
        if "\n" not in body:
            errors.append("outline should contain multiple lines")

    return ValidationResult(valid=(len(errors) == 0), errors=errors)
