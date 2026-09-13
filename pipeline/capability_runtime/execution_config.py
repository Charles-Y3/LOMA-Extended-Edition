# -*- coding: utf-8 -*-
"""Read execution mode from capability config / session settings."""
from __future__ import annotations

from pipeline.execution_modes.constants import normalize_execution_mode


def execution_mode_from_config(config: dict | None) -> str:
    cfg = config or {}
    raw = cfg.get("execution_mode") or cfg.get("chat_execution_mode")
    return normalize_execution_mode(raw)


def execution_mode_from_settings(settings: dict | None) -> str:
    data = settings or {}
    raw = data.get("execution_mode") or data.get("chat_execution_mode")
    return normalize_execution_mode(raw)
