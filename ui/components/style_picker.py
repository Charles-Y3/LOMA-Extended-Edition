# -*- coding: utf-8 -*-
"""Lightweight modal picker for choosing a template/style before generation —
mirrors ui/components/capability_installer.py's OnDemandInstaller dialog shape
(modal ui.dialog + ui.card, schedule_on_ui for worker-thread callers), but for a
plain "pick one of N options" choice rather than a package install."""
from __future__ import annotations

from typing import Callable


def _open_on_client(build_fn: Callable[[], None]) -> None:
    """Schedule `build_fn` to run explicitly bound to the (single, LOMA is not
    multi-session) connected client, on the NiceGUI event loop thread.

    services.session.workflow_control.schedule_on_ui tries the ambient
    slot-stack context first and only falls back to `with client:` if that
    raises — fine for refreshing an EXISTING element (e.g. a progress label),
    but unreliable for creating a brand-new top-level element like a dialog
    from a background pipeline thread: the ambient context can silently
    resolve to a stale/wrong parent with no error, leaving the new element
    attached nowhere visible. Binding to the real client unconditionally
    avoids that."""

    def _run() -> None:
        from nicegui import app

        clients = list(app.clients())
        if not clients:
            return
        with clients[-1]:
            build_fn()

    try:
        from nicegui import core

        if core.loop and core.loop.is_running():
            core.loop.call_soon_threadsafe(_run)
            return
    except Exception:
        pass
    try:
        import asyncio

        loop = asyncio.get_running_loop()
        loop.call_soon_threadsafe(_run)
        return
    except RuntimeError:
        pass
    _run()


def run_generation_on_client(fn: Callable[[], None], *, sink=None) -> None:
    """Resume a long-running generation call — after the user picks a style in
    one of these pickers — on a background thread bound to the connected
    client for the call's ENTIRE duration, wrapped in the same
    begin_workflow()/end_workflow() lifecycle a normal chat turn gets.

    Without this, a picker's on_select callback that just calls the
    generation function directly on a bare thread silently breaks: the send
    button never flips to the stop state, the live progress indicator never
    appears, streamed content never reaches the chat, and — worst — the final
    "artifact ready" notification is wrapped in a bare try/except inside
    ui/themes/sink.py, so on an unbound thread it throws and is swallowed
    with no visible message at all, even though the file was generated
    successfully. Binding the client with `with client:` for the whole call
    (not just one dialog, see _open_on_client above) makes every NiceGUI
    update nested inside `fn` — progress refreshes, chat refreshes, the final
    notification — resolve against the real client instead of an ambient
    context that doesn't exist on a plain background thread.

    `sink`, if given, also gets the same on_workflow_succeeded()/
    on_workflow_failed() hooks a normal chat turn fires at the end — those
    are what reset the progress indicator (e.g. "step 8/8 (100%)") back to
    idle; without them it's left showing the last diffusion step forever
    after the picker-driven generation finishes. It also gets the
    "Preparing output…" status a normal turn shows while its synthesis stage
    is active — without it, the status line above the message box stays
    blank for however long the author LLM call takes, before any diffusion
    progress exists to show instead."""

    def _run() -> None:
        from nicegui import app

        from services.session.workflow_control import begin_workflow, end_workflow

        clients = list(app.clients())
        if not clients:
            return
        with clients[-1]:
            begin_workflow()
            if sink is not None:
                from pipeline.progress_stages import ensure_lite_stages_before_synthesis

                ensure_lite_stages_before_synthesis(sink)
            try:
                fn()
            except Exception as exc:
                if sink is not None:
                    from pipeline.workflow import handle_workflow_exception

                    # Same translation run_workflow()'s own top-level except
                    # block does (context-length exceeded, model not found,
                    # ...) — this picker-resume path calls generation directly
                    # rather than through run_workflow(), so without this call
                    # any exception here previously failed completely
                    # silently: no chat message, just a stuck empty bubble.
                    handle_workflow_exception(sink, exc)
                else:
                    raise
            else:
                if sink is not None:
                    from pipeline.progress_stages import on_workflow_succeeded

                    on_workflow_succeeded(sink)
            finally:
                end_workflow()

    import threading

    threading.Thread(target=_run, daemon=True).start()


def _show_option_picker(
    *,
    title_key: str,
    subtitle_key: str,
    cancel_key: str,
    options: list[dict],
    on_select: Callable[[str], None],
) -> None:
    """Shared modal shape for both the poster and presentation pickers. Safe to
    call from a background/worker thread — marshals the dialog onto the UI
    thread itself. `options` rows need `id`/`label_key`/`description_key`."""

    def _open() -> None:
        from nicegui import ui

        from pipeline.i18n import t as tr

        with ui.dialog() as dialog, ui.card().classes("w-[520px] p-6"):
            ui.label(tr(title_key)).classes("text-xl font-bold text-primary")
            ui.label(tr(subtitle_key)).classes("text-sm text-gray-600 mb-2")

            def _choose(option_id: str) -> None:
                dialog.close()
                on_select(option_id)

            for opt in options:
                with ui.card().classes(
                    "w-full my-2 p-3 cursor-pointer hover:bg-gray-50"
                ).on("click", lambda _e=None, oid=opt["id"]: _choose(oid)):
                    with ui.row().classes("items-center gap-3 w-full no-wrap"):
                        thumb = opt.get("thumbnail_svg")
                        if thumb:
                            ui.html(thumb).classes("shrink-0 rounded")
                        with ui.column().classes("gap-0"):
                            ui.label(tr(opt["label_key"])).classes("font-semibold")
                            ui.label(tr(opt["description_key"])).classes("text-xs text-gray-500")

            with ui.row().classes("w-full justify-end mt-4"):
                ui.button(tr(cancel_key), on_click=dialog.close).props("flat")

            dialog.open()

    _open_on_client(_open)


def show_poster_style_picker(*, on_select: Callable[[str], None]) -> None:
    """Show the poster template picker. `on_select(template_id)` fires once,
    when the user clicks an option, then the dialog closes."""
    from services.poster_generation import POSTER_TEMPLATES

    _show_option_picker(
        title_key="poster.style_picker.title",
        subtitle_key="poster.style_picker.subtitle",
        cancel_key="poster.style_picker.cancel",
        options=POSTER_TEMPLATES,
        on_select=on_select,
    )


def show_presentation_style_picker(*, on_select: Callable[[str], None]) -> None:
    """Show the presentation deck style picker. `on_select(style_id)` fires
    once, when the user clicks an option, then the dialog closes."""
    from pipeline.deliverables.presentation_theme import PRESENTATION_STYLES

    _show_option_picker(
        title_key="presentation.style_picker.title",
        subtitle_key="presentation.style_picker.subtitle",
        cancel_key="presentation.style_picker.cancel",
        options=PRESENTATION_STYLES,
        on_select=on_select,
    )
