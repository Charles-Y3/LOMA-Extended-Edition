# -*- coding: utf-8 -*-
"""Reset workspace session to startup-like state."""
from __future__ import annotations

from pipeline.i18n import t as tr
from services.session import state


def clear_chat_history() -> None:
    """Reset chat messages only (e.g. browser refresh)."""
    state.messages = [{"role": "assistant", "content": tr("chat.online"), "bootstrap": True}]
    state.last_user_instruction = ""


def reset_workspace_session(*, execution_mode: str = "direct") -> None:
    """Clear chat, sources, workflow pauses, preview — default Direct flow."""
    state.messages = [{"role": "assistant", "content": tr("chat.online"), "bootstrap": True}]
    state.active_context_files = []
    state.active_web_links = []
    state.web_scrape_cache = {}
    state.context_bundle_cache_key = None
    state.context_parsed_sources_cache = None
    state.context_images_cache = []
    state.context_source_digests_cache = None
    state.context_media_blocks_cache = []
    state.chart_artifacts = []
    # chart paths cleared so reboot chat does not show stale figures
    state.orchestra_log = [tr("console.session_reset"), tr("console.awaiting_input")]
    state.progress_state = {
        "Intent": "⚪",
        "Planner": "⚪",
        "Execution": "⚪",
        "Synthesis": "⚪",
    }
    state.last_generated_file_path = None
    state.artifact_ready = False
    state.draft_content = ""
    state.live_workspace_html = ""
    state.live_workspace_plain = ""
    state.preview_dirty = False
    state.preview_selection = ""
    state.preview_sel_start = -1
    state.preview_sel_end = -1
    state.mutation_in_progress = False
    state.mutation_map = None
    state.workflow_active = False
    state.workflow_cancel_requested = False
    state.pending_human_choice = None
    state.pending_extension_confirm = None
    state.pending_plan_review = None
    state.pending_delivery_review = None
    state.agentic_log_dir = None
    state.last_user_instruction = ""
    state.active_workflow_instruction = ""

    settings = state.current_settings or {}
    settings["execution_mode"] = execution_mode
    settings["chat_execution_mode"] = execution_mode
    from services.session import settings as session_settings

    session_settings.save_settings(settings, quiet=True)

    try:
        from ui.layouts.workspace_panel import sync_execution_mode_select

        sync_execution_mode_select()
    except Exception:
        pass

    try:
        from ui.components.preview_workspace import refresh_preview_panel

        refresh_preview_panel()
    except Exception:
        pass

    try:
        from ui.components.attachments_hub import render_sources_hub

        render_sources_hub.refresh()
    except Exception:
        pass

    try:
        from ui.layouts.output_panel import render_logs

        render_logs.refresh()
    except Exception:
        pass
