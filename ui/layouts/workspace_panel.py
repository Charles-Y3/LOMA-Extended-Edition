# -*- coding: utf-8 -*-
from nicegui import ui

from pipeline.i18n import t as tr
from services.session import handlers
from ui.components.chat_message import render_chat
from ui.components.process_indicator import render_progress
from ui.components.toolbar import build_workspace_toolbar
from ui.components.voice_controls import mount_voice_mic_button
from ui.themes import registry, tokens
from ui.themes.assets import CHAT_SCROLL_ID


def build_workspace_panel(t: dict) -> None:
    registry.workspace_panel = ui.column().classes(
        f'flex-1 h-full min-h-0 bg-[{t["sidebar"]}] border border-[{t["border"]}] '
        f"rounded-2xl p-5 flex flex-col backdrop-blur-xl shadow-2xl relative"
    ).props("id=loma-workspace-panel")
    with registry.workspace_panel:
        build_workspace_toolbar()

        registry.chat_scroll = (
            ui.scroll_area()
            .props(f"id={CHAT_SCROLL_ID}")
            .classes("w-full flex-1 loma-scroll min-h-0")
        )
        with registry.chat_scroll:
            registry.chat_container = ui.column().classes("w-full pb-4")
        render_chat()

        registry.progress_bar = ui.row().classes(
            "items-center gap-2 px-1 pb-1 shrink-0"
        )
        registry.progress_bar.set_visibility(False)
        render_progress()

        with ui.row().classes(
            f"w-full items-center p-2 rounded-xl mt-auto shrink-0 {t['input_row']}"
        ):
            # autogrow with no cap let a long paste or many newlines grow this
            # textarea tall enough to push the send button (and mic/attach buttons,
            # in the same row) below the visible area — capped here to roughly 3
            # lines via CSS max-height, past which loma-chat-input scrolls
            # internally instead of growing further (see the ui.add_css rule below).
            chat_input = ui.input(placeholder=tr("chat.placeholder")).props(
                f"{t['input_props']} type=textarea autogrow rows=1"
            ).classes(
                f"flex-1 pl-3 text-sm loma-chat-input {t['input_text']}"
            )
            registry.chat_input = chat_input
            ui.add_css(
                """
                .loma-chat-input .q-field__native {
                    max-height: 4.75rem !important;
                    overflow-y: auto !important;
                }
                """
            )
            # Enter alone sends the message; Shift+Enter inserts a newline (default
            # textarea behavior) since it's excluded via the .exact modifier.
            chat_input.on(
                "keydown.enter.exact.prevent", lambda: handlers.handle_chat_action(chat_input)
            )
            chat_input.on("update:model-value", handlers.on_chat_input_change)

            mount_voice_mic_button(chat_input, t)

            registry.chat_send_btn = ui.button(
                icon="send",
                on_click=lambda: handlers.handle_chat_action(chat_input),
            ).props("flat round color=primary").classes(
                "text-blue-500 bg-blue-500/15 shadow-sm"
            )
            with registry.chat_send_btn:
                registry.chat_send_tooltip = ui.tooltip(tr("chat.send_tooltip"))
