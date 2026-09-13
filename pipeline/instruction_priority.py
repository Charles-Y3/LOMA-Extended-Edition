# -*- coding: utf-8 -*-
"""Resolve conflicting guidance: chat message > profile > application settings."""
from __future__ import annotations

import re
from typing import Any

from pipeline.i18n import infer_language_from_query, normalize_locale

INSTRUCTION_PRIORITY_PREAMBLE = (
    "INSTRUCTION PRIORITY (when guidance conflicts):\n"
    "1. The user's latest message in this chat — highest priority.\n"
    "2. The active profile rules and preferences.\n"
    "3. Application settings defaults — lowest priority.\n"
)


def infer_locale_from_profile(profile: dict | None) -> str | None:
    """Detect response language from profile RULES / INTENT text."""
    if not profile or not isinstance(profile, dict):
        return None

    chunks: list[str] = []
    rules = profile.get("RULES") if isinstance(profile.get("RULES"), dict) else {}
    for key in ("must_follow", "must_avoid", "safety_constraints"):
        vals = rules.get(key) if isinstance(rules, dict) else None
        if isinstance(vals, list):
            chunks.extend(str(v) for v in vals if v)
        elif vals:
            chunks.append(str(vals))

    intent = profile.get("INTENT") if isinstance(profile.get("INTENT"), dict) else {}
    for key in ("primary_goal", "task_scope", "success_criteria"):
        val = intent.get(key)
        if val:
            chunks.append(str(val))

    blob = " ".join(chunks).lower()
    if not blob.strip():
        return None

    if re.search(
        r"traditional\s+chinese|繁體|繁体|zh[_\s-]?tw|taiwan\s+chinese",
        blob,
        re.IGNORECASE,
    ):
        return "zh_tw"
    if re.search(
        r"simplified\s+chinese|简体中文|简体|zh[_\s-]?cn|mandarin",
        blob,
        re.IGNORECASE,
    ):
        return "zh_cn"
    if re.search(r"\benglish\b", blob) and re.search(
        r"reply|respond|answer|write|use", blob
    ):
        return "en"
    if re.search(r"中文|汉语|華語|国语", blob):
        return "zh_cn"
    return None


def resolve_response_locale(
    user_query: str,
    profile: dict | None = None,
    *,
    settings_locale: str | None = None,
) -> str:
    """
    Merge language preference with priority: chat > profile > settings.
    """
    from_chat = infer_language_from_query(user_query)
    if from_chat:
        return normalize_locale(from_chat)

    from_profile = infer_locale_from_profile(profile)
    if from_profile:
        return normalize_locale(from_profile)

    if settings_locale:
        return normalize_locale(settings_locale)

    from pipeline.i18n import get_locale

    return get_locale()


def apply_instruction_priority(
    system_text: str,
    *,
    user_query: str = "",
    profile: dict | None = None,
    settings_locale: str | None = None,
) -> str:
    """
    Prepend priority preamble and append a single resolved LANGUAGE rule.
    """
    from pipeline.i18n import language_system_rule

    locale = resolve_response_locale(
        user_query,
        profile,
        settings_locale=settings_locale,
    )
    body = (system_text or "").strip()
    parts = [INSTRUCTION_PRIORITY_PREAMBLE.strip()]
    if body:
        parts.append(body)
    lang_rule = language_system_rule(locale)
    if lang_rule:
        parts.append(
            f"{lang_rule}\n"
            "(This language default applies only when the user's latest message and "
            "profile do not specify otherwise.)"
        )
    return "\n\n".join(parts)


def strip_priority_preamble_echo(text: str) -> str:
    """Remove weak-model echoes of the instruction-priority block from assistant output."""
    body = (text or "").strip()
    if not body:
        return body
    marker = "INSTRUCTION PRIORITY"
    if marker not in body.upper() and "highest priority" not in body.lower():
        return body
    lines = body.splitlines()
    out: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        lower = stripped.lower()
        if not skipping and (
            stripped.upper().startswith(marker)
            or lower.startswith("1. the user's latest message")
            or lower.startswith("2. the active profile")
            or lower.startswith("3. application settings")
        ):
            skipping = True
            continue
        if skipping:
            if re.match(r"^\d+\.\s+", stripped):
                continue
            if not stripped:
                continue
            skipping = False
        out.append(line)
    cleaned = "\n".join(out).strip()
    if cleaned:
        return cleaned
    # Model echoed only the priority block — never return that as content.
    return ""
