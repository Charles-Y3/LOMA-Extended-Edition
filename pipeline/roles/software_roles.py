# -*- coding: utf-8 -*-
"""Roles for software capability (Python codegen + sandbox)."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole

_ROLES: dict[str, ChatRole] = {
    "software_planner": ChatRole(
        id="software_planner",
        description="Plan Python solution structure before coding.",
        system_prompt=(
            "Role: Software Planner.\n"
            "Outline modules, inputs/outputs, and steps for a runnable Python 3 script.\n"
            "Use short bullets only — no full code yet."
        ),
        default_contract_id="software_plan",
    ),
    "software_coder": ChatRole(
        id="software_coder",
        description="Generate complete runnable Python for the sandbox.",
        system_prompt=(
            "Role: Software Coder.\n"
            "Write complete, runnable Python 3 for the user's request.\n"
            "LOMA runs code in the built-in sandbox."
        ),
        default_contract_id="software_python",
    ),
    "software_debugger": ChatRole(
        id="software_debugger",
        description="Repair Python that failed in the sandbox.",
        system_prompt=(
            "Role: Software Debugger.\n"
            "Fix the script from the error message while preserving the user's intent."
        ),
        default_contract_id="software_python",
    ),
}


def classify_software_roles(user_query: str) -> list[str]:
    q = (user_query or "").lower()
    if any(w in q for w in ("fix", "debug", "error", "traceback", "failed", "repair")):
        return ["software_debugger"]
    if any(w in q for w in ("plan", "outline", "architecture", "steps")):
        return ["software_planner", "software_coder"]
    return ["software_coder"]


def get_software_role(role_id: str) -> ChatRole:
    return _ROLES.get(role_id, _ROLES["software_coder"])
