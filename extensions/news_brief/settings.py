# -*- coding: utf-8 -*-
"""Persisted settings for News Brief."""
from __future__ import annotations

import json
import os

_ROOT = os.path.join("data", "news_brief")
_SETTINGS_PATH = os.path.join(_ROOT, "settings.json")


def _ensure() -> None:
    os.makedirs(_ROOT, exist_ok=True)


def load_settings() -> dict:
    _ensure()
    if not os.path.isfile(_SETTINGS_PATH):
        return {}
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    _ensure()
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def load_email_to() -> str:
    return str(load_settings().get("email_to") or "").strip()


def save_email_to(addr: str) -> None:
    data = load_settings()
    data["email_to"] = (addr or "").strip()
    save_settings(data)
