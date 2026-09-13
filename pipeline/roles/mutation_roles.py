# -*- coding: utf-8 -*-
"""Roles for Office and stub mutation capabilities."""

from __future__ import annotations

import re

from pipeline.roles.chat_roles import ChatRole

_MUTATION_CAPABILITY_IDS = frozenset(
    {
        "document_mutation",
        "presentation_mutation",
        "image_mutation",
    }
)

_ROLES: dict[str, ChatRole] = {
    "mutation_selective_translator": ChatRole(
        id="mutation_selective_translator",
        description="Translate only specified source-language spans in each fragment.",
        system_prompt=(
            "Role: Selective Language Translator.\n"
            "Follow the user instruction exactly for which language spans to translate "
            "and which target language to use.\n"
            "Per fragment: change only the spans that match the instruction; "
            "leave all other text unchanged."
        ),
        default_contract_id="mutation_selective_translate",
    ),
    "mutation_full_translator": ChatRole(
        id="mutation_full_translator",
        description="Translate entire fragments to a target language.",
        system_prompt=(
            "Role: Full Fragment Translator.\n"
            "Translate the complete text of each affected fragment to the target language "
            "named in the user instruction.\n"
            "Never summarize or shorten — every sentence must be translated."
        ),
        default_contract_id="mutation_full_translate",
    ),
    "mutation_translator": ChatRole(
        id="mutation_translator",
        description="Translate fragments (defaults to full-fragment translation).",
        system_prompt=(
            "Role: Mutation Translator.\n"
            "Apply translation instructions to each text fragment faithfully.\n"
            "Preserve structure and emphasis from the source."
        ),
        default_contract_id="mutation_full_translate",
    ),
    "mutation_summarizer": ChatRole(
        id="mutation_summarizer",
        description="Summarize or condense fragment text.",
        system_prompt=(
            "Role: Mutation Summarizer.\n"
            "Summarize each fragment per the user instruction (bullets, brevity, language)."
        ),
        default_contract_id="mutation_summarize",
    ),
    "mutation_rewriter": ChatRole(
        id="mutation_rewriter",
        description="Rewrite tone, clarity, or style in uploaded file text.",
        system_prompt=(
            "Role: Mutation Rewriter.\n"
            "Rewrite fragments per the user instruction while preserving facts and layout intent."
        ),
        default_contract_id="mutation_rewrite",
    ),
    "mutation_editor": ChatRole(
        id="mutation_editor",
        description="General in-place edit of Office text units.",
        system_prompt=(
            "Role: Mutation Editor.\n"
            "Edit each text fragment per the user instruction.\n"
            "Change only what the instruction requires."
        ),
        default_contract_id="mutation_fragment_map",
    ),
}

def _is_selective_translation(q: str) -> bool:
    """Detect selective translation intent without hardcoding language names."""
    from pipeline.query_intent_i18n import matches

    if not matches(q, "verb_translate"):
        return False
    return matches(q, "selective_scope")


def classify_mutation_roles(user_query: str, capability_id: str = "document_mutation") -> list[str]:
    from pipeline.query_intent_i18n import matches

    q = (user_query or "").lower()
    has_translate = matches(q, "verb_translate")
    has_summarize = matches(q, "verb_summarize")

    if has_summarize and has_translate:
        if _is_selective_translation(q):
            return ["mutation_selective_translator", "mutation_summarizer"]
        return ["mutation_summarizer", "mutation_full_translator"]

    if _is_selective_translation(q):
        return ["mutation_selective_translator"]

    if has_summarize:
        return ["mutation_summarizer"]

    if has_translate:
        return ["mutation_full_translator"]

    if matches(q, "verb_rewrite") or matches(q, "tone_formal") or matches(q, "tone_academic"):
        return ["mutation_rewriter"]

    return ["mutation_editor"]


def get_mutation_role(role_id: str, capability_id: str = "document_mutation") -> ChatRole:
    _ = capability_id
    return _ROLES.get(role_id, _ROLES["mutation_editor"])


def is_mutation_capability(capability_id: str) -> bool:
    return (capability_id or "").strip().lower() in _MUTATION_CAPABILITY_IDS
