# -*- coding: utf-8 -*-
"""Workflow run/cancel flags and workspace control UI sync."""
from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, TypeVar

from services.session import state

T = TypeVar("T")


class WorkflowCancelled(BaseException):
    """Raised by run_cancellable() to unwind a blocking call the instant a Stop click
    lands. Subclasses BaseException (not Exception) — the same trick asyncio.CancelledError
    uses — so it is never accidentally swallowed by the many `except Exception:` blocks in
    the plan/agentic pipeline (orchestrator, coordinator, agents). A caller that wants to
    turn a stop into a clean result must explicitly catch WorkflowCancelled."""


def run_cancellable(fn: Callable[..., T], *args: Any, poll_interval: float = 0.2, **kwargs: Any) -> T:
    """Run a blocking call on a worker thread; return its result as soon as it finishes,
    or raise WorkflowCancelled the moment the user clicks Stop — whichever comes first.

    There is no cooperative server-side abort for a single non-streamed LLM call (Ollama
    has nothing to cancel mid-response), so the abandoned background thread keeps running
    to completion and its result is simply discarded. This still fixes the actual problem:
    the workflow stops blocking the UI immediately on Stop, instead of only after the
    in-flight LLM call finishes on its own — which for a slow plan/orchestration call could
    be a long wait with no way out.
    """
    result: dict[str, T] = {}
    error: dict[str, BaseException] = {}
    done = threading.Event()

    def _run() -> None:
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 - propagate to the caller
            error["exc"] = exc
        finally:
            done.set()

    threading.Thread(target=_run, daemon=True).start()
    while not done.wait(timeout=poll_interval):
        if is_cancelled():
            raise WorkflowCancelled()
    if "exc" in error:
        raise error["exc"]
    return result["value"]


def workflow_processing_message() -> str:
    from pipeline.execution_modes.constants import normalize_execution_mode
    from pipeline.i18n import t as tr

    mode = normalize_execution_mode(
        state.current_settings.get("execution_mode")
        or state.current_settings.get("chat_execution_mode")
        or "direct"
    )
    if mode == "plan":
        return tr("chat.processing_plan")
    return tr("chat.processing")


def begin_workflow() -> None:
    state.workflow_active = True
    state.workflow_cancel_requested = False
    if state.messages and state.messages[-1].get("role") == "assistant":
        if not (state.messages[-1].get("content") or "").strip():
            from pipeline.i18n import t as tr

            state.messages[-1]["content"] = workflow_processing_message()
            state.messages[-1]["processing"] = True
    schedule_on_ui(_refresh_send_button)
    schedule_on_ui(_refresh_processing_chat)


def end_workflow() -> None:
    state.workflow_active = False
    state.workflow_cancel_requested = False
    if state.messages and state.messages[-1].get("role") == "assistant":
        state.messages[-1].pop("processing", None)
    # Attachments are consumed for this turn by the time end_workflow runs (called from
    # a `finally:` after context-building completes) — clear them so they don't silently
    # carry into the next turn's request. Keeps uploaded files on disk in case the user
    # references them again.
    if state.active_context_files or state.active_web_links:
        from services.session.upload_cleanup import clear_all_sources

        clear_all_sources(delete_disk_files=False)
        schedule_on_ui(_refresh_sources_hub)
    schedule_on_ui(_refresh_send_button)
    schedule_on_ui(_refresh_processing_chat)


def _refresh_processing_chat() -> None:
    try:
        ui_mod = state.get_ui_module()
        if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):
            ui_mod.render_chat.refresh()
    except Exception:
        pass


def _refresh_sources_hub() -> None:
    try:
        from ui.components.attachments_hub import render_sources_hub

        render_sources_hub.refresh()
    except Exception:
        pass


def request_cancel() -> None:
    state.workflow_cancel_requested = True
    try:
        from services.sandbox.interactive import stop_sandbox_run

        if stop_sandbox_run():
            state.sandbox_running = False
            state.sandbox_status = "stopped"
            state.add_log("Sandbox run stopped.")
    except Exception:
        pass
    state.add_log("User requested stop — cancelling workflow…")


def is_cancelled() -> bool:
    return bool(state.workflow_cancel_requested)


def reset_progress_if_idle() -> None:
    """Reset stage lightbulbs when the user starts typing (not during a run)."""
    if state.workflow_active:
        return
    if getattr(state, "pending_plan_review", None):
        return
    changed = False
    for key in state.progress_state:
        if state.progress_state[key] != "⚪":
            state.progress_state[key] = "⚪"
            changed = True
    if changed:
        ui_mod = state.get_ui_module()
        if hasattr(ui_mod, "render_progress") and hasattr(ui_mod.render_progress, "refresh"):
            ui_mod.render_progress.refresh()


def _refresh_send_button() -> None:
    try:
        from ui.themes import registry

        btn = registry.chat_send_btn
        if btn is None:
            return
        if state.workflow_active:
            btn.props("flat round icon=stop color=negative")
            btn.classes(remove="text-blue-500 bg-blue-500/10", add="text-red-500 bg-red-500/20")
            from pipeline.i18n import t as tr

            if registry.chat_send_tooltip is not None:
                registry.chat_send_tooltip.set_text(tr("chat.stop_tooltip"))
        else:
            btn.props(remove="color")
            btn.props("flat round icon=send")
            btn.classes(remove="text-red-500 bg-red-500/20", add="text-blue-500 bg-blue-500/10")
            from pipeline.i18n import t as tr

            if registry.chat_send_tooltip is not None:
                registry.chat_send_tooltip.set_text(tr("chat.send_tooltip"))
    except Exception:
        pass


def schedule_on_ui(callback) -> None:
    """Schedule UI work on the NiceGUI event loop (safe from worker threads)."""

    def _run() -> None:
        try:
            callback()
            return
        except RuntimeError as exc:
            msg = str(exc).lower()
            if "slot stack" not in msg and "has been deleted" not in msg:
                raise
        try:
            from nicegui import app

            clients = list(app.clients())
            if not clients:
                return
            with clients[0]:
                callback()
        except Exception:
            pass

    try:
        from nicegui import core

        if core.loop and core.loop.is_running():
            core.loop.call_soon_threadsafe(_run)
            return
    except Exception:
        pass
    try:
        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(_run)
        return
    except RuntimeError:
        pass
    _run()
