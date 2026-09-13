# -*- coding: utf-8 -*-
"""Language hints, neighbour context, and LLM-safe unit payloads for mutation."""
from __future__ import annotations

import re
from typing import Any, Callable

_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")
_JP = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_KR = re.compile(r"[\uac00-\ud7af]")
_LATIN = re.compile(r"[A-Za-z]")


def detect_lang_hint(text: str) -> str:
    t = text or ""
    if not t.strip():
        return "empty"
    cjk = len(_CJK.findall(t))
    jp = len(_JP.findall(t))
    kr = len(_KR.findall(t))
    latin = len(_LATIN.findall(t))
    if cjk >= max(jp, kr, latin) and cjk > 0:
        return "chinese"
    if jp > max(cjk, kr, latin) and jp > 0:
        return "japanese"
    if kr > max(cjk, jp, latin) and kr > 0:
        return "korean"
    if latin > 0:
        return "latin"
    return "mixed"


def heading_level_from_style(style_name: str) -> int | None:
    name = (style_name or "").strip().lower()
    if not name:
        return None
    m = re.match(r"heading\s*(\d+)", name)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return None
    if name in ("title", "subtitle"):
        return 0
    return None


def enrich_neighbor_context(
    units: list[dict],
    *,
    group_key: Callable[[dict], Any],
    max_chars: int = 100,
) -> None:
    """Attach short before/after text from adjacent units in the same group."""
    for i, unit in enumerate(units):
        key = group_key(unit)
        if i > 0 and group_key(units[i - 1]) == key:
            prev = (units[i - 1].get("text") or units[i - 1].get("value") or "").strip()
            if prev:
                unit["context_before"] = prev[:max_chars]
        if i + 1 < len(units) and group_key(units[i + 1]) == key:
            nxt = (units[i + 1].get("text") or units[i + 1].get("value") or "").strip()
            if nxt:
                unit["context_after"] = nxt[:max_chars]


def unit_original_text(unit: dict) -> str:
    return str(unit.get("text") or unit.get("value") or "")


def unit_changed(unit: dict, text_map: dict[str, str]) -> bool:
    uid = unit.get("id") or ""
    if not uid or uid not in text_map:
        return False
    return text_map[uid] != unit_original_text(unit)


def changed_text_map(units: list[dict], text_map: dict[str, str]) -> dict[str, str]:
    """Only ids whose replacement differs from extracted original."""
    return {
        u["id"]: text_map[u["id"]]
        for u in units
        if u.get("id") and unit_changed(u, text_map)
    }


def unit_to_plan_payload(unit: dict) -> dict[str, Any]:
    """Metadata-rich fragment for LLM planning (style/structure are context only — do not alter)."""
    payload: dict[str, Any] = {
        "id": unit["id"],
        "text": unit_original_text(unit),
    }
    for key in (
        "format",
        "slide",
        "page",
        "section",
        "role",
        "location",
        "structure",
        "style_name",
        "heading_level",
        "alignment",
        "kind",
        "sheet",
        "cell",
        "shape",
        "lang_hint",
        "context_before",
        "context_after",
    ):
        if unit.get(key) is not None:
            payload[key] = unit[key]
    style = unit.get("style")
    if isinstance(style, dict) and style:
        payload["style"] = style
    return payload
