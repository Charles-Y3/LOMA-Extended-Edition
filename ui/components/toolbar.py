# -*- coding: utf-8 -*-
from nicegui import ui

from pipeline.i18n import panel_heading_class, t as tr
from services.session import handlers
from services.session import settings as session_settings
from services.session import state
from ui.components.chat_message import render_chat
from ui.components.attachments_hub import render_sources_hub
from ui.components.process_indicator import render_progress
from ui.themes import registry, tokens


def _apply_chat_font_px(px: int) -> None:
    if registry.chat_scroll is not None:
        registry.chat_scroll.style(f"--loma-chat-font-size: {px}px")


def build_workspace_toolbar() -> None:
    with ui.column().classes("w-full shrink-0 gap-0.5 mb-1"):
        with ui.row().classes(tokens.PANEL_HEADER_ROW_WORKSPACE):
            registry.input_open_btn = ui.button(
                on_click=lambda: (
                    registry.input_panel.set_visibility(True),
                    registry.input_open_btn.set_visibility(False),
                )
            ).props("flat round dense icon=menu").classes(
                "text-gray-500 hover:text-blue-400 -ml-1"
            ).tooltip(tr("nav.reopen_tooltip"))
            registry.input_open_btn.set_visibility(False)

            ui.label(tr("panel.workspace")).classes(
                f"{panel_heading_class()} text-blue-500/80 shrink-0 pt-0.5"
            )

            with ui.row().classes("items-center gap-0 shrink-0 -mr-2"):
                def _bump_font(delta: int) -> None:
                    cur = int(state.current_settings.get("chat_font_px", 13))
                    px = max(11, min(20, cur + delta))
                    state.current_settings["chat_font_px"] = px
                    session_settings.save_settings(state.current_settings, quiet=True)
                    _apply_chat_font_px(px)
                    render_chat.refresh()

                ui.button(icon="text_decrease", on_click=lambda: _bump_font(-1)).props(
                    "flat round dense"
                ).classes("text-gray-500 hover:text-blue-400").tooltip(tr("chat.font_decrease"))

                ui.button(icon="text_increase", on_click=lambda: _bump_font(1)).props(
                    "flat round dense"
                ).classes("text-gray-500 hover:text-blue-400").tooltip(tr("chat.font_increase"))

                ui.button(icon="bookmark_add", on_click=_open_chat_freeze).props("flat round dense").classes(
                    "text-gray-500 hover:text-purple-400"
                ).tooltip(tr("toolbar.freeze_tooltip"))

                ui.button(icon="summarize", on_click=_open_chat_summarize).props("flat round dense").classes(
                    "text-gray-500 hover:text-cyan-400"
                ).tooltip(tr("toolbar.summarize_tooltip"))

                ui.button(icon="folder_open", on_click=_open_output_folder).props("flat round dense").classes(
                    "text-gray-500 hover:text-amber-400"
                ).tooltip(tr("toolbar.output_folder_tooltip"))

                ui.button(
                    icon="refresh",
                    on_click=lambda: handlers.reboot_workspace(
                        render_chat, render_progress, render_sources_hub
                    ),
                ).props("flat round dense").classes("text-gray-500 hover:text-red-400").tooltip(
                    tr("toolbar.reboot_tooltip")
                )


async def _open_chat_freeze() -> None:
    opener = getattr(state, "open_chat_freeze_dialog", None)
    if opener:
        await opener()
    else:
        ui.notify(tr("toolbar.freeze_not_ready"), color="warning")


def _open_chat_summarize() -> None:
    opener = getattr(state, "open_chat_summarize_dialog", None)
    if opener:
        opener()
    else:
        ui.notify(tr("toolbar.summarize_not_ready"), color="warning")


def _open_output_folder() -> None:
    from services.platform_paths import open_path_in_os

    try:
        open_path_in_os("data/generated")
    except Exception as exc:
        ui.notify(f"{tr('toolbar.output_folder_failed')}: {exc}", color="negative")
