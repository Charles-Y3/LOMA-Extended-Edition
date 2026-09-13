# -*- coding: utf-8 -*-
"""Global workflow stage indicators (Intent → Planner → Execution → Synthesis)."""
from __future__ import annotations

from pipeline.execution_modes.constants import normalize_execution_mode
from pipeline.state_machine import ACTIVE, DONE, FAILED, STANDBY, STAGES, WorkflowStateMachine


def execution_mode_from_session() -> str:
    from pipeline.capability_runtime.execution_config import execution_mode_from_settings
    from services.session import state

    return execution_mode_from_settings(state.current_settings)


def reset_stages(sink) -> None:
    WorkflowStateMachine(sink).reset()


def on_workflow_begin(sink) -> None:
    reset_stages(sink)
    WorkflowStateMachine(sink).set_stage("Intent", ACTIVE)
    try:
        from ui.components.ux_guidance import set_progress_detail

        set_progress_detail("Intent")
    except Exception:
        pass


def advance_lite_preflight(sink) -> None:
    """Lite: mark Planner and Execution complete before synthesis (no yellow/red idle)."""
    sm = WorkflowStateMachine(sink)
    sm.set_stage("Intent", DONE)
    sm.set_stage("Planner", DONE)
    sm.set_stage("Execution", DONE)


def on_intent_complete(sink, execution_mode: str | None = None) -> None:
    mode = normalize_execution_mode(execution_mode or execution_mode_from_session())
    sm = WorkflowStateMachine(sink)
    sm.set_stage("Intent", DONE)
    if mode == "direct":
        advance_lite_preflight(sink)
        # Flip the Synthesis icon to active in the same call that sets the "Preparing
        # output…" detail text — otherwise there's a window where the text already says
        # "preparing" but the icon still shows idle (⚪) until the later on_synthesis_active()
        # call right before run_direct(), which for fast/express replies can be visible.
        sm.set_stage("Synthesis", ACTIVE)
        try:
            from ui.components.ux_guidance import set_progress_detail

            set_progress_detail("Synthesis")
        except Exception:
            pass
    else:
        try:
            from ui.components.ux_guidance import set_progress_detail

            set_progress_detail("Planner")
        except Exception:
            pass


def on_planner_active(sink) -> None:
    WorkflowStateMachine(sink).set_stage("Planner", ACTIVE)
    try:
        from ui.components.ux_guidance import set_progress_detail

        set_progress_detail("Planner")
    except Exception:
        pass


def on_planner_complete(sink) -> None:
    WorkflowStateMachine(sink).set_stage("Planner", DONE)


def on_execution_active(sink) -> None:
    WorkflowStateMachine(sink).set_stage("Execution", ACTIVE)
    try:
        from ui.components.ux_guidance import set_progress_detail

        set_progress_detail("Execution")
    except Exception:
        pass


def on_execution_complete(sink) -> None:
    WorkflowStateMachine(sink).set_stage("Execution", DONE)


def on_synthesis_active(sink) -> None:
    WorkflowStateMachine(sink).set_stage("Synthesis", ACTIVE)
    try:
        from ui.components.ux_guidance import set_progress_detail

        set_progress_detail("Synthesis")
    except Exception:
        pass


def on_synthesis_complete(sink, execution_mode: str | None = None) -> None:
    mode = normalize_execution_mode(execution_mode or execution_mode_from_session())
    sm = WorkflowStateMachine(sink)
    sm.set_stage("Synthesis", DONE)
    if mode == "direct":
        sm.set_stage("Intent", DONE)
        sm.set_stage("Planner", DONE)
        sm.set_stage("Execution", DONE)


def on_workflow_succeeded(sink, execution_mode: str | None = None) -> None:
    sm = WorkflowStateMachine(sink)
    for stage in STAGES:
        sm.set_stage(stage, DONE)
    try:
        from ui.components.ux_guidance import set_progress_detail

        set_progress_detail(None)
    except Exception:
        pass
    try:
        from services.voice_reply import finish_or_fallback_reply

        finish_or_fallback_reply()
    except Exception:
        pass


def on_workflow_failed(sink) -> None:
    WorkflowStateMachine(sink).fail_all()
    try:
        from ui.components.ux_guidance import set_progress_detail

        set_progress_detail(None)
    except Exception:
        pass


def ensure_lite_stages_before_synthesis(sink, execution_mode: str | None = None) -> None:
    """Call before LLM/synthesis work in Lite mode."""
    mode = normalize_execution_mode(execution_mode or execution_mode_from_session())
    if mode == "direct":
        advance_lite_preflight(sink)
    on_synthesis_active(sink)
