# -*- coding: utf-8 -*-
"""Small UX helpers: flow cue, routing summary, progress detail."""
from __future__ import annotations

from pipeline.i18n import t as tr


def format_route_summary(
    *,
    execution_mode: str,
    output_type: str,
    reason: str,
) -> str:
    mode_key = "ux.route_mode_plan" if (execution_mode or "").lower() == "plan" else "ux.route_mode_direct"
    out = (output_type or "chat").strip() or "chat"
    why = (reason or "").strip() or out
    return tr(
        "ux.route_line",
        mode=tr(mode_key),
        output=out.upper(),
        reason=why,
    )


def set_progress_detail(stage: str | None) -> None:
    from services.session import state

    key = {
        "Intent": "progress.detail.intent",
        "Planner": "progress.detail.planner",
        "Execution": "progress.detail.execution",
        "Synthesis": "progress.detail.synthesis",
    }.get(stage or "")
    # Empty (not "Ready"/idle text) — process_indicator.py's render_progress()
    # is documented as "visible only while a request is actually in flight";
    # giving the idle state real text meant that if this refresh landed before
    # end_workflow() flips state.workflow_active back to False (it runs in the
    # caller's `finally`, after this), the bar would render once with a
    # spinner next to "Ready" and then never get told to re-render — stuck
    # showing that contradictory state indefinitely. An empty string hides it
    # regardless of that ordering.
    state.progress_detail = tr(key) if key else ""
    try:
        from services.session.workflow_control import schedule_on_ui

        def _refresh() -> None:
            from ui.components.process_indicator import render_progress

            render_progress.refresh()

        schedule_on_ui(_refresh)
    except Exception:
        pass


def publish_routing_summary(
    *,
    execution_mode: str,
    output_type: str,
    reason: str,
    log_fn=None,
) -> str:
    """Store + optionally log the user-facing routing line."""
    from services.session import state

    summary = format_route_summary(
        execution_mode=execution_mode,
        output_type=output_type,
        reason=reason,
    )
    state.last_routing_summary = summary
    if log_fn:
        try:
            log_fn(summary)
        except Exception:
            pass
    return summary
