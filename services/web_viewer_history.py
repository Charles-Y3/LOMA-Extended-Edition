# -*- coding: utf-8 -*-
"""Persistent recent-URL history for the Web Viewer extension — most-recent-first,
capped list saved in settings.json, so it survives across app restarts."""
from __future__ import annotations

_MAX_HISTORY = 5


def get_web_viewer_history() -> list[str]:
    from services.session import state

    raw = (state.current_settings or {}).get("web_viewer_history") or []
    return [u for u in raw if isinstance(u, str) and u.strip()]


def add_web_viewer_history(url: str) -> None:
    """Move `url` to the front of the history (deduping), capped at _MAX_HISTORY."""
    clean = (url or "").strip()
    if not clean:
        return

    from services.session import state
    from services.session import settings as session_settings

    settings = state.current_settings
    history = [u for u in get_web_viewer_history() if u != clean]
    history.insert(0, clean)
    settings["web_viewer_history"] = history[:_MAX_HISTORY]
    session_settings.save_settings(settings, quiet=True)
