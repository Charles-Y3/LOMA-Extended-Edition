# -*- coding: utf-8 -*-
"""Highlight → Revise for document viewer (edit mode, .docx only)."""
from __future__ import annotations

import os
import threading
from typing import Any, Callable

from extensions.document_editor.persist import persist_docx_state
from pipeline.base import profile_pack as profile_manager
from pipeline.base.profile_pack import default_profile
from services.model_router import resolve_chat_model, resolve_general_model
from services.session.revision import revise_excerpt, splice_excerpt
from services.session.workflow_control import schedule_on_ui
from ui.themes.sink import NiceGUIStateSink


def run_document_revision(
    state: dict[str, Any],
    selection: str,
    instruction: str,
    *,
    on_complete: Callable[[], None] | None = None,
) -> None:
    sink = NiceGUIStateSink()
    instruction = (instruction or "").strip()
    selection = (selection or "").strip()
    path = (state.get("filepath") or "").strip()
    parsed = state.get("parsed") or {}

    if not path.lower().endswith(".docx"):
        sink.log("Revision only supports .docx in edit mode.")
        return
    if not instruction or not selection:
        sink.log("Revision aborted — need selection and instruction.")
        return

    editor = state.get("editor")
    body = (getattr(editor, "value", None) or parsed.get("content") or "").strip()
    if selection not in body:
        sink.log("Selection not found in current document text.")
        return

    profile_id = (state.get("settings_profile") or "simple_assistant")
    try:
        from services.session import state as loma_state

        profile_id = loma_state.current_settings.get("active_profile", profile_id)
    except Exception:
        pass
    prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
    gen_model = resolve_general_model(prof)
    chat_model, vision_error = resolve_chat_model(prof, "text", [])
    model = chat_model or gen_model
    if vision_error:
        sink.log(f"Revision failed: {vision_error}")
        return

    from pipeline.capability_runtime.execution_config import execution_mode_from_settings
    from services.session import state as loma_state

    execution_mode = execution_mode_from_settings(loma_state.current_settings)
    sink.log(f"Revising selection ({len(selection):,} chars)…")

    try:
        from extensions.document_editor.extension import _push_undo_snapshot

        _push_undo_snapshot()
    except Exception:
        pass
    try:
        revised = revise_excerpt(
            selection,
            instruction,
            model,
            profile=prof,
            execution_mode=execution_mode,
            sink=sink,
        )
        new_body = splice_excerpt(body, selection, revised)
        parsed["content"] = new_body
        if editor is not None:
            editor.value = new_body
            editor.update()
        persist_docx_state(path, parsed, new_body)
        sink.log(f"Revision saved → {os.path.basename(path)}")
        if on_complete:
            schedule_on_ui(on_complete)
    except Exception as exc:
        sink.log(f"Document revision failed: {exc}")
        if on_complete:
            schedule_on_ui(on_complete)


def start_document_revision(
    state: dict[str, Any],
    selection: str,
    instruction: str,
    *,
    on_complete: Callable[[], None] | None = None,
) -> None:
    threading.Thread(
        target=run_document_revision,
        args=(state, selection, instruction),
        kwargs={"on_complete": on_complete},
        daemon=True,
    ).start()
