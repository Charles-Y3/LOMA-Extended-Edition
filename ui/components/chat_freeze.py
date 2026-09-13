# -*- coding: utf-8 -*-
"""Save workspace chat snapshots to data/chats (Chat Archive extension)."""
from __future__ import annotations

import os
from datetime import datetime

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import chat_archive
from services.session import state


def build_freeze_dialog() -> None:
    from ui.themes import tokens

    theme = tokens.get_theme()
    with ui.dialog() as freeze_dialog, ui.card().classes(
        f"w-[320px] p-5 rounded-xl {theme['settings_card']}"
    ):
        ui.label(tr("freeze.title")).classes("text-xs font-bold text-purple-400 mb-2")
        freeze_title_input = ui.input(
            tr("freeze.title_label"), placeholder=tr("freeze.title_ph")
        ).props("dense outlined").classes("w-full mb-3")
        freeze_pending = {"content": "", "source_type": ""}

        def confirm_freeze() -> None:
            title = (freeze_title_input.value or tr("freeze.untitled")).strip()
            now = datetime.now()
            timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
            filename_timestamp = now.strftime("%Y%m%d_%H%M%S")
            file_name = f"freeze_{filename_timestamp}.md"
            try:
                from extensions.chat_archive_manager.extension import get_selected_folder

                folder = get_selected_folder()
            except Exception:
                folder = ""
            rel_name = chat_archive.rel_path(folder, file_name)
            file_path = str(chat_archive.chat_path(rel_name))
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            markdown_content = (
                f"# LOMA CHAT FREEZE EXPORT\n\n"
                f"## METADATA\n"
                f"- **Title:** {title}\n"
                f"- **Export Date/Time:** {timestamp_str}\n"
                f"- **Source Type:** {freeze_pending['source_type']}\n\n"
                f"---\n\n"
                f"## CONTENT\n"
                f"{freeze_pending['content']}\n"
            )
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(markdown_content)
                chat_archive.register_freeze(rel_name, title, timestamp_str)
                try:
                    from extensions.chat_archive_manager.extension import render_chats_tab

                    render_chats_tab.refresh()
                except Exception:
                    pass
                ui.notify(tr("freeze.saved", title=title), color="positive")
                freeze_dialog.close()
                if freeze_pending.get("source_type") == tr("freeze.source_summary"):
                    closer = getattr(state, "close_chat_summarize_dialog", None)
                    if closer:
                        closer()
            except Exception as e:
                ui.notify(tr("freeze.save_failed", error=str(e)), color="negative")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(tr("freeze.cancel"), on_click=freeze_dialog.close).props("flat dense")
            ui.button(tr("freeze.save"), on_click=confirm_freeze).props(
                "flat dense color=positive"
            )

    async def open_freeze_dialog() -> None:
        prefill = (getattr(state, "_summarize_freeze_prefill", None) or "").strip()
        state._summarize_freeze_prefill = ""
        if prefill:
            freeze_pending["content"] = prefill
            freeze_pending["source_type"] = tr("freeze.source_summary")
            freeze_title_input.value = tr("freeze.source_summary")
            freeze_dialog.open()
            return

        try:
            selected_text = await ui.run_javascript(
                "window.getSelection ? String(window.getSelection()) : ''"
            )
        except Exception:
            selected_text = ""
        selected_text = (selected_text or "").strip()
        if selected_text:
            freeze_pending["content"] = selected_text
            freeze_pending["source_type"] = tr("freeze.source_selection")
            freeze_title_input.value = ""
            freeze_dialog.open()
            return

        lines = []
        for msg in state.messages or []:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role") or ""
            content = (msg.get("content") or "").strip()
            if not content:
                continue
            label = "USER" if role == "user" else "LOMA"
            lines.append(f"### {label}\n\n{content}")
        freeze_pending["content"] = "\n\n".join(lines)
        freeze_pending["source_type"] = tr("freeze.source_full")
        freeze_title_input.value = ""
        freeze_dialog.open()

    state.open_chat_freeze_dialog = open_freeze_dialog
