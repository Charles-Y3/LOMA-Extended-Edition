"""Contracts for chat role outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ChatContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, ChatContract] = {
    "chat_default": ChatContract(
        id="chat_default",
        description="Default chat answer contract.",
        output_rules=(
            "Answer directly and accurately.",
            "Use concise structure where helpful.",
            "Avoid unnecessary preambles and self-referential commentary.",
        ),
        max_chars=9000,
    ),
    "chat_translation": ChatContract(
        id="chat_translation",
        description="Translation output contract.",
        output_rules=(
            "Return translated content only — never a summary or analysis.",
            "Translate every sentence; preserve list/paragraph structure.",
            "Do not add explanations, prefaces, or 'Workspace Excerpt Analysis' sections.",
        ),
        max_chars=12000,
    ),
    "chat_summary": ChatContract(
        id="chat_summary",
        description="Summary output contract.",
        output_rules=(
            "Summarize ONLY the source text provided in this step — not other files.",
            "Focus on key points and remove repetition.",
            "Prefer short, scannable structure.",
            "Avoid introducing facts not present in available context.",
        ),
        max_chars=7000,
    ),
    "chat_extract": ChatContract(
        id="chat_extract",
        description="Extraction output contract.",
        output_rules=(
            "Return only extracted facts requested by user.",
            "Use concise bullet points unless JSON is explicitly requested.",
            "Do not include unsupported assumptions.",
        ),
        max_chars=7000,
    ),
    "chat_rewrite": ChatContract(
        id="chat_rewrite",
        description="Rewrite output contract.",
        output_rules=(
            "Preserve original meaning while improving clarity.",
            "Avoid changing factual claims.",
            "Do not include explanation unless requested.",
        ),
        max_chars=9000,
    ),
    "chat_outline": ChatContract(
        id="chat_outline",
        description="Outline output contract.",
        output_rules=(
            "Use a structured outline with short numbered sections.",
            "Keep each item concise and actionable.",
            "Avoid long paragraph blocks.",
        ),
        max_chars=7000,
    ),
    "chat_transcript": ChatContract(
        id="chat_transcript",
        description="Verbatim transcript passthrough contract.",
        output_rules=(
            "Return the transcript exactly as provided — its original, verbatim form.",
            "Do not summarize, translate, analyze, or add commentary.",
            "Do not add headings, bullets, or notes that are not already in the source.",
        ),
        max_chars=48000,
    ),
}


def get_chat_contract(contract_id: str) -> ChatContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["chat_default"])


def get_chat_contract_for_role(role_id: str) -> ChatContract:
    role_to_contract = {
        "translator": "chat_translation",
        "summarizer": "chat_summary",
        "extractor": "chat_extract",
        "editor": "chat_rewrite",
        "outliner": "chat_outline",
        "transcriber": "chat_transcript",
        "general_answer": "chat_default",
        "data_analyst": "chat_default",
    }
    return get_chat_contract(role_to_contract.get(role_id, "chat_default"))

