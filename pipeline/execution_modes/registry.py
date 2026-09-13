"""Execution strategy registry (Direct / Plan)."""
from __future__ import annotations

from pipeline.execution_modes.constants import VALID_EXECUTION_MODES, normalize_execution_mode

__all__ = [
    "VALID_EXECUTION_MODES",
    "normalize_execution_mode",
    "list_execution_modes",
]


def list_execution_modes() -> list[str]:
    return ["direct", "plan"]


# Back-compat aliases used by older imports
def get_chat_strategy(mode_id: str):
    """Deprecated: execution is handled by pipeline.capability_runtime.role_pipeline."""
    raise NotImplementedError(
        "Use pipeline.capability_runtime.role_pipeline.run_role_pipeline instead."
    )
