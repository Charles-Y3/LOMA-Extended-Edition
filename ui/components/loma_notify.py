# -*- coding: utf-8 -*-
"""LOMA notification defaults — top of screen, 3 second timeout."""
from __future__ import annotations

from nicegui import ui

_DEFAULT_TIMEOUT_MS = 3000
_DEFAULT_POSITION = "top"
_PATCHED = False


def notify(
    message: str,
    *,
    color: str = "info",
    type: str | None = None,
    timeout: int | None = _DEFAULT_TIMEOUT_MS,
    position: str = _DEFAULT_POSITION,
    **kwargs,
) -> None:
    opts: dict = {
        "message": message,
        "position": position,
        "timeout": timeout if timeout is not None else _DEFAULT_TIMEOUT_MS,
        **kwargs,
    }
    if type is not None:
        opts["type"] = type
    else:
        opts["color"] = color
    ui.notify(**opts)


def install_notify_defaults() -> None:
    """Patch ui.notify so every banner uses top + 3s unless explicitly overridden."""
    global _PATCHED
    if _PATCHED:
        return
    _PATCHED = True
    _original = ui.notify

    def _wrapped(*args, **kwargs):
        if args and isinstance(args[0], str) and "message" not in kwargs:
            kwargs["message"] = args[0]
        kwargs.pop("pos", None)
        kwargs.setdefault("position", _DEFAULT_POSITION)
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = _DEFAULT_TIMEOUT_MS
        return _original(**kwargs)

    ui.notify = _wrapped  # type: ignore[method-assign]
