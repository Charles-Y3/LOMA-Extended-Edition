# -*- coding: utf-8 -*-
"""Shared highlight → query / add-to-sources dialogs for extension viewers."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import state
from services.session.prompt_memory import last_typed_prompt, remember_typed_prompt

_highlight_dialog_open = False


def render_last_query_chip(instruction_field) -> None:
    """Clickable chip to reuse the user's last typed prompt text."""
    last = last_typed_prompt()
    if not last:
        return
    preview = last if len(last) <= 72 else last[:69] + "..."

    def use_last() -> None:
        instruction_field.value = last
        instruction_field.update()

    with ui.row().classes("w-full items-center gap-1 flex-wrap"):
        ui.label(tr("viewer.highlight_last_query")).classes("text-[10px] text-gray-500 shrink-0")
        ui.button(preview, on_click=use_last).props("flat dense no-caps").classes(
            "text-[11px] text-cyan-300/90 hover:bg-cyan-500/10 normal-case "
            "max-w-full truncate text-left"
        ).tooltip(last)


def add_text_to_sources(text: str, label: str = "viewer_excerpt") -> None:
    excerpt = (text or "").strip()
    if not excerpt:
        ui.notify(tr("viewer.highlight_nothing_add"), color="warning")
        return
    name = f"{label[:40]}.txt" if label else "excerpt.txt"
    existing = [f.get("filename") for f in state.active_context_files]
    if name in existing:
        ui.notify(tr("viewer.highlight_already_sources"), color="warning")
        return
    if len(state.active_context_files) >= 5:
        ui.notify(tr("viewer.highlight_sources_limit"), color="warning")
        return
    state.active_context_files.append(
        {"filename": name, "type": "text", "content": excerpt, "_attached_turn": state.context_attach_turn}
    )
    ui.notify(tr("viewer.highlight_added"), color="positive")
    try:
        from ui.components.attachments_hub import render_sources_hub

        render_sources_hub.refresh()
    except Exception:
        pass


def open_highlight_dialog(
    title: str,
    selection: str,
    *,
    on_query,
    source_label: str = "excerpt",
    revise_allowed: bool = False,
    on_revise=None,
    show_add_to_sources: bool = True,
    show_revise: bool = True,
) -> None:
    global _highlight_dialog_open
    sel = (selection or "").strip()
    if not sel or _highlight_dialog_open:
        return
    _highlight_dialog_open = True

    preview = sel if len(sel) <= 240 else sel[:237] + "..."
    with ui.dialog() as dialog, ui.card().classes("min-w-[340px] max-w-lg gap-3 p-4"):
        ui.label(title).classes("text-sm font-bold text-cyan-300")
        ui.label(preview).classes(
            "text-[11px] text-gray-400 font-mono max-h-28 overflow-y-auto break-words "
            "whitespace-pre-wrap [overflow-wrap:anywhere] "
            "bg-black/30 rounded p-2 border border-white/10"
        )
        instruction = ui.input(tr("viewer.highlight_query_placeholder")).props(
            "outlined dense autofocus"
        ).classes("w-full text-[12px]")
        render_last_query_chip(instruction)

        def _focus_input() -> None:
            try:
                instruction.run_method("focus")
            except Exception:
                pass
            try:
                ui.run_javascript(
                    """
                    setTimeout(function() {
                        const dlg = document.querySelector('.q-dialog--active, .q-dialog');
                        if (!dlg) return;
                        const inp = dlg.querySelector('input[type="text"], input:not([type])');
                        if (inp) { inp.focus(); }
                    }, 120);
                    """
                )
            except Exception:
                pass

        def _close_dialog() -> None:
            global _highlight_dialog_open
            _highlight_dialog_open = False
            dialog.close()

        def add_source_only() -> None:
            _close_dialog()
            add_text_to_sources(sel, source_label)

        def apply_and_close() -> None:
            text = (instruction.value or "").strip()
            _close_dialog()
            if not text:
                ui.notify(tr("viewer.highlight_enter_question"), color="warning")
                return
            remember_typed_prompt(text)
            on_query(sel, text)

        def revise_and_close() -> None:
            text = (instruction.value or "").strip()
            _close_dialog()
            if not revise_allowed or on_revise is None:
                ui.notify(tr("viewer.highlight_revise_edit_mode"), color="warning")
                return
            if not text:
                ui.notify(tr("viewer.highlight_describe_change"), color="warning")
                return
            remember_typed_prompt(text)
            on_revise(sel, text)

        def copy_selection() -> None:
            import json

            payload = json.dumps(sel)
            ui.run_javascript(f"navigator.clipboard.writeText({payload})")
            ui.notify(tr("chat.copy_done"), color="positive", timeout=1500)

        with ui.row().classes("w-full justify-end gap-2 mt-1 flex-wrap"):
            ui.button(tr("viewer.highlight_cancel"), on_click=_close_dialog).props("flat dense")
            ui.button(tr("viewer.highlight_copy"), icon="content_copy", on_click=copy_selection).props(
                "flat dense"
            )
            if show_add_to_sources:
                ui.button(tr("viewer.highlight_add_sources"), icon="input", on_click=add_source_only).props(
                    "flat dense"
                )
            ui.button(tr("viewer.highlight_ask"), icon="send", on_click=apply_and_close).props("flat dense").classes(
                "text-cyan-300"
            )
            if show_revise:
                revise_btn = ui.button(tr("viewer.highlight_revise"), on_click=revise_and_close).props(
                    "dense color=primary"
                )
                if not revise_allowed or on_revise is None:
                    revise_btn.props("flat dense")
                    revise_btn.classes("opacity-50")

        instruction.on(
            "keydown.enter",
            revise_and_close if (show_revise and revise_allowed) else apply_and_close,
        )
    def _on_hide() -> None:
        global _highlight_dialog_open
        _highlight_dialog_open = False

    dialog.on("hide", _on_hide)
    dialog.open()
    ui.timer(0.05, _focus_input, once=True)
    ui.timer(0.15, _focus_input, once=True)
    ui.timer(0.35, _focus_input, once=True)


async def read_dom_selection() -> str:
    result = await ui.run_javascript(
        """
        const selection = window.getSelection();
        return selection ? selection.toString().trim() : "";
        """,
        timeout=2.0,
    )
    return (result or "").strip() if isinstance(result, str) else ""
