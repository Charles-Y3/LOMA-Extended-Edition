# -*- coding: utf-8 -*-
"""Build InputMetadata from session state before heavy I/O."""
from __future__ import annotations

from pipeline.context_builder import sources_need_retrieval, estimate_source_chars
from pipeline.schemas.task_schema import InputMetadata, QuerySource
from services.session import draft as draft_sync
from services.session import prompt_memory
from services.session.preview_selection import selection_valid_in_draft


def build_input_metadata(state) -> InputMetadata:
    query = (prompt_memory.last_typed_prompt() or "").strip()
    if not query:
        query = (state.last_user_instruction or "").strip()

    files_meta: list[dict] = []
    has_image = False
    has_docs = False
    for f in state.active_context_files or []:
        if not isinstance(f, dict):
            continue
        ftype = f.get("type") or "unknown"
        source_kind = f.get("source_kind") or ftype
        name = f.get("filename") or f.get("name") or "file"
        if ftype == "image" or source_kind == "image":
            has_image = True
        elif ftype in ("text", "media_audio", "media_video") or source_kind in (
            "document",
            "presentation",
            "spreadsheet",
            "text",
            "web",
            "audio",
            "video",
        ):
            has_docs = True
        files_meta.append({"name": name, "type": source_kind if f.get("source_kind") else ftype})

    links = list(state.active_web_links or [])
    messages = state.messages or []
    # Query is intentionally the raw typed prompt; excerpt wrappers exist only in the
    # model-facing user message content.
    has_excerpt = any(
        (f.get("type") == "text" and str(f.get("filename", "")).lower().startswith("excerpt"))
        for f in (state.active_context_files or [])
        if isinstance(f, dict)
    )

    query_source: QuerySource = "workspace"
    if (state.preview_selection or "").strip() and has_excerpt:
        query_source = "highlight"
    elif (state.preview_selection or "").strip():
        query_source = "revision"

    preferred = (state.current_settings or {}).get("default_output_format", "chat")

    preview_sel = (state.preview_selection or "").strip()
    draft_text = draft_sync.get_draft_for_export() if preview_sel else ""
    has_valid_preview_selection = bool(
        preview_sel and draft_text and selection_valid_in_draft(draft_text, preview_sel)
    )

    total_source_chars = estimate_source_chars(state.active_context_files or [])
    needs_retrieval = sources_need_retrieval(state.active_context_files or []) or bool(links)

    from pipeline.base import profile_pack as profile_manager

    profile_id = profile_manager.resolve_active_profile_id(
        (state.current_settings or {}).get("active_profile")
    )
    if (state.current_settings or {}).get("active_profile") != profile_id:
        state.current_settings["active_profile"] = profile_id

    return InputMetadata(
        query=query,
        profile_id=profile_id,
        preferred_output_format=preferred,
        file_count=len(state.active_context_files or []),
        link_count=len(links),
        message_count=len(messages),
        files=files_meta,
        links=links,
        has_image=has_image,
        has_docs=has_docs,
        has_excerpt=has_excerpt,
        has_preview_selection=bool(preview_sel),
        has_valid_preview_selection=has_valid_preview_selection,
        query_source=query_source,
        total_source_chars=total_source_chars,
        needs_context_retrieval=needs_retrieval,
    )
