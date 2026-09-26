# -*- coding: utf-8 -*-
"""Workspace event handlers (upload, send, reboot)."""
import asyncio
import os
import threading

from nicegui import ui

from services.source_parser import attach_uploaded_file
from pipeline.i18n import t as tr
from pipeline.workflow import start_loma_workflow
from pipeline.base import profile_pack as profile_service
from services.session import settings as session_settings
from services.session import state
from pipeline.i18n import t as _tr  # noqa: E402

UPLOAD_DIR = os.path.join("data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

active_upload_count = 0
upload_lock = threading.Lock()


def _resolve_attached_vision_model() -> str:
    """Installed tag for settings default vision model."""
    preferred = (state.current_settings or {}).get("default_vision_model", "").strip()
    if not preferred:
        from services.model_router import resolve_vision_model

        return resolve_vision_model() or ""
    import config
    from services.model_assignments import _installed_match

    installed = config.get_installed_models()
    if installed:
        return _installed_match(preferred, installed) or preferred
    return preferred


def _prewarm_vision_model_async() -> None:
    """Load vision model into VRAM while user types their question."""
    try:
        from services.startup_warmup import prewarm_chat_model

        model = _resolve_attached_vision_model()
        if model:
            prewarm_chat_model(model)
    except Exception:
        pass


def update_role_in_memory(role, new_model_args):
    state.update_role_in_memory(role, new_model_args)




def send_message(chat_input) -> None:
    val = (chat_input.value or "").strip()
    if not val:
        return
    if not submit_user_text(val):
        return
    chat_input.value = ""


def submit_user_text(val: str) -> bool:
    """Core message-submission logic, independent of any input widget —
    shared by send_message() (the chat box) and retry_style_picker_request()
    (see its docstring). Returns False when the message was not accepted
    (workflow already busy, engine not ready)."""
    if state.workflow_active:
        return False
    from services.inference.readiness import inference_ready

    if not inference_ready():
        ui.notify(tr("startup.loading"), type="info")
        return False

    low = val.lower()

    from services.session.prompt_memory import remember_typed_prompt

    # Preview selection active → dedicated ask/revise paths that bypass generic routing and
    # plan mode entirely. Highlight + question → chat (ask); highlight + edit → mutation
    # (revise, always direct), regardless of how the preview was originally produced.
    from services.session import draft as draft_sync
    from services.session.preview_selection import selection_valid_in_draft
    from services.session.preview_revision import (
        is_selection_edit_request,
        start_preview_revision,
        submit_preview_ask,
    )

    preview_sel = (state.preview_selection or "").strip()
    if preview_sel:
        draft_sync.sync_editor_to_state()
        draft_text = draft_sync.get_draft_for_export()
        if draft_text and selection_valid_in_draft(draft_text, preview_sel):
            if is_selection_edit_request(val):
                remember_typed_prompt(val)
                start_preview_revision(val)
            else:
                submit_preview_ask(val)
            return True

    from services.model_router import resolve_vision_model
    from services.capability.gap_handler import offer_vision_installer

    has_image_context = any(f.get("type") == "image" for f in state.active_context_files)
    has_media_context = any(
        f.get("type") in ("media_audio", "media_video") for f in state.active_context_files
    )

    remember_typed_prompt(val)
    state.messages.append({"role": "user", "content": val})
    state.messages.append(
        {"role": "assistant", "content": "", "processing": True, "thinking": ""}
    )
    try:
        from services.voice_reply import start_stream_reply

        start_stream_reply(state.current_settings)
    except Exception:
        pass
    ui_module = state.get_ui_module()
    ui_module.render_chat.refresh()
    from ui.themes.assets import schedule_scroll_chat

    schedule_scroll_chat()
    threading.Thread(
        target=_preflight_and_start_workflow,
        args=(val, has_image_context, has_media_context),
        daemon=True,
    ).start()
    return True


def _preflight_and_start_workflow(
    val: str, has_image_context: bool, has_media_context: bool
) -> None:
    """Run vision/media checks off the UI thread so Enter shows the user message immediately."""
    from services.model_router import resolve_vision_model
    from ui.themes.assets import schedule_scroll_chat

    ui_module = state.get_ui_module()

    if has_image_context:
        vision_model = _resolve_attached_vision_model()
        if not vision_model:
            from services.capability.gap_handler import offer_vision_installer

            def _resume() -> None:
                threading.Thread(target=start_loma_workflow, args=(val,), daemon=True).start()

            offer_vision_installer(_resume)
            if state.messages and state.messages[-1].get("role") == "assistant":
                state.messages[-1]["content"] = (
                    _tr("chat.vision_missing")
                )
                state.messages[-1].pop("processing", None)
            ui_module.render_chat.refresh()
            schedule_scroll_chat()
            return

    if has_media_context:
        from pipeline.base import profile_pack as profile_manager
        from services.model_router import check_model_supports_audio, resolve_general_model
        from services.media_transcription import transcription_deps_available

        profile_id = profile_manager.resolve_active_profile_id(
            state.current_settings.get("active_profile")
        )
        prof = profile_manager.load_profile(profile_id) or {}
        model = resolve_general_model(prof)
        if not check_model_supports_audio(model):
            ok, _ = transcription_deps_available()
            if not ok:
                from services.capability.gap_handler import offer_transcription_installer

                def _resume_media() -> None:
                    threading.Thread(target=start_loma_workflow, args=(val,), daemon=True).start()

                offer_transcription_installer(_resume_media)
                if state.messages and state.messages[-1].get("role") == "assistant":
                    state.messages[-1]["content"] = (
                        "Audio/video is attached but local transcription is not installed. "
                        "Click **Install faster-whisper** in the dialog (or use an audio-capable model "
                        "such as Gemma in Settings). LOMA will retry when ready."
                    )
                    state.messages[-1].pop("processing", None)
                ui_module.render_chat.refresh()
                schedule_scroll_chat()
                return

    from services.model_assignments import has_usable_chat_model
    from pipeline.i18n import t as tr

    if not has_usable_chat_model():
        from services.capability.gap_handler import offer_chat_model_installer

        def _resume_chat() -> None:
            threading.Thread(target=start_loma_workflow, args=(val,), daemon=True).start()

        offer_chat_model_installer(_resume_chat)
        if state.messages and state.messages[-1].get("role") == "assistant":
            state.messages[-1]["content"] = tr("chat.no_models_installed")
            state.messages[-1].pop("processing", None)
        ui_module.render_chat.refresh()
        schedule_scroll_chat()
        return

    threading.Thread(target=start_loma_workflow, args=(val,), daemon=True).start()


def handle_chat_action(chat_input) -> None:
    """Send message or stop an in-flight workflow."""
    from services.session.workflow_control import request_cancel

    if state.workflow_active:
        request_cancel()
        from pipeline.i18n import t as tr

        ui.notify(tr("chat.stopping"), color="warning")
        return
    send_message(chat_input)


def on_chat_input_change(_=None) -> None:
    from services.session.workflow_control import reset_progress_if_idle

    reset_progress_if_idle()


async def _store_and_attach_file(file_name: str, content_bytes: bytes | None) -> None:
    if content_bytes is None:
        state.add_log(f"Upload Error: Empty payload for {file_name}")
        ui.notify(_tr("notify.upload_unreadable"), color="negative")
        return
    filepath = os.path.join(UPLOAD_DIR, file_name)
    with open(filepath, "wb") as f:
        f.write(content_bytes if isinstance(content_bytes, bytes) else bytes(str(content_bytes), "utf-8"))
    state.add_log(f"File physically stored: {filepath}")
    parsed_data = await asyncio.to_thread(attach_uploaded_file, filepath, filename=file_name)
    if parsed_data and parsed_data.get("type") not in ["error", "unsupported"]:
        parsed_data["filename"] = file_name
        # Stamp with the current turn counter — request scoping (pipeline/workflow.py)
        # treats every file sharing today's stamp as "attached for this message" and
        # includes them all, regardless of how many; only files left over from an
        # earlier, already-completed turn need a name match to stay in scope.
        parsed_data["_attached_turn"] = state.context_attach_turn
        with upload_lock:
            if file_name not in [f.get("filename") for f in state.active_context_files]:
                if len(state.active_context_files) < 5:
                    state.active_context_files.append(parsed_data)
                    state.log_session_source("file", file_name)
                    state.add_log(f"Context successfully populated: {file_name}")
                    if parsed_data.get("type") in ("media_audio", "media_video"):
                        ui.notify(
                            _tr("notify.attached_media", name=file_name),
                            color="positive",
                        )
                    else:
                        ui.notify(_tr("notify.loaded_file", name=file_name), color="positive")
                    if parsed_data.get("type") == "image":
                        _prewarm_vision_model_async()
                else:
                    ui.notify(_tr("notify.capped", name=file_name), color="warning")
            else:
                ui.notify(_tr("notify.already_attached", name=file_name), color="warning")
    else:
        reason = parsed_data.get("content", "Unknown parsing error") if parsed_data else "Empty response"
        state.add_log(f"Failed parsing: {file_name} -> {reason}")
        from services.capability.gap_handler import handle_transcription_error_message

        if not handle_transcription_error_message(reason):
            ui.notify(_tr("notify.parse_failed", name=file_name), color="negative")


async def handle_file_upload(e) -> None:
    global active_upload_count
    try:
        with upload_lock:
            active_upload_count += 1
        for cap in state.LOMA_CAPABILITIES:
            if cap["name"] == "Document Parsing":
                cap["status"] = "processing"

        if hasattr(e, "file"):
            file_name = e.file.name
            content_bytes = await e.file.read()
            await _store_and_attach_file(file_name, content_bytes)
        elif hasattr(e, "content"):
            file_name = getattr(e, "name", getattr(e, "filename", "uploaded_file.txt"))
            content_bytes = e.content.read() if hasattr(e.content, "read") else e.content
            await _store_and_attach_file(file_name, content_bytes)
        elif hasattr(e, "files") and e.files:
            for entry in e.files:
                file_name = entry.get("name", "uploaded_file.txt")
                content_stream = entry.get("content") or entry
                if hasattr(content_stream, "read"):
                    content_bytes = content_stream.read()
                    if hasattr(content_stream, "seek"):
                        content_stream.seek(0)
                else:
                    content_bytes = content_stream
                await _store_and_attach_file(file_name, content_bytes)
        else:
            state.add_log("Upload Error: Event object structure unreadable")
            ui.notify(_tr("notify.upload_unreadable"), color="negative")
    except Exception as ex:
        state.add_log(f"Critical Exception inside Upload Handler: {str(ex)}")
        ui.notify(_tr("notify.upload_failed", error=str(ex)), color="negative")
    finally:
        with upload_lock:
            active_upload_count -= 1
            current_active = active_upload_count
        if current_active == 0:
            for cap in state.LOMA_CAPABILITIES:
                if cap["name"] == "Document Parsing":
                    cap["status"] = "success" if len(state.active_context_files) > 0 else "idle"


def reboot_workspace(render_chat_fn, render_progress_fn, render_sources_hub_fn) -> None:
    from pipeline.output_format import DEFAULT_OUTPUT_FORMAT
    from services.voice_reply import stop_voice_reply
    from ui.themes import registry

    stop_voice_reply()
    preserved_chat_font = state.current_settings.get("chat_font_px")

    state.messages.clear()
    state.messages.append({"role": "assistant", "content": tr("chat.reboot")})
    state.active_context_files.clear()
    state.active_web_links.clear()
    state.web_scrape_cache.clear()
    state.session_sources_log.clear()
    state.chart_artifacts = []

    for key in state.progress_state:
        state.progress_state[key] = "⚪"

    state.orchestra_log.clear()
    state.orchestra_log.append("System execution environments reset successfully.")
    from pipeline.base import profile_pack as profile_manager

    import config

    state.current_settings["active_profile"] = profile_manager.PROFILE_NONE
    state.current_settings["theme"] = state.current_settings.get("theme", config.THEME_DEFAULT)
    state.current_settings["default_output_format"] = DEFAULT_OUTPUT_FORMAT
    state.current_settings["execution_mode"] = "direct"
    state.current_settings["chat_execution_mode"] = "direct"
    if preserved_chat_font is not None:
        try:
            state.current_settings["chat_font_px"] = max(
                11, min(20, int(preserved_chat_font))
            )
        except (TypeError, ValueError):
            pass
    session_settings.save_settings(state.current_settings)

    from services.session import draft as draft_sync

    draft_sync.reset_preview_workspace()

    if registry.output_format_select is not None:
        try:
            registry.output_format_select.set_value(DEFAULT_OUTPUT_FORMAT)
        except Exception:
            pass
    render_chat_fn.refresh()
    render_progress_fn.refresh()
    render_sources_hub_fn.refresh()

    from ui.themes.assets import sync_profile_selector

    sync_profile_selector(force_select_id=state.current_settings.get("active_profile"))

    try:
        from ui.components.preview_workspace import refresh_preview_panel, update_preview_status_label
        from ui.layouts.output_panel import render_preview_tab

        update_preview_status_label("chat")
        render_preview_tab.refresh()
        refresh_preview_panel()
    except Exception:
        pass

    # #region agent log
    try:
        from pipeline.debug_session import debug_log

        debug_log(
            "handlers:reboot_workspace",
            "reboot complete",
            {
                "output_format": state.current_settings.get("default_output_format"),
                "draft_len": len(state.draft_content or ""),
                "live_type": state.live_workspace_output_type,
            },
            "reboot",
        )
    except Exception:
        pass
    # #endregion

    ui.notify(_tr("notify.workspace_rebooted"), color="info")
