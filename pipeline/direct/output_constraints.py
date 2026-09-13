# -*- coding: utf-8 -*-
"""Consolidated output constraints for the direct pipeline."""
from __future__ import annotations

from dataclasses import dataclass

from pipeline.contracts.chat_contracts import get_chat_contract
from pipeline.contracts.deliverable_generation_contracts import get_deliverable_generation_contract
from pipeline.contracts.mutation_contracts import get_mutation_contract
from pipeline.contracts.presentation_generation_contracts import get_presentation_generation_contract
from pipeline.deliverables.contracts import get_deliverable_contract


@dataclass(frozen=True)
class OutputConstraint:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


def _from_any(contract) -> OutputConstraint:
    return OutputConstraint(
        id=contract.id,
        description=contract.description,
        output_rules=contract.output_rules,
        max_chars=contract.max_chars,
    )


_ALIASES: dict[str, str] = {
    "chat_default": "chat_default",
    "chat_summary": "chat_summary",
    "chat_translation": "chat_translation",
    "chat_extract": "chat_extract",
    "chat_rewrite": "chat_rewrite",
    "document_markdown": "document_markdown",
    "content_analysis": "content_analysis",
    "presentation_markdown": "presentation_markdown",
    "presentation_deck_spec": "presentation_deck_spec",
    "presentation_metadata_json": "presentation_metadata_json",
    "presentation_narrative_json": "presentation_narrative_json",
    "presentation_slide_plan_json": "presentation_slide_plan_json",
    "presentation_slide_copy": "presentation_slide_copy",
    "image_prompt": "image_prompt",
    "mutation_fragment_map": "mutation_fragment_map",
    "mutation_selective_translate": "mutation_selective_translate",
    "mutation_full_translate": "mutation_full_translate",
    "mutation_summarize": "mutation_summarize",
    "mutation_rewrite": "mutation_rewrite",
    "presentation_mutation_fragment_map": "mutation_fragment_map",
    "presentation_mutation_selective_translate": "mutation_selective_translate",
    "document_mutation_fragment_map": "mutation_fragment_map",
    "deliverable_outline": "deliverable_outline",
    "deliverable_synthesis": "deliverable_synthesis",
    "deliverable_markdown": "deliverable_markdown",
}

_EXTRA: dict[str, OutputConstraint] = {}


def get_output_constraint(constraint_id: str) -> OutputConstraint:
    cid = (constraint_id or "chat_default").strip()
    if cid in _EXTRA:
        return _EXTRA[cid]
    key = _ALIASES.get(cid, cid)
    if key in _EXTRA:
        return _EXTRA[key]
    # Every get_*_contract() below falls back to its own default instead of
    # raising on an unknown id, so a plain try/except cascade always "succeeds"
    # on the first getter and never reaches the rest. Checking contract.id == key
    # after each call distinguishes a genuine hit from a silent fallback.
    for getter in (
        get_chat_contract,
        get_mutation_contract,
        get_presentation_generation_contract,
        get_deliverable_generation_contract,
        get_deliverable_contract,
    ):
        try:
            contract = getter(key)
        except Exception:
            continue
        if getattr(contract, "id", None) == key:
            return _from_any(contract)
    return _from_any(get_chat_contract("chat_default"))


# Role -> task-substance contract, independent of delivery format. This is the single
# source of truth for "what should this role write" — a translator/summarizer/analyst step
# gets the SAME contract whether its result lands in chat, a .docx, or feeds a slide deck.
# Only the terminal authoring/format-shaping roles (writer, synthesizer, slide_author,
# deck_planner — handled below, not in this table) are delivery-specific, because authoring
# from scratch and reflowing into slides ARE the format concern, by definition. Collapsing
# every role into one delivery-keyed contract here was the root cause of two real bugs: a
# translator told to "author a document with headings" (produced a half-translated,
# half-echoed mess) and a plain-answer role inheriting document_markdown's dataset/
# correlation rules and hallucinating statistics for a non-tabular source.
_TASK_CONTRACT_BY_ROLE: dict[str, str] = {
    "translator": "chat_translation",
    "selective_translator": "chat_translation",
    "summarizer": "chat_summary",
    "extractor": "chat_extract",
    "editor": "chat_rewrite",
    "tone_rewriter": "chat_rewrite",
    "outliner": "chat_outline",
    "transcriber": "chat_transcript",
    "data_analyst": "content_analysis",
    "general_answer": "chat_default",
}


def default_constraint_id(output_type: str, mode: str, role_id: str = "") -> str:
    ot = (output_type or "chat").strip().lower()
    m = (mode or "generation").strip().lower()
    role = (role_id or "").strip().lower()
    if m == "mutation":
        if role in ("selective_translator", "mutation_selective_translator"):
            return "mutation_selective_translate"
        if role in ("translator", "mutation_full_translator", "mutation_translator"):
            return "mutation_full_translate"
        if role in ("summarizer", "mutation_summarizer"):
            return "mutation_summarize"
        if role in ("editor", "tone_rewriter", "mutation_rewriter"):
            return "mutation_rewrite"
        return "mutation_fragment_map"

    if role in _TASK_CONTRACT_BY_ROLE:
        return _TASK_CONTRACT_BY_ROLE[role]

    if ot == "presentation":
        if role == "slide_author":
            return "presentation_markdown"
        if role == "deck_planner":
            return "presentation_deck_spec"
        if role == "synthesizer":
            return "deliverable_synthesis"
        return "presentation_markdown"
    if ot == "document":
        return "document_markdown"
    if ot == "image":
        return "image_prompt"
    return "chat_default"


def build_constraint_text(constraint: OutputConstraint) -> str:
    lines = [constraint.description, "Rules:"]
    lines.extend(f"- {rule}" for rule in constraint.output_rules)
    if constraint.max_chars:
        lines.append(f"- Maximum length: {constraint.max_chars} characters.")
    return "\n".join(lines)
