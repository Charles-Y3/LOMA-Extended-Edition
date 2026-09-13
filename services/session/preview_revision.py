# -*- coding: utf-8 -*-
"""Run Preview selection revision from the UI."""
from __future__ import annotations

import os
import re
import threading

# An imperative to change the highlighted text → revise (mutation). Anything else
# (a question / request for more info) → ask (chat). Default is ask.
# Verbs that REPLACE the highlighted text. "summarize/explain" are intentionally absent —
# for a selection they usually mean "tell me" (ask/chat); the user can say "replace with a
# summary" or "shorten this" for an in-place edit.
_EDIT_INTENT = re.compile(
    r"\b(revise|edit|change|rewrite|re-?word|rephrase|fix|correct|replace|translate|"
    r"localize|localise|update|improve|shorten|lengthen|expand|condense|simplify|"
    r"polish|adjust|convert|reformat|format|proofread|tidy|"
    r"capitali[sz]e|bold|make it|make this|turn (?:it|this) into|clean up)\b",
    re.I,
)


def is_selection_edit_request(query: str) -> bool:
    """True when a preview-selection query asks to change the text (vs. ask about it)."""
    return bool(_EDIT_INTENT.search(query or ""))

from pipeline.base.profile_pack import default_profile
from pipeline.base import profile_pack as profile_manager
from services.model_router import resolve_chat_model, resolve_general_model
from services.session import draft as draft_sync
from services.session import state
from services.session.artifact import save_preview_to_artifact
from services.session.preview_selection import clear_selection
from services.session.revision import revise_excerpt, splice_excerpt, explain_excerpt
from services.session.workflow_control import schedule_on_ui
from ui.themes.sink import NiceGUIStateSink


def _finish_preview_revision_ui(out_type: str) -> None:
    from ui.components.preview_workspace import sync_preview_editor, update_preview_status_label

    sync_preview_editor(refresh_panel=False, preserve_scroll=True)
    update_preview_status_label(out_type)


def run_preview_revision(instruction: str) -> None:
    sink = NiceGUIStateSink()
    instruction = (instruction or "").strip()
    draft_sync.sync_editor_to_state()
    draft = draft_sync.get_draft_for_export()
    selection = (state.preview_selection or "").strip()
    sel_start = getattr(state, "preview_sel_start", -1)
    sel_end = getattr(state, "preview_sel_end", -1)

    if not instruction:
        sink.log("Revision aborted — enter how to change the selection.")
        return
    if not selection or not draft:
        sink.log("Revision aborted — highlight text in Preview first.")
        return

    profile_id = state.current_settings.get("active_profile", "simple_assistant")
    try:
        prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
    except Exception:
        prof = default_profile(profile_id)

    out_type = state.live_workspace_output_type or "document"
    mode = state.live_workspace_mode or "generation"
    gen_model = resolve_general_model(prof)
    chat_model, vision_error = resolve_chat_model(prof, "text", [])
    model = chat_model or gen_model

    if vision_error:
        sink.log(f"Revision failed: {vision_error}")
        return

    original = "output"
    if state.active_context_files:
        original = state.active_context_files[0].get("filename", "output")

    sink.log(f"Revising selection ({len(selection):,} chars)…")
    sink.set_progress("Synthesis", "🟡")
    sink.refresh_progress()

    from pipeline.capability_runtime.execution_config import execution_mode_from_settings

    execution_mode = execution_mode_from_settings(state.current_settings)
    sink.log(f"Selection revision | execution mode: {execution_mode}")

    try:
        revised = revise_excerpt(
            selection,
            instruction,
            model,
            profile=prof,
            execution_mode=execution_mode,
            sink=sink,
        )
        new_draft = splice_excerpt(
            draft,
            selection,
            revised,
            start=sel_start if sel_start >= 0 else None,
            end=sel_end if sel_end >= 0 else None,
        )
        draft_sync.set_draft(new_draft, out_type, mode)
        state.preview_dirty = True

        result = save_preview_to_artifact(
            output_type=out_type,
            original_filename=original,
            gen_model=gen_model,
            mode=mode,
            log_fn=sink.log,
            use_editor=False,
        )

        clear_selection()
        if result:
            state.last_generated_file_path = result
            state.artifact_ready = True
            state.preview_dirty = False
            sink.log(f"Revision saved → data/generated/{os.path.basename(result)}")
            sink.notify_artifact_ready(result)
        else:
            sink.log("Selection revised in Preview — use Save/Download to write the file.")

        schedule_on_ui(lambda: _finish_preview_revision_ui(out_type))
    except Exception as ex:
        sink.log(f"Selection revision failed: {ex}")
        schedule_on_ui(lambda: _finish_preview_revision_ui(out_type))
    finally:
        sink.set_progress("Synthesis", "🟢")
        sink.refresh_progress()


