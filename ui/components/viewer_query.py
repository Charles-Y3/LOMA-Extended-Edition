# -*- coding: utf-8 -*-
"""Send extension viewer highlights to the workspace and run LOMA workflow."""
from __future__ import annotations

import threading

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import state
from services.session.prompt_memory import remember_typed_prompt
from ui.themes import registry
from ui.themes.assets import schedule_scroll_chat


def submit_highlight_query(selected_text: str, source_label: str, instruction: str) -> None:
    """
    Same flow as workspace Send: append user message, refresh chat, start workflow.
    Must run on the NiceGUI UI thread (e.g. from highlight dialog button handler).
    """
    if not (selected_text or "").strip():
        ui.notify("No highlighted text detected.", color="warning")
        return
    if not (instruction or "").strip():
        ui.notify("Describe what you want LOMA to do with the selection.", color="warning")
        return

    clean = selected_text.strip()
    source_part = (source_label or "").strip() or "the document"
    query = (
        f"{tr('chat.excerpt_prefix', source=source_part)}\n\n"
        f'"{clean}"\n\n'
        f"{instruction.strip()}"
    )

    remember_typed_prompt(instruction)
    state.messages.append({"role": "user", "content": query})
    state.add_log(f"Selection query sent to workspace ({len(clean)} chars).")

    ui_mod = state.get_ui_module()
    if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):
        ui_mod.render_chat.refresh()
    schedule_scroll_chat()

    ext = (registry.active_extension or "").strip()
    if ext in ("document_editor", "web_viewer", "email_assistant"):
        from extensions.viewer_runtime.highlight_runner import start_viewer_highlight_workflow

        viewer_id = "document_editor" if ext == "document_editor" else ext
        start_viewer_highlight_workflow(query, instruction, viewer_id=viewer_id)
        return

    from pipeline.workflow import start_loma_workflow

    threading.Thread(target=start_loma_workflow, args=(query,), daemon=True).start()
