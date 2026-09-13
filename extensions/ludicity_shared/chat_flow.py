# -*- coding: utf-8 -*-
"""Workspace chat helpers for Ludicity extensions."""
from __future__ import annotations

from collections.abc import Callable

from services.session.chat_post import post_assistant_message, set_assistant_message

_streaming = False


def post_player_line(
    text: str, *, label: str = "Your action", anchor: str | None = None, scroll_to: str | None = None
) -> None:
    post_assistant_message(
        f'<span style="color:#60a5fa">**{label}:** {text}</span>',
        anchor=anchor,
        scroll_to=scroll_to,
    )


def post_status_line(text: str, *, scroll_to: str | None = None) -> None:
    post_assistant_message(f"_{text}_", scroll_to=scroll_to)


def begin_stream_bubble(header: str, *, scroll_to: str | None = None) -> None:
    global _streaming
    _streaming = True
    post_assistant_message(f"{header}\n\n", scroll_to=scroll_to)


def resume_stream_bubble(header: str, *, scroll_to: str | None = None) -> None:
    """Reset the in-progress bubble without appending a new chat message."""
    global _streaming
    _streaming = True
    set_assistant_message(f"{header}\n\n", scroll_to=scroll_to)


def stream_bubble(header: str, partial: str, *, cursor: bool = True) -> None:
    suffix = "▌" if cursor else ""
    set_assistant_message(f"{header}\n\n{partial}{suffix}")


def finalize_bubble(header: str, body: str, *, footer: str = "", scroll_to: str | None = None) -> None:
    global _streaming
    text = f"{header}\n\n{body.strip()}"
    if footer:
        text = f"{text}\n\n{footer}"
    set_assistant_message(text, scroll_to=scroll_to)
    _streaming = False


def is_streaming() -> bool:
    return _streaming
