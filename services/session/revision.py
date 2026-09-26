# -*- coding: utf-8 -*-
"""Targeted revision of a highlighted Preview excerpt."""
from __future__ import annotations

from pipeline.capability_runtime.chat_runner import generate_text_sync
from pipeline.i18n import t as _tr  # noqa: E402


def _strip_fences(text: str) -> str:
    revised = (text or "").strip()
    if revised.startswith("```"):
        lines = revised.split("\n")
        revised = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:]).strip()
    return revised


def _build_revision_prompt(excerpt: str, instruction: str) -> str:
    from pipeline.query_intent_i18n import matches

    lower = (instruction or "").lower()
    if matches(lower, "verb_translate"):
        return (
            "Translate the excerpt exactly per the user instruction.\n"
            "Output ONLY the translated excerpt text — no labels, quotes, or commentary.\n\n"
            f"INSTRUCTION:\n{instruction}\n\n"
            f"EXCERPT:\n{excerpt}"
        )
    return (
        "Revise the following text excerpt according to the user instruction.\n"
        "Output ONLY the revised excerpt text — no labels or commentary.\n\n"
        f"INSTRUCTION:\n{instruction}\n\n"
        f"EXCERPT:\n{excerpt}"
    )


def revise_excerpt(
    excerpt: str,
    instruction: str,
    model: str,
    profile: dict | None = None,
    *,
    execution_mode: str = "lite",
    sink=None,
    is_cancelled=None,
) -> str:
    """LLM revises only the selected excerpt; returns replacement text."""
    excerpt = (excerpt or "").strip()
    instruction = (instruction or "").strip()
    if not excerpt or not instruction:
        return excerpt

    from pipeline.capability_runtime.chat_runner import generate_text_sync

    prompt = _build_revision_prompt(excerpt, instruction)
    prof = profile or {}
    out = generate_text_sync(
        prof,
        model,
        [{"role": "user", "content": prompt}],
    )
    return _strip_fences(out) or excerpt


def explain_excerpt(
    excerpt: str,
    question: str,
    model: str,
    profile: dict | None = None,
) -> str:
    """Explain or answer questions about a highlighted excerpt; does not revise it."""
    excerpt = (excerpt or "").strip()
    if not excerpt:
        return _tr("preview.no_excerpt")

    question = (question or "").strip()
    if not question:
        question = "Explain what this text means in clear, concise language."

    from services import llm_bridge as chat_client
    from services.llm_bridge import build_chat_request
    from services.resource_governor import ResourceGovernor

    prompt = (
        "The user highlighted the following excerpt from their document preview.\n"
        "Answer their question about it. Do NOT rewrite, revise, or replace the excerpt — "
        "only explain, clarify, or answer.\n\n"
        f"QUESTION:\n{question}\n\n"
        f"EXCERPT:\n{excerpt}"
    )
    chat_kwargs, _ = build_chat_request(
        profile,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        stream=False,
        extra_options={"temperature": 0.4},
    )
    with ResourceGovernor.acquire("llm_chat"):
        response = chat_client.chat(**chat_kwargs)
    answer = (response.get("message", {}).get("content") or "").strip()
    return answer or _tr("preview.no_answer")


def splice_excerpt(
    full_draft: str,
    excerpt: str,
    replacement: str,
    *,
    start: int | None = None,
    end: int | None = None,
) -> str:
    """Replace excerpt in draft; prefer character indices when provided."""
    if not full_draft:
        return full_draft
    if start is not None and end is not None and 0 <= start < end <= len(full_draft):
        return full_draft[:start] + replacement + full_draft[end:]
    if excerpt and excerpt in full_draft:
        return full_draft.replace(excerpt, replacement, 1)
    return full_draft
