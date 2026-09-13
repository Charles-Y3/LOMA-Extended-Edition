# -*- coding: utf-8 -*-
"""Roles for media_transcription capability."""
from __future__ import annotations

import re

from pipeline.roles.chat_roles import ChatRole

_ROLES: dict[str, ChatRole] = {
    "transcript_presenter": ChatRole(
        id="transcript_presenter",
        description="Present timestamped transcript text for chat or export.",
        system_prompt=(
            "Role: Transcript Presenter.\n"
            "Format the transcript clearly with timestamps and sections.\n"
            "Do not invent content not present in the source transcript."
        ),
        default_contract_id="transcript_markdown",
    ),
    "transcript_summarizer": ChatRole(
        id="transcript_summarizer",
        description="Summarize a transcript after local speech-to-text.",
        system_prompt=(
            "Role: Transcript Summarizer.\n"
            "Summarize the provided transcript into key points.\n"
            "Stay grounded in the transcript text only."
        ),
        default_contract_id="transcript_summary",
    ),
    "transcript_translator": ChatRole(
        id="transcript_translator",
        description="Translate transcript content per user request.",
        system_prompt=(
            "Role: Transcript Translator.\n"
            "Translate the transcript while preserving timestamps and section breaks where possible."
        ),
        default_contract_id="transcript_translation",
    ),
}


def classify_media_transcription_roles(user_query: str) -> list[str]:
    q = (user_query or "").lower()
    roles: list[str] = []
    if any(w in q for w in ("summarize", "summarise", "summary", "tldr", "brief")):
        roles.append("transcript_summarizer")
    needs_translation = any(
        w in q for w in ("translate", "translation", "localize", "localise")
    ) or bool(re.search(r"\boutput\s+(?:as|in)\b", q))
    if needs_translation:
        roles.append("transcript_translator")
    if not roles:
        return ["transcript_presenter"]
    return roles


def get_media_transcription_role(role_id: str) -> ChatRole:
    return _ROLES.get(role_id, _ROLES["transcript_presenter"])
