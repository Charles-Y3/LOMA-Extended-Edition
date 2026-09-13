# -*- coding: utf-8 -*-
"""Contract resolution for direct pipeline task roles."""
from __future__ import annotations

from pipeline.direct.output_constraints import OutputConstraint, get_output_constraint
from pipeline.direct.task_roles import get_task_role


def get_task_contract_for_role(
    role_id: str,
    *,
    output_type: str = "chat",
    mode: str = "generation",
    constraint_id: str | None = None,
) -> OutputConstraint:
    if constraint_id:
        return get_output_constraint(constraint_id)
    role = get_task_role(role_id)
    from pipeline.direct.output_constraints import default_constraint_id

    cid = default_constraint_id(output_type, mode, role_id)
    if role.default_contract_id and mode == "generation":
        try:
            return get_output_constraint(role.default_contract_id)
        except Exception:
            pass
    return get_output_constraint(cid)
