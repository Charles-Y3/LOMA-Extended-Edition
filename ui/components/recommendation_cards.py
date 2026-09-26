# -*- coding: utf-8 -*-
"""Reusable 3-tier model recommendation cards for setup wizard."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from config.model_catalog import hardware_tag
from pipeline.i18n import t
from services.system.profiler import SystemProfile
from pipeline.i18n import t as _tr  # noqa: E402


def build_recommendation_cards(
    tiers: dict[str, dict | None],
    profile: SystemProfile,
    *,
    on_select: Callable[[str, dict], None] | None = None,
) -> ui.radio:
    """Build three selectable cards; returns the radio bound to model name."""
    options: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    default = ""

    slot_labels = {
        "recommended": t("setup.tier.recommended"),
        "quality": t("setup.tier.quality"),
        "fast": t("setup.tier.fast"),
    }

    for slot, entry in tiers.items():
        if not entry:
            continue
        name = entry["name"]
        tag = hardware_tag(entry, profile)
        badge = entry.get("badge") or slot_labels.get(slot, slot)
        options[name] = f"{badge} — {entry.get('label', name)} ({entry.get('size', '?')}) [{tag}]"
        descriptions[name] = entry.get("desc", "")
        if slot == "recommended" and not default:
            default = name

    if not default and options:
        default = next(iter(options))

    ui.label(t("setup.llm.pick_model")).classes("text-xs font-bold text-gray-500 mt-2")
    radio = ui.radio(options, value=default).classes("w-full gap-2")
    desc_box = ui.markdown("").classes("text-xs text-gray-500 italic mt-2 p-2 bg-gray-50 dark:bg-gray-800 rounded")

    def _update(val: str) -> None:
        desc_box.set_content(_tr("installer.vision_details", desc=descriptions.get(val, "")))

    radio.on_value_change(lambda e: _update(e.value))
    _update(default)

    if on_select:
        radio.on_value_change(lambda e: on_select(e.value, tiers.get("recommended") or {}))

    return radio
