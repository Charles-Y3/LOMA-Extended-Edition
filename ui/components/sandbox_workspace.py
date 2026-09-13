# -*- coding: utf-8 -*-
"""Sandbox tab — stable layout (no full-panel refresh on Run)."""
from __future__ import annotations

import threading

from nicegui import ui

from pipeline.i18n import t as tr
from services.sandbox.stdin_detect import code_uses_stdin
from services.session import state
from services.software_delivery import run_sandbox_from_state, stop_sandbox_from_ui
from ui.themes import registry, tokens


def mount_sandbox_workspace() -> None:
    """Build sandbox UI once; use sync_display() to update after runs."""
    root = registry.sandbox_panel_inner
    if root is None:
        return
    root.clear()
    t = tokens.get_theme()

    # Ensure the sandbox area never causes horizontal scrolling.
    root.classes(add="w-full h-full min-h-0 min-w-0 overflow-x-hidden")

    with root:
        with ui.row().classes("w-full items-center gap-2 shrink-0 mb-2 min-w-0"):
            registry.sandbox_status_label = ui.label("").classes(
                f"text-[10px] font-bold tracking-wide {t['muted']} truncate flex-1 min-w-0"
            )
            registry.sandbox_run_btn = ui.button(
                tr("sandbox.run"), icon="play_arrow", on_click=_on_run
            ).props("dense no-caps").classes("shrink-0 bg-green-600/25 text-green-400 px-2")
            registry.sandbox_stop_btn = ui.button(
                tr("sandbox.stop"), icon="stop", on_click=_on_stop
            ).props("dense no-caps").classes("shrink-0 bg-red-600/25 text-red-400 px-2")

        with ui.tabs().classes("w-full text-[11px] min-h-[32px] shrink-0") as inner_tabs:
            registry.sandbox_code_tab = ui.tab("code").classes("lowercase text-xs")
            registry.sandbox_output_tab = ui.tab("output").classes("lowercase text-xs")
        registry.sandbox_inner_tabs = inner_tabs

        with ui.tab_panels(inner_tabs, value=registry.sandbox_code_tab).classes(
            "w-full flex-1 min-h-0 min-w-0 bg-transparent p-0 overflow-x-hidden"
        ):
            with ui.tab_panel(registry.sandbox_code_tab).classes(
                "p-0 h-full flex flex-col min-h-0 min-w-0 overflow-x-hidden"
            ):
                registry.sandbox_code_ta = (
                    ui.textarea(value=_code_value())
                    .props('outlined autogrow="false"')
                    .classes(f"w-full flex-1 min-h-0 min-w-0 font-mono text-[11px] leading-relaxed {t['input_text']} overflow-x-hidden")
                    .style(
                        "height: 100%; width: 100%; box-sizing: border-box; resize: none; overflow: auto;"
                    )
                    .on_value_change(lambda _: _sync_code_from_editor())
                )

                registry.sandbox_stdin_section = ui.column().classes("w-full shrink-0 mt-2 min-w-0")
                with registry.sandbox_stdin_section:
                    registry.sandbox_stdin_ta = (
                        ui.textarea(value=getattr(state, "sandbox_stdin", "") or "")
                        .props(f'outlined placeholder="{tr("sandbox.stdin_label")}"')
                        .classes(f"w-full font-mono text-[11px] {t['input_text']}")
                        .style(
                            "height: 64px; min-height: 64px; max-height: 64px; "
                            "width: 100%; box-sizing: border-box;"
                        )
                        .on_value_change(lambda _: _sync_stdin_from_editor())
                    )

            with ui.tab_panel(registry.sandbox_output_tab).classes(
                "p-0 h-full flex flex-col min-h-0 min-w-0 overflow-x-hidden"
            ):
                registry.sandbox_output_ta = (
                    ui.textarea(value=_output_value())
                    .props("outlined readonly")
                    .classes(f"w-full flex-1 min-h-0 min-w-0 font-mono text-[11px] leading-relaxed {t['console_log']} overflow-x-hidden")
                    .style(
                        "height: 100%; width: 100%; box-sizing: border-box; resize: none; overflow: auto;"
                    )
                )

    sync_stdin_visibility()
    sync_display()


