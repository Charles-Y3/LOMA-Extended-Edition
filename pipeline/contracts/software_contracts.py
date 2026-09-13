# -*- coding: utf-8 -*-
"""Contracts for software capability outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SoftwareContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, SoftwareContract] = {
    "software_plan": SoftwareContract(
        id="software_plan",
        description="Implementation plan bullets.",
        output_rules=(
            "Return a short bullet plan only.",
            "Do not include Python code.",
        ),
        max_chars=4000,
    ),
    "software_python": SoftwareContract(
        id="software_python",
        description="Runnable Python source.",
        output_rules=(
            "Output ONLY Python 3 source in a single ```python fenced block, or raw Python.",
            "No prose after the code block.",
            "Programs using input() are allowed when needed.",
        ),
        max_chars=24000,
    ),
}


def get_software_contract(contract_id: str) -> SoftwareContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["software_python"])


def get_software_contract_for_role(role_id: str) -> SoftwareContract:
    role_to_contract = {
        "software_planner": "software_plan",
        "software_coder": "software_python",
        "software_debugger": "software_python",
    }
    return get_software_contract(role_to_contract.get(role_id, "software_python"))
