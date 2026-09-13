# -*- coding: utf-8 -*-
"""Per-user extension enable/disable (bundled extensions can be toggled off)."""
from __future__ import annotations

from services.session import settings as session_settings
from services.session import state


def disabled_extensions(settings: dict | None = None) -> set[str]:
    s = settings if settings is not None else (state.current_settings or {})
    return {str(x).strip() for x in (s.get("disabled_extensions") or []) if str(x).strip()}


def is_extension_enabled(ext_id: str, settings: dict | None = None) -> bool:
    if not ext_id:
        return True
    return ext_id not in disabled_extensions(settings)


def set_extension_enabled(ext_id: str, enabled: bool, *, save: bool = True) -> None:
    s = state.current_settings or {}
    disabled = disabled_extensions(s)
    if enabled:
        disabled.discard(ext_id)
    else:
        disabled.add(ext_id)
    s["disabled_extensions"] = sorted(disabled)
    state.current_settings = s
    if save:
        session_settings.save_settings(s, quiet=True)
