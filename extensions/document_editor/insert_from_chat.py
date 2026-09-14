# -*- coding: utf-8 -*-
"""Insert an Ask LOMA chat answer into the open Document Editor document.

Replaces the old "Revise" flow (highlight -> generate -> replace in place) with
an additive one (highlight -> generate in chat -> insert as a new line below the
highlight) — a bad or unwanted answer never destructively overwrites the user's
original text; it just sits there to be deleted manually. This module only
knows how to splice text into the currently open document; deciding whether a
given chat message is eligible for these actions lives in ui/components/chat_message.py
(it needs the preceding user message to recover which excerpt triggered the
query — see pipeline/direct/highlight_excerpt.py)."""
from __future__ import annotations

import re
import threading
from typing import Callable

from pipeline.i18n import t as tr


_LEADING_MODE_HEADER_RE = re.compile(r"^\*\*[^\n*]+\*\*[ \t]*\([^\n]*\n\n+")


def strip_citations_for_insert(content: str) -> str:
    """Drop the leading mode-label header (e.g. "**Knowledge Vault** (Analyse —
    broader retrieval...)"), the trailing Sources block, and inline [n] markers
    from a Knowledge Vault chat answer, leaving plain prose fit for splicing into
    a document. The header is stripped structurally (a bold line followed by a
    blank line) rather than by matching each mode's exact translated text, since
    Ask/Analyze/Deep/Agentic/Search each use their own header string but all
    share that same shape: the bold brand name is IMMEDIATELY followed by a
    " (mode qualifier)" parenthetical OUTSIDE the bold span. That extra
    "immediately followed by an opening paren" requirement matters: without it,
    this regex also matched a real answer paragraph that happens to open with a
    bolded term whose OWN parenthetical sits INSIDE the bold, like
    "**快樂 (Joy)**: A fleeting..." — silently eating that entire first paragraph
    (confirmed: "give examples..." reply's first term-definition paragraph
    vanished on insert). A term followed by ": " never matches "**Term** (",
    so requiring the paren right outside the bold keeps real headers matching
    while leaving that content paragraph alone."""
    text = (content or "").strip()
    text = _LEADING_MODE_HEADER_RE.sub("", text, count=1).strip()
    for key in ("knowledge_vault.sources_header", "knowledge_vault.sources_header_uncited"):
        header = tr(key)
        if not header:
            continue
        idx = text.find(header)
        if idx != -1:
            text = text[:idx].strip()
            break
    text = re.sub(r"\[\d+\]", "", text)
    # Citation markers often sit right before punctuation ("... fate [1]。") —
    # removing just the marker leaves a stray space there.
    text = re.sub(r"[ \t]+([。，、,.!?！？])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def insert_allowed() -> bool:
    """Same gate the old Revise button used — a .docx open in edit mode."""
    from extensions.document_editor.extension import _doc_state

    path = (_doc_state.get("filepath") or "").strip()
    return bool(_doc_state.get("edit_mode") and path.lower().endswith(".docx"))


def _current_body(doc_state: dict) -> str:
    editor = doc_state.get("editor")
    val = getattr(editor, "value", None)
    if isinstance(val, str) and val:
        return val
    return (doc_state.get("parsed") or {}).get("content") or ""


def insert_text_below_excerpt(excerpt: str, insert_text: str) -> bool:
    """Splice `insert_text` as a new paragraph directly below `excerpt` in the
    currently open document. Returns False if the excerpt can no longer be
    found (document changed since the chat answer was generated, or the wrong
    document is now open), there's nothing to insert, or the document is no
    longer open in edit mode — re-checked here via insert_allowed() rather than
    trusting the caller's button to have gated correctly, since the chat panel's
    insert buttons are computed once per chat render and don't disappear the
    instant the user flips Document Editor to View mode; a stale button click
    must still be refused rather than silently mutating a view-mode document."""
    from extensions.document_editor.extension import _doc_state, _push_undo_snapshot
    from extensions.document_editor.persist import persist_docx_state

    excerpt = (excerpt or "").strip()
    insert_text = (insert_text or "").strip()
    if not excerpt or not insert_text:
        return False

    if not insert_allowed():
        return False

    path = (_doc_state.get("filepath") or "").strip()

    body = _current_body(_doc_state)
    if excerpt not in body:
        return False

    try:
        _push_undo_snapshot()
    except Exception:
        pass

    new_body = body.replace(excerpt, f"{excerpt}\n\n{insert_text}", 1)
    parsed = _doc_state.get("parsed") or {}
    parsed["content"] = new_body
    _doc_state["parsed"] = parsed
    editor = _doc_state.get("editor")
    if editor is not None:
        editor.value = new_body
        editor.update()
    persist_docx_state(path, parsed, new_body)
    return True


def start_insert_summary(
    excerpt: str,
    content: str,
    *,
    on_complete: Callable[[bool], None] | None = None,
) -> None:
    """Condense `content` (already citation-stripped) with one quick LLM call,
    then insert the result below the excerpt. Runs off the UI thread since it
    makes a model call; `on_complete` is scheduled back onto the UI thread.

    Wraps the run in the same begin_workflow()/end_workflow() pair every other
    chat action uses — this flips the shared send button to the red stop icon
    and shows "Preparing output…" above the chat for the duration, and the LLM
    call itself goes through run_cancellable() so clicking that stop button
    actually aborts the wait instead of being purely cosmetic."""
    from services.session.workflow_control import begin_workflow
    from ui.components.ux_guidance import set_progress_detail

    begin_workflow()
    set_progress_detail("Synthesis")

    def _run() -> None:
        from pipeline.base import profile_pack as profile_manager
        from pipeline.base.profile_pack import default_profile
        from pipeline.capability_runtime.chat_runner import generate_text_sync
        from services.model_router import resolve_chat_model, resolve_general_model
        from services.session import state as loma_state
        from services.session.workflow_control import (
            WorkflowCancelled,
            end_workflow,
            run_cancellable,
            schedule_on_ui,
        )
        from ui.components.ux_guidance import set_progress_detail

        ok = False
        try:
            profile_id = loma_state.current_settings.get("active_profile", "simple_assistant")
            prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
            gen_model = resolve_general_model(prof)
            chat_model, vision_error = resolve_chat_model(prof, "text", [])
            model = chat_model or gen_model
            if vision_error or not (content or "").strip():
                return
            prompt = (
                "Summarize the following text as a concise note — a few sentences "
                "capturing the key point, in the same language as the text. Output "
                "ONLY the summary, no labels or commentary.\n\n" + content
            )
            try:
                summary = run_cancellable(
                    generate_text_sync, prof, model, [{"role": "user", "content": prompt}]
                )
            except WorkflowCancelled:
                return
            summary = (summary or "").strip()
            if summary:
                ok = insert_text_below_excerpt(excerpt, summary)
        finally:
            set_progress_detail(None)
            end_workflow()
            if on_complete:
                schedule_on_ui(lambda: on_complete(ok))

    threading.Thread(target=_run, daemon=True).start()