def start_preview_revision(instruction: str) -> None:
    threading.Thread(target=run_preview_revision, args=(instruction,), daemon=True).start()


def _format_preview_ask_user_message(selection: str, question: str) -> str:
    excerpt_display = selection if len(selection) <= 400 else selection[:397] + "..."
    display_q = question if question else "What does this mean?"
    return (
        f"**Ask LOMA** (preview selection):\n\n"
        f"> {excerpt_display}\n\n"
        f"{display_q}"
    )


def _finish_preview_ask_ui(answer: str) -> None:
    if state.messages and state.messages[-1].get("role") == "assistant":
        state.messages[-1]["content"] = answer
    else:
        state.messages.append({"role": "assistant", "content": answer})

    sink = NiceGUIStateSink()
    sink.refresh_chat()
    sink.scroll_chat()


def run_preview_ask(question: str, selection: str) -> None:
    sink = NiceGUIStateSink()
    selection = (selection or "").strip()
    question = (question or "").strip()

    if not selection:
        sink.log("Ask LOMA aborted — highlight text in Preview first.")
        schedule_on_ui(
            lambda: _finish_preview_ask_ui("Highlight text in Preview first, then ask LOMA about it.")
        )
        return

    profile_id = state.current_settings.get("active_profile", "simple_assistant")
    try:
        prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
    except Exception:
        prof = default_profile(profile_id)

    gen_model = resolve_general_model(prof)
    chat_model, vision_error = resolve_chat_model(prof, "text", [])
    model = chat_model or gen_model

    if vision_error:
        sink.log(f"Ask LOMA failed: {vision_error}")
        schedule_on_ui(lambda: _finish_preview_ask_ui(vision_error))
        return

    sink.log(f"Asking LOMA about selection ({len(selection):,} chars)…")

    try:
        answer = explain_excerpt(selection, question, model, profile=prof)
        schedule_on_ui(lambda: _finish_preview_ask_ui(answer))
    except Exception as ex:
        sink.log(f"Ask LOMA failed: {ex}")
        schedule_on_ui(lambda: _finish_preview_ask_ui(f"Could not answer about the selection: {ex}"))


def submit_preview_ask(question: str) -> None:
    """Append chat messages on the UI thread, then answer in the background."""
    from ui.themes.assets import schedule_scroll_chat

    selection = (state.preview_selection or "").strip()
    if not selection:
        from nicegui import ui

        ui.notify("Highlight text in Preview first.", color="warning")
        return
    if state.workflow_active:
        from nicegui import ui

        ui.notify("Wait until the current run finishes.", color="warning")
        return

    question = (question or "").strip()
    state.messages.append(
        {"role": "user", "content": _format_preview_ask_user_message(selection, question)}
    )
    state.messages.append({"role": "assistant", "content": ""})

    ui_mod = state.get_ui_module()
    if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):
        ui_mod.render_chat.refresh()
    schedule_scroll_chat()

    threading.Thread(target=run_preview_ask, args=(question, selection), daemon=True).start()
