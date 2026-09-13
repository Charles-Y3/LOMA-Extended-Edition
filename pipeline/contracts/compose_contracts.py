"""Contracts for agentic compose worker outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ComposeContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, ComposeContract] = {
    "compose_general": ComposeContract(
        id="compose_general",
        description="General text worker output contract.",
        output_rules=(
            "Answer directly without unnecessary preambles.",
            "Preserve markdown structure, tables, and paragraph correlation with source.",
            "Respect profile glossary and tone guidelines.",
            "Do not invent facts not present in provided context.",
        ),
        max_chars=12000,
    ),
    "compose_coder": ComposeContract(
        id="compose_coder",
        description="Coder worker output contract.",
        output_rules=(
            "Output runnable Python 3 in a single ```python fenced block or raw Python.",
            "Do not import ui, nicegui, or pipeline modules.",
            "Include only code and minimal inline comments when needed.",
            "No conversational prose after the code block.",
        ),
        max_chars=16000,
    ),
    "compose_vision": ComposeContract(
        id="compose_vision",
        description="Vision worker output contract.",
        output_rules=(
            "Describe only what is visible in the provided images.",
            "Cite image regions or elements when relevant.",
            "Do not speculate about content outside the images.",
            "Use concise, structured prose or bullets.",
        ),
        max_chars=8000,
    ),
    "compose_extract": ComposeContract(
        id="compose_extract",
        description="Extract/read step output contract.",
        output_rules=(
            "Return only extracted facts requested by the task.",
            "Use concise bullet points unless JSON is explicitly requested.",
            "Do not include unsupported assumptions.",
        ),
        max_chars=8000,
    ),
}


def get_compose_contract(contract_id: str) -> ComposeContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["compose_general"])


def get_compose_contract_for_role(role: str) -> ComposeContract:
    role_key = (role or "General").strip().lower()
    if role_key in ("coder", "code"):
        return _CONTRACTS["compose_coder"]
    if role_key in ("vision", "visual"):
        return _CONTRACTS["compose_vision"]
    return _CONTRACTS["compose_general"]


def build_compose_contract_text(contract: ComposeContract) -> str:
    lines = [contract.description, "Rules:"]
    lines.extend(f"- {rule}" for rule in contract.output_rules)
    if contract.max_chars:
        lines.append(f"- Maximum length: {contract.max_chars} characters.")
    return "\n".join(lines)
