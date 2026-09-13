# -*- coding: utf-8 -*-
"""Formslator user settings persistence."""
from __future__ import annotations

import json
import os
from typing import Any

from services.formslator.paths import SETTINGS_FILE, ensure_dirs

DEFAULT_SETTINGS: dict[str, Any] = {
    "active_template": "",
    "single_column_template": "",
    "original_column": "left",
    "last_model": "",
    "url_template": (
        "https://www.mdbg.net/chinese/dictionary?page=worddict&wdrst=0&wdqb={term}"
    ),
    "default_translate_sec_per_char": 0.25,
    "translate_sec_per_char": {},
    "use_vault_prefill": False,
    "vault_scope": "all",
    "vault_library_ids": [],
    "custom_target_name": "",
}


def get_model_sec_per_char(model_name: str) -> float:
    """Seconds per character estimate for a model (learned over time)."""
    settings = load_settings()
    per_model = settings.get("translate_sec_per_char") or {}
    if model_name and model_name in per_model:
        try:
            return max(0.05, float(per_model[model_name]))
        except (TypeError, ValueError):
            pass
    try:
        return max(0.05, float(settings.get("default_translate_sec_per_char", 0.25)))
    except (TypeError, ValueError):
        return 0.25


def record_translate_speed(model_name: str, total_chars: float, total_seconds: float) -> None:
    """Update per-model translation speed using exponential moving average."""
    if not model_name or total_chars <= 0 or total_seconds < 15:
        return
    observed = total_seconds / total_chars
    settings = load_settings()
    per_model = dict(settings.get("translate_sec_per_char") or {})
    prev = float(per_model.get(model_name, settings.get("default_translate_sec_per_char", 0.25)))
    per_model[model_name] = round(0.6 * prev + 0.4 * observed, 4)
    settings["translate_sec_per_char"] = per_model
    save_settings(settings)


def load_settings() -> dict[str, Any]:
    ensure_dirs()
    if not os.path.isfile(SETTINGS_FILE):
        return dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_SETTINGS)
        merged.update(data if isinstance(data, dict) else {})
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def save_settings(data: dict[str, Any]) -> None:
    ensure_dirs()
    merged = dict(DEFAULT_SETTINGS)
    merged.update(data)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