def sync_display() -> None:
    """Refresh status, output, and buttons without destroying the code editor."""
    if registry.sandbox_output_ta is not None:
        registry.sandbox_output_ta.value = _output_value()
    if registry.sandbox_status_label is not None:
        registry.sandbox_status_label.text = _status_text()
        registry.sandbox_status_label.classes(_status_color_class(), remove="text-green-400 text-amber-400 text-red-400 text-amber-300 text-gray-500")
    running = bool(getattr(state, "sandbox_running", False))
    if registry.sandbox_run_btn:
        (registry.sandbox_run_btn.enable if not running else registry.sandbox_run_btn.disable)()
    if registry.sandbox_stop_btn:
        (registry.sandbox_stop_btn.enable if running else registry.sandbox_stop_btn.disable)()


def sync_stdin_visibility() -> None:
    show = code_uses_stdin(_code_value())
    if registry.sandbox_stdin_section is not None:
        registry.sandbox_stdin_section.set_visibility(show)
        # Keep output area filling space when stdin hides/shows.
        try:
            if registry.sandbox_code_ta is not None:
                registry.sandbox_code_ta.update()
        except Exception:
            pass


def load_sandbox_code(code: str) -> None:
    state.sandbox_code = code or ""
    if registry.sandbox_code_ta is not None:
        registry.sandbox_code_ta.value = state.sandbox_code
    sync_stdin_visibility()
    sync_display()
    activate_code_tab()


def _code_value() -> str:
    if registry.sandbox_code_ta is not None:
        return (registry.sandbox_code_ta.value or "").strip() or getattr(state, "sandbox_code", "") or ""
    return getattr(state, "sandbox_code", "") or ""


def _output_value() -> str:
    out = getattr(state, "sandbox_output", "") or ""
    if out:
        return out
    if getattr(state, "sandbox_running", False):
        return tr("sandbox.running")
    return tr("sandbox.no_output")


def _status_text() -> str:
    status = getattr(state, "sandbox_status", "") or "idle"
    return tr("sandbox.status", status=status)


def _status_color_class() -> str:
    status = getattr(state, "sandbox_status", "") or "idle"
    if status in ("pass", "ready"):
        return "text-green-400"
    if status == "running":
        return "text-amber-400"
    if status == "stopped":
        return "text-amber-300"
    if status in ("fail", "error"):
        return "text-red-400"
    return "text-gray-500"


def _sync_code_from_editor() -> None:
    if registry.sandbox_code_ta is not None:
        state.sandbox_code = registry.sandbox_code_ta.value or ""
    sync_stdin_visibility()


def _sync_stdin_from_editor() -> None:
    if registry.sandbox_stdin_ta is not None:
        state.sandbox_stdin = registry.sandbox_stdin_ta.value or ""


def _on_run() -> None:
    if getattr(state, "sandbox_running", False):
        return
    state.sandbox_code = _code_value()
    if not state.sandbox_code.strip():
        ui.notify(tr("sandbox.no_code"), type="warning")
        return

    state.sandbox_running = True
    state.sandbox_status = "running"
    state.sandbox_output = ""
    sync_display()
    activate_output_tab()

    stdin_text = (registry.sandbox_stdin_ta.value or "") if registry.sandbox_stdin_ta else ""
    state.sandbox_stdin = stdin_text

    def _worker() -> None:
        try:
            run_sandbox_from_state(state)
            state.add_log(f"Sandbox run: {state.sandbox_status}")
        except Exception as exc:
            state.sandbox_status = "error"
            state.sandbox_output = str(exc)
        finally:
            state.sandbox_running = False
            from services.session.workflow_control import schedule_on_ui

            schedule_on_ui(sync_display)

    threading.Thread(target=_worker, daemon=True).start()


def _on_stop() -> None:
    stop_sandbox_from_ui(state)
    sync_display()


def activate_output_tab() -> None:
    if registry.sandbox_inner_tabs is not None and registry.sandbox_output_tab is not None:
        registry.sandbox_inner_tabs.set_value(registry.sandbox_output_tab)


def activate_code_tab() -> None:
    if registry.sandbox_inner_tabs is not None and registry.sandbox_code_tab is not None:
        registry.sandbox_inner_tabs.set_value(registry.sandbox_code_tab)


# Back-compat for callers that still refresh()
def render_sandbox_panel() -> None:
    sync_display()
