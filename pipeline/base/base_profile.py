# -*- coding: utf-8 -*-
"""Template and helpers for LOMA YAML profile packs."""
from __future__ import annotations

from abc import ABC
from typing import Any

from pipeline.schemas.task_schema import RoutingDecision


class BaseProfile(ABC):
    """Profiles shape intent, behavior, model routing, and output rules."""

    profile_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        raise NotImplementedError

    def system_instruction(self, decision: RoutingDecision) -> str:
        raise NotImplementedError


def default_profile_dict(profile_id: str) -> dict:
    from pipeline.base.profile_pack import default_profile

    return default_profile(profile_id)
