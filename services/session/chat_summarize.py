# -*- coding: utf-8 -*-
"""LLM summary of workspace chat and sources."""
from __future__ import annotations

from pipeline.i18n import get_locale, language_system_rule, t as tr
from services.session import state

_MSG_CHAR_CAP = 6000
_DRAFT_CHAR_CAP = 5000
_LOG_LINES = 20


def _summary_system_prompt() -> str:
    sections = "\n".join(
        [
            f"## {tr('summarize.section_overview')}",
            f"## {tr('summarize.section_key_points')}",
            f"## {tr('summarize.section_sources')}",
            f"## {tr('summarize.section_outputs')}",
            f"## {tr('summarize.section_next_steps')}",
        ]
    )
    return tr(
        "summarize.prompt_role",
        lang_rule=language_system_rule(get_locale()),
        sections=sections,
    )


def _role_label(role: str) -> str:
    r = (role or "").strip().lower()
    if r == "user":
        return tr("summarize.role_user")
    if r == "assistant":
        return "LOMA"
    return r.upper() or tr("summarize.role_unknown")


def _truncate(text: str, cap: int) -> str:
    body = (text or "").strip()
    if len(body) <= cap:
        return body
    head = (cap * 2) // 3
    tail = cap // 3
    omitted = len(body) - head - tail
    return (
        f"{body[:head]}\n\n{tr('summarize.chars_omitted', n=omitted)}\n\n{body[-tail:]}"
    )


def _format_transcript() -> str:
    lines: list[str] = []
    turn = 0
    for msg in state.messages or []:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role") or ""
        content = (msg.get("content") or "").strip()
        thinking = (msg.get("thinking") or "").strip()
        if role == "assistant" and msg.get("processing") and not content:
            continue
        if not content and not thinking:
            continue
        turn += 1
        block_parts = [f"### {tr('summarize.turn', n=turn, role=_role_label(role))}"]
        if content:
            block_parts.append(_truncate(content, _MSG_CHAR_CAP))
        if thinking:
            block_parts.append(f"{tr('summarize.reasoning')}\n{_truncate(thinking, 1500)}")
        lines.append("\n\n".join(block_parts))
    return "\n\n".join(lines)


def _format_supplemental() -> str:
    parts: list[str] = []
    draft = (getattr(state, "draft_content", None) or "").strip()
    chat_tail = ""
    for msg in reversed(state.messages or []):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            chat_tail = (msg.get("content") or "").strip()
            break
    if draft and (not chat_tail or draft[:400] not in chat_tail):
        parts.append(
            tr("summarize.preview_draft", body=_truncate(draft, _DRAFT_CHAR_CAP))
        )
    logs = [str(x).strip() for x in (state.orchestra_log or []) if str(x).strip()]
    if logs:
        tail = logs[-_LOG_LINES:]
        parts.append(
            tr("summarize.recent_log", lines="\n".join(f"- {line}" for line in tail))
        )
    return "\n\n".join(parts)


def _format_sources_block() -> str:
    # session_sources_log (not active_context_files/active_web_links) — the live
    # basket is deliberately cleared after every turn (workflow_control.end_workflow)
    # so an old upload doesn't leak into a later, unrelated request; the log below is
    # the only record that survives across the whole session for this summary to read.
    files = [
        e.get("name", "").strip()
        for e in (state.session_sources_log or [])
        if isinstance(e, dict) and e.get("kind") == "file" and e.get("name")
    ]
    links = [
        e.get("name", "").strip()
        for e in (state.session_sources_log or [])
        if isinstance(e, dict) and e.get("kind") == "link" and e.get("name")
    ]
    artifact = (state.last_generated_file_path or "").strip()
    parts: list[str] = []
    if files:
        parts.append(tr("summarize.uploaded_files", files=", ".join(files)))
    if links:
        parts.append(tr("summarize.web_links", links=", ".join(links)))
    if artifact:
        parts.append(tr("summarize.last_artifact", path=artifact))
    if not parts:
        return tr("summarize.no_sources")
    return "\n".join(parts)


def summarize_workspace_chat() -> str:
    """Return markdown summary of chat + sources (blocking LLM call)."""
    transcript = _format_transcript()
    if not transcript:
        return tr("summarize.empty")

    from pipeline.base import profile_pack as profile_manager
    from services.model_router import resolve_general_model
    from services.resource_governor import ResourceGovernor
    from services import llm_bridge as chat_client
    from services.llm_bridge import build_chat_request

    profile_id = profile_manager.resolve_active_profile_id(
        (state.current_settings or {}).get("active_profile")
    )
    prof = profile_manager.load_profile(profile_id) or {}
    model = resolve_general_model(prof)
    sources_block = _format_sources_block()
    supplemental = _format_supplemental()

    user_body = (
        f"{tr('summarize.block_sources')}\n{sources_block}\n\n"
        f"{tr('summarize.block_transcript')}\n{transcript}"
    )
    if supplemental:
        user_body += f"\n\n{tr('summarize.block_supplemental')}\n{supplemental}"

    chat_kwargs, _ = build_chat_request(
        prof,
        model=model,
        messages=[
            {"role": "system", "content": _summary_system_prompt()},
            {"role": "user", "content": user_body},
        ],
        stream=False,
        extra_options={"temperature": 0.2},
    )
    with ResourceGovernor.acquire("llm_chat"):
        response = chat_client.chat(**chat_kwargs)
    summary = (response.get("message", {}).get("content") or "").strip()
    return summary or tr("summarize.failed")
