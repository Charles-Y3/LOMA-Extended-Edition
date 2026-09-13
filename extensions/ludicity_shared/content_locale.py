# -*- coding: utf-8 -*-
"""LLM translation of extension content blobs for the active UI locale."""
from __future__ import annotations

import json
import re
from typing import Any

from pipeline.i18n import get_locale, language_system_rule

_cache: dict[str, Any] = {}


def _locale() -> str:
    return get_locale()


def _cache_get(key: str) -> Any | None:
    return _cache.get(f"{_locale()}:{key}")


def _cache_set(key: str, value: Any) -> Any:
    _cache[f"{_locale()}:{key}"] = value
    return value


def _parse_json_object(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def localize_markdown(text: str, *, context: str = "") -> str:
    loc = _locale()
    if loc == "en" or not (text or "").strip():
        return text or ""
    ck = f"md:{hash(text)}"
    hit = _cache_get(ck)
    if isinstance(hit, str):
        return hit
    from extensions.ludicity_shared.llm import ludicity_chat

    rule = language_system_rule(loc)
    out = ludicity_chat(
        [
            {
                "role": "system",
                "content": (
                    f"{rule}\n\n"
                    "Translate the user's markdown into the target language. "
                    "Preserve markdown structure (**bold**, headings, lists). "
                    "Output only the translation."
                    + (f"\n\nContext: {context}" if context else "")
                ),
            },
            {"role": "user", "content": text},
        ],
        extra_options={"temperature": 0.1},
    )
    translated = (out or text).strip()
    return _cache_set(ck, translated)


def localize_puzzle_fields(puzzle: dict[str, Any]) -> dict[str, Any]:
    """Localize puzzle UI text; keep canonical English answer for grading."""
    loc = _locale()
    if loc == "en":
        return dict(puzzle)
    pid = str(puzzle.get("id") or "")
    ck = f"puzzle:{pid}"
    hit = _cache_get(ck)
    if isinstance(hit, dict):
        return hit

    from extensions.ludicity_shared.llm import ludicity_chat

    rule = language_system_rule(loc)
    payload = {
        "title": puzzle.get("title", ""),
        "puzzle": puzzle.get("puzzle", ""),
        "hints": list(puzzle.get("hints") or []),
        "explanation": puzzle.get("explanation", ""),
        "answer": puzzle.get("answer", ""),
    }
    raw = ludicity_chat(
        [
            {
                "role": "system",
                "content": (
                    f"{rule}\n\n"
                    "Translate a logic-puzzle JSON object into the target language.\n"
                    "Return ONLY valid JSON with keys: title, puzzle, hints (array), "
                    "explanation, answer, answer_aliases (array).\n"
                    "- Translate title, puzzle, hints, explanation for the player.\n"
                    "- Keep answer as the original English canonical token.\n"
                    "- answer_aliases: lowercase acceptable answers in target language "
                    "AND English (include the canonical answer)."
                ),
            },
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        extra_options={"temperature": 0.1},
    )
    data = _parse_json_object(raw) or {}
    out = dict(puzzle)
    for key in ("title", "puzzle", "explanation"):
        if data.get(key):
            out[key] = str(data[key])
    if isinstance(data.get("hints"), list) and data["hints"]:
        out["hints"] = [str(h) for h in data["hints"]]
    aliases = [str(a).strip().lower() for a in (data.get("answer_aliases") or []) if str(a).strip()]
    base = str(puzzle.get("answer") or "").strip().lower()
    if base and base not in aliases:
        aliases.insert(0, base)
    out["answer_aliases"] = aliases or ([base] if base else [])
    return _cache_set(ck, out)


def localize_history_encounter(enc: Any) -> dict[str, Any]:
    """Localized encounter fields for workspace chat display."""
    from extensions.history_events.encounter_backgrounds import chat_background_for

    loc = _locale()
    base = {
        "title": enc.title,
        "era": enc.era,
        "region": enc.region,
        "category": enc.category,
        "background": chat_background_for(enc),
        "setup": enc.setup,
        "factors": list(enc.key_factors or []),
        "question": enc.question,
    }
    if loc == "en":
        return base

    ck = f"history:{enc.id}"
    hit = _cache_get(ck)
    if isinstance(hit, dict):
        return hit

    from extensions.ludicity_shared.llm import ludicity_chat

    rule = language_system_rule(loc)
    raw = ludicity_chat(
        [
            {
                "role": "system",
                "content": (
                    f"{rule}\n\n"
                    "Translate historical encounter content for display. "
                    "Do NOT reveal outcomes beyond what is already in the input. "
                    "Return ONLY JSON with keys: title, era, region, category, background, "
                    "setup, factors (array), question.\n"
                    "Preserve markdown inside strings. Keep proper nouns recognizable."
                ),
            },
            {"role": "user", "content": json.dumps(base, ensure_ascii=False)},
        ],
        extra_options={"temperature": 0.15},
    )
    data = _parse_json_object(raw) or {}
    out = dict(base)
    for key in ("title", "era", "region", "category", "background", "setup", "question"):
        if data.get(key):
            out[key] = str(data[key])
    if isinstance(data.get("factors"), list) and data["factors"]:
        out["factors"] = [str(f) for f in data["factors"]]
    return _cache_set(ck, out)
