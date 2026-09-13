# -*- coding: utf-8 -*-
"""Assemble worker step outputs into a final deliverable source."""
from __future__ import annotations

import re

_SLIDE_MARKER = re.compile(r"^---\s*Slide\s+\d+\s*---\s*$", re.MULTILINE | re.IGNORECASE)

from pipeline.deliverables.specs import DeliverableSpec, get_deliverable_spec
from pipeline.schemas.task_schema import AgenticPlan, PlanStep


def _strip_fences(text: str) -> str:
    body = (text or "").strip()
    if not body.startswith("```"):
        return body
    lines = body.split("\n")
    end = len(lines)
    if lines[-1].strip() == "```":
        end = len(lines) - 1
    return "\n".join(lines[1:end]).strip()


def _pick_by_phase(completed: dict[str, str], plan_steps: list[PlanStep], phase: str) -> str:
    for step in reversed(plan_steps):
        sid = step.step_id or ""
        if (step.phase or "").lower() == phase.lower() and sid in completed:
            text = completed[sid].strip()
            if text:
                return text
    return ""


def assemble_deliverable(
    *,
    completed: dict[str, str],
    plan: AgenticPlan,
    output_type: str,
    mode: str | None = None,
) -> str:
    spec = get_deliverable_spec(output_type, mode)
    steps = plan.steps or []

    if spec.output_type == "presentation":
        from pipeline.deliverables.presentation_deck import (
            deck_spec_from_completed,
            parse_deck_spec,
        )
        from services.presentation_markdown import normalize_presentation_markdown

        assemble_md = _pick_by_phase(completed, steps, "assemble")
        if assemble_md:
            normalized = normalize_presentation_markdown(_strip_fences(assemble_md))
            if normalized:
                return normalized

        deck = deck_spec_from_completed(completed, steps)
        if deck:
            return deck.to_markdown()

        for phase in ("draft", "outline"):
            candidate = _pick_by_phase(completed, steps, phase)
            if not candidate:
                continue
            if phase == "outline":
                spec_parsed, _ = parse_deck_spec(candidate)
                if spec_parsed:
                    return spec_parsed.to_markdown()
            normalized = normalize_presentation_markdown(_strip_fences(candidate))
            if normalized and _SLIDE_MARKER.search(normalized):
                return normalized

        last = _last_completed(completed, steps)
        return normalize_presentation_markdown(_strip_fences(last))

    if spec.output_type == "document":
        for phase in ("assemble", "draft"):
            candidate = _pick_by_phase(completed, steps, phase)
            if candidate:
                return _strip_fences(candidate)
        return _strip_fences(_last_completed(completed, steps))

    if spec.output_type == "image":
        for phase in ("prompt", "assemble", "draft"):
            candidate = _pick_by_phase(completed, steps, phase)
            if candidate:
                return _strip_fences(candidate)
        return _strip_fences(_last_completed(completed, steps))

    return _strip_fences(_last_completed(completed, steps))


def _last_completed(completed: dict[str, str], steps: list[PlanStep]) -> str:
    if not completed:
        return ""
    if steps:
        for step in reversed(steps):
            sid = step.step_id or ""
            if sid in completed and completed[sid].strip():
                return completed[sid].strip()
    return list(completed.values())[-1].strip()


def merge_revised_assemble(
    completed: dict[str, str],
    plan: AgenticPlan,
    revised_text: str,
) -> dict[str, str]:
    """Replace the assemble-phase step output after delivery-review revision."""
    updated = dict(completed)
    assemble_id = ""
    for step in reversed(plan.steps or []):
        if (step.phase or "").lower() in ("assemble", "prompt", "draft"):
            assemble_id = step.step_id or ""
            break
    if not assemble_id and plan.steps:
        assemble_id = plan.steps[-1].step_id or str(len(plan.steps))
    if assemble_id:
        updated[assemble_id] = (revised_text or "").strip()
    return updated
