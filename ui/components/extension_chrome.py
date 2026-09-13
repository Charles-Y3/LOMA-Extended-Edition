# -*- coding: utf-8 -*-
"""Standard extension panel header — category color, uppercase label."""

from __future__ import annotations

from nicegui import ui

from pipeline.registry.extension_registry import extension_registry

# Keep in sync with services.plugins.extension_categories (utility = TOKEN USAGE cyan).
_CATEGORY_CLASS: dict[str, str] = {
    "utility": "text-cyan-400",
    "creativity": "text-orange-400",
    "productivity": "text-purple-400",
    "ludicity": "text-rose-400",
    "system": "text-gray-400",
    "tools": "text-cyan-400",
}

_HEADER = "text-[10px] font-bold tracking-widest mb-1 shrink-0 uppercase leading-none"


def extension_category(ext_id: str) -> str:
    if not ext_id:
        return "utility"
    for row in extension_registry.list_metadata():
        if row.get("id") == ext_id or row.get("extension_id") == ext_id:
            return str(row.get("category") or "utility").lower()
    try:
        cls = extension_registry._extensions.get(ext_id)
        if cls:
            return str(cls().metadata().get("category") or "utility").lower()
    except Exception:
        pass
    return "utility"


def category_text_class(category: str) -> str:
    return _CATEGORY_CLASS.get((category or "").lower(), "text-cyan-400")


def mount_extension_header(ext_id: str) -> None:
    label = extension_registry.label_for(ext_id).upper()
    cat = extension_category(ext_id)
    ui.label(label).classes(f"{_HEADER} {category_text_class(cat)}")
