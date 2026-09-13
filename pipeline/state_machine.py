# -*- coding: utf-8 -*-
"""Workflow stage state machine (Intent → Planner → Execution → Synthesis)."""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Iterable

STAGES = ("Intent", "Planner", "Execution", "Synthesis")


class UISink:
    """Protocol for pipeline → UI updates (implemented by NiceGUI sink in ui layer)."""

    def log(self, msg: str) -> None:
        raise NotImplementedError

    def set_progress(self, stage: str, status: str) -> None:
        raise NotImplementedError

    def refresh_progress(self) -> None:
        pass

    def set_capability(self, name: str, status: str) -> None:
        pass

    def refresh_capabilities(self) -> None:
        pass

    def refresh_chat(self) -> None:
        pass

    def refresh_chat_throttled(self) -> None:
        self.refresh_chat()

    def scroll_chat(self) -> None:
        pass

    def append_assistant_token(self, token: str) -> None:
        pass

    def append_assistant_thinking_token(self, token: str) -> None:
        pass

    def set_assistant_content(self, content: str) -> None:
        pass

    def append_assistant_content(self, extra: str) -> None:
        pass

    def ensure_assistant_message(self) -> None:
        pass

    def attach_images_to_last_user(self, images: list) -> None:
        pass

    def sync_preview(self) -> None:
        pass

    def notify_preview_ready(self) -> None:
        pass

    def notify_artifact_ready(self, path: str) -> None:
        pass

    def set_mutation_progress(self, slide: int, total: int) -> None:
        pass

    def pulse_stages(self, stages: list[str], delay: float = 0.08) -> None:
        pass

STANDBY = "⚪"
ACTIVE = "🟡"
DONE = "🟢"
FAILED = "🔴"


class WorkflowStateMachine:
    def __init__(self, sink: UISink) -> None:
        self.sink = sink

    def set_stage(self, stage: str, status: str) -> None:
        self.sink.set_progress(stage, status)
        self.sink.refresh_progress()

    def reset(self) -> None:
        for stage in STAGES:
            self.set_stage(stage, STANDBY)

    def fail_all(self) -> None:
        for stage in STAGES:
            self.set_stage(stage, FAILED)

    def pulse(self, stages: Iterable[str], delay: float = 0.08) -> None:
        for stage in stages:
            self.set_stage(stage, ACTIVE)
            time.sleep(delay)
            self.set_stage(stage, DONE)

    def fast_path_flash(self) -> None:
        """Low-complexity path: quickly advance Planner and Execution."""
        self.sink.pulse_stages(["Planner", "Execution"])


_extension_processing_count = 0
_extension_processing_lock = threading.Lock()


def _set_all_stages(status: str) -> None:
    from services.session import state
    from services.session.workflow_control import schedule_on_ui

    for stage in STAGES:
        state.progress_state[stage] = status

    def _refresh() -> None:
        from ui.components.process_indicator import render_progress

        render_progress.refresh()

    schedule_on_ui(_refresh)


@contextlib.contextmanager
def extension_processing():
    """Light up the top Intent/Planner/Execution/Synthesis indicator while an extension's
    own LLM call is running, instead of each extension inventing its own busy/spinner cue.

    Usage: wrap the LLM call in your extension's own chat helper (see
    extensions/ludicity_shared/llm.py for the reference wiring):

        with extension_processing():
            return generate_text_sync(...)

    Reference-counted so concurrent calls (e.g. Arena's parallel race-mode requests) keep
    the indicator lit until every in-flight call has finished, not just the first to return.
    This is the required convention for ALL extensions — see extensions/EXTENSION_TEMPLATE.md.
    """
    global _extension_processing_count
    with _extension_processing_lock:
        _extension_processing_count += 1
        first = _extension_processing_count == 1
    if first:
        _set_all_stages(ACTIVE)
    try:
        yield
    finally:
        with _extension_processing_lock:
            _extension_processing_count -= 1
            last = _extension_processing_count <= 0
        if last:
            _set_all_stages(STANDBY)


_extension_synth_count = 0
_extension_synth_lock = threading.Lock()


def _set_synthesis_pulse() -> None:
    from services.session import state
    from services.session.workflow_control import schedule_on_ui

    state.progress_state["Intent"] = DONE
    state.progress_state["Planner"] = DONE
    state.progress_state["Execution"] = DONE
    state.progress_state["Synthesis"] = ACTIVE

    def _refresh() -> None:
        from ui.components.process_indicator import render_progress

        render_progress.refresh()

    schedule_on_ui(_refresh)


@contextlib.contextmanager
def extension_synthesizing():
    """For extension work that isn't a plain chat-style LLM call but still takes visible
    time to produce an output — TTS synthesis, image diffusion, a scrape-then-summarize job,
    a document query, a translation run, etc.

    Flips Intent/Planner/Execution to done (green) immediately and pulses Synthesis until the
    wrapped block finishes, then resets everything to standby — the same shape as the main
    chat pipeline's own "lite" execution mode (see pipeline/progress_stages.py). Use this
    instead of extension_processing() when the work isn't itself an LLM chat call:

        with extension_synthesizing():
            generate_image(...)   # or synthesize TTS, scrape+summarize, run a query, ...

    Reference-counted like extension_processing() — see there for the plain-LLM-call variant
    and extensions/EXTENSION_TEMPLATE.md for when to use which.
    """
    global _extension_synth_count
    with _extension_synth_lock:
        _extension_synth_count += 1
        first = _extension_synth_count == 1
    if first:
        _set_synthesis_pulse()
    try:
        yield
    finally:
        with _extension_synth_lock:
            _extension_synth_count -= 1
            last = _extension_synth_count <= 0
        if last:
            _set_all_stages(STANDBY)
