# -*- coding: utf-8 -*-
"""Workspace chat summarizer dialog."""
from __future__ import annotations

import threading

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import state
from ui.themes.tokens import get_theme


def build_summarize_dialog() -> None:
    pending: dict[str, str] = {"text": ""}
    theme = get_theme()

    with ui.dialog() as dialog, ui.card().classes(
        "w-[min(640px,92vw)] max-h-[85vh] flex flex-col p-5 bg-[#121216] border border-white/10 rounded-xl"
    ):
        ui.label(tr("toolbar.summarize_title")).classes("text-xs font-bold text-cyan-400 mb-2 shrink-0")
        summary_holder = ui.column().classes(
            "w-full flex-1 min-h-[200px] max-h-[60vh] overflow-y-auto loma-scroll"
        )

        def _render_summary(text: str) -> None:
            summary_holder.clear()
            with summary_holder:
                ui.markdown(text or "_…_").classes(f"text-sm {theme['text']} w-full")

        async def _save_summary() -> None:
            text = (pending.get("text") or "").strip()
            if not text:
                ui.notify(tr("toolbar.summarize_empty"), color="warning")
                return
            opener = getattr(state, "open_chat_freeze_dialog", None)
            if not opener:
                ui.notify(tr("ui.archive_not_ready"), color="warning")
                return
            state._summarize_freeze_prefill = text
            await opener()
            state._summarize_freeze_prefill = ""

        with ui.row().classes("w-full justify-end gap-2 shrink-0 mt-3"):
            ui.button(tr("toolbar.summarize_close"), on_click=dialog.close).props("flat dense")
            ui.button(tr("toolbar.summarize_save"), on_click=_save_summary).props(
                "flat dense color=positive"
            )

    def _run_summarize() -> None:
        if state.workflow_active:
            ui.notify(tr("toolbar.summarize_busy"), color="warning")
            return
        from services.inference.readiness import inference_ready

        if not inference_ready():
            ui.notify(tr("startup.loading"), type="info")
            return
        pending["text"] = ""
        _render_summary(tr("toolbar.summarize_working"))
        client = ui.context.client
        dialog.open()

        def worker() -> None:
            try:
                from services.session.chat_summarize import summarize_workspace_chat

                text = summarize_workspace_chat()
            except Exception as exc:
                text = tr("summarize.failed_error", error=exc)

            def apply() -> None:
                with client:
                    pending["text"] = text
                    _render_summary(text)

            from services.session.workflow_control import schedule_on_ui

            schedule_on_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    state.open_chat_summarize_dialog = _run_summarize
    state.close_chat_summarize_dialog = dialog.close
