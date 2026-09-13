# -*- coding: utf-8 -*-
"""Rough wall-clock estimate for a Plan-mode run, from a live model benchmark."""
from __future__ import annotations

from services.system.benchmark import benchmark_tokens_per_second

# Rough token budgets per phase — not exact, just enough to turn a step count
# into a ballpark wall-clock estimate for the user before/while a plan runs.
_PLANNING_TOKEN_BUDGET = 900  # orchestrator mission plan + subtask instructions
_STEP_TOKEN_BUDGET = 700  # worker generation + verifier check, per plan step


def estimate_plan_duration_seconds(orchestrator_model: str, step_count: int) -> float | None:
    """Estimate seconds for a plan-mode run from a live tokens/sec benchmark of
    the orchestrator model on this machine. None if the model can't be benchmarked
    (e.g. inference backend unreachable) — callers should skip showing an ETA then."""
    tokens_per_second = benchmark_tokens_per_second(orchestrator_model)
    if not tokens_per_second:
        return None
    total_tokens = _PLANNING_TOKEN_BUDGET + max(step_count, 0) * _STEP_TOKEN_BUDGET
    return total_tokens / tokens_per_second


def format_eta(seconds: float) -> str:
    if seconds < 60:
        return f"~{round(seconds)}s"
    minutes = seconds / 60
    if minutes < 60:
        return f"~{minutes:.1f} min"
    return f"~{minutes / 60:.1f} hr"
