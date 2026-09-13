# -*- coding: utf-8 -*-
from nicegui import ui

from services.session import state
from ui.themes import registry


@ui.refreshable
def render_progress() -> None:
    """Single live status line — spinner + short phrase — visible only while a request
    is actually in flight. Replaces the old four-stage bulb pill: those stages (Intent/
    Planner/Execution/Synthesis, still used internally by pipeline/state_machine.py)
    don't map to anything a user decides or needs to track."""
    bar = registry.progress_bar
    if bar is None:
        return
    bar.clear()
    detail = (getattr(state, "progress_detail", None) or "").strip()
    show = bool(detail and state.workflow_active)
    bar.set_visibility(show)
    if show:
        with bar:
            ui.spinner(size="1em").classes("text-blue-400 shrink-0")
            ui.label(detail).classes("text-[11px] text-blue-300/90 truncate")
