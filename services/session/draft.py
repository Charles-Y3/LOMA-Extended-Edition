# core/draft_sync.py
# -*- coding: utf-8 -*-
"""Canonical draft content shared between workspace chat and Preview panel."""
from __future__ import annotations

PREVIEW_FOOTER = (
    "\n\n---\n"
    "📄 **Preview** — edit on the right; **Download** saves your latest edits as a file."
)

GENERATION_SAVED_FOOTER = (
    "\n\n---\n"
    "✅ **Saved to data/generated/** — revise in **Preview**, then **Download** to export edits."
)

STATUS_PHRASES = (
    "draft in preview workspace",
    "draft in **preview**",
    "mission accomplished",
    "file operational complete",
    "multi-step draft ready",
    "edit on the right, then click **export**",
)


def is_boilerplate_message(text: str) -> bool:
    if not text or len(text.strip()) > 400:
        return False
    lower = text.lower()
    return any(p in lower for p in STATUS_PHRASES)


def _as_text(value) -> str:
    """Coerce draft/event payloads to plain text (NiceGUI sends GenericEventArguments)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    args = getattr(value, "args", None)
    if isinstance(args, str):
        return args
    if args is not None and not isinstance(args, (dict, list)):
        return str(args)
    return ""


def set_draft(content: str, output_type: str = "document", mode=None) -> None:
    from services.session import state

    text = _as_text(content).strip()
    state.draft_content = text
    state.live_workspace_plain = text
    state.live_workspace_output_type = output_type or "document"
    state.live_workspace_mode = mode


def get_draft() -> str:
    from services.session import state

    return _as_text(state.draft_content or state.live_workspace_plain).strip()


def strip_export_content(text: str) -> str:
    """Remove preview/chat footers before compiling to a file."""
    body = (text or "").strip()
    for footer in (PREVIEW_FOOTER, GENERATION_SAVED_FOOTER):
        if footer.strip() in body:
            body = body.split(footer.strip())[0].strip()
    if "\n\n---\n" in body:
        tail = body.rsplit("\n\n---\n", 1)[-1].strip()
        if tail.startswith("✅ **Saved") or tail.startswith("📄 **Preview"):
            body = body.rsplit("\n\n---\n", 1)[0].strip()
    return body


def get_draft_for_export() -> str:
    """Prefer Preview draft; fall back to last assistant chat body so web/link
    answers that streamed to chat (but never synced into Preview) still export."""
    from services.session import state

    body = get_draft()
    if not body:
        body = best_draft_from_messages(state.messages or [])
    return strip_export_content(body)


_ARTIFACT_PREVIEW_TYPES = frozenset(
    {"document", "presentation", "software", "image", "video"}
)


def is_artifact_preview_output(output_type: str | None) -> bool:
    return (output_type or "").strip().lower() in _ARTIFACT_PREVIEW_TYPES


def mirror_streaming_draft_to_preview(sink) -> None:
    """Keep Preview draft in sync with streaming assistant content (non-chat outputs)."""
    from services.session import state

    out = (
        state.pending_output_type
        or state.live_workspace_output_type
        or ""
    ).strip().lower()
    if not is_artifact_preview_output(out) or out == "chat":
        return
    if not state.messages or state.messages[-1].get("role") != "assistant":
        return
    content = strip_export_content(state.messages[-1].get("content") or "")
    if not content:
        return
    state.draft_content = content
    state.live_workspace_plain = content
    state.live_workspace_output_type = out
    sink.sync_preview_throttled()
    try:
        from ui.themes import registry

        if registry.preview_editor is None and registry.preview_markup_holder is None:
            sink.refresh_preview_panel_throttled()
    except Exception:
        pass


def sync_editor_to_state() -> None:
    """Read the Preview textarea into canonical draft state."""
    from services.session import state
    from ui.themes import registry

    editor = registry.preview_editor
    if editor is None:
        return
    try:
        val = (editor.value or "").strip()
    except Exception:
        return
    if val != state.draft_content:
        state.preview_dirty = True
    state.live_workspace_plain = val
    state.draft_content = val


def append_preview_footer(chat_body: str) -> str:
    body = (chat_body or "").strip()
    if PREVIEW_FOOTER.strip() in body:
        return body
    return body + PREVIEW_FOOTER


def get_last_user_instruction(messages: list, stored: str = "") -> str:
    from services.session.prompt_memory import _prompt_from_stored

    prompt = _prompt_from_stored(stored)
    if prompt:
        return prompt
    for m in reversed(messages):
        if m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def best_draft_from_messages(messages: list) -> str:
    """Prefer stored draft; else last substantial assistant message."""
    draft = get_draft()
    if draft:
        return draft
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = (msg.get("content") or "").strip()
        if content and not is_boilerplate_message(content):
            return strip_export_content(content)
    return ""


def reset_preview_workspace() -> None:
    """Clear Preview panel state, artifacts, and in-flight mutation progress."""
    from services.session import state
    from ui.themes import registry

    state.draft_content = ""
    state.live_workspace_html = ""
    state.live_workspace_plain = ""
    state.live_workspace_output_type = "chat"
    state.live_workspace_mode = None
    state.pending_output_type = None
    state.mutation_map = None
    state.mutation_span_map = None
    state.last_user_instruction = ""
    state.mutation_in_progress = False
    state.mutation_slide_current = 0
    state.mutation_slide_total = 0
    state.mutation_work_path = ""
    state.artifact_ready = False
    state.last_generated_file_path = None
    state.last_image_diffusion_prompt = ""
    state.last_image_user_query = ""
    state.last_image_generation_meta = {}
    state.preview_dirty = False
    state.preview_selection = ""
    state.preview_sel_start = -1
    state.preview_sel_end = -1
    state.preview_view_mode = "viewer"

    editor = registry.preview_editor
    if editor is not None:
        try:
            editor.value = ""
            editor.update()
        except Exception:
            registry.preview_editor = None
