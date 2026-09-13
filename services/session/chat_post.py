# -*- coding: utf-8 -*-
"""Post assistant messages into the workspace chat panel."""
from __future__ import annotations


def _refresh_chat() -> None:
    try:
        from ui.components.chat_message import render_chat

        render_chat.refresh()
    except Exception:
        pass


def post_assistant_message(content: str, *, anchor: str | None = None, scroll_to: str | None = None) -> None:
    text = (content or "").strip()
    if not text:
        return

    def _apply() -> None:
        from services.session import state

        msg = {"role": "assistant", "content": text}
        if anchor:
            msg["anchor"] = anchor
        if scroll_to:
            msg["scroll_to"] = scroll_to
        state.messages.append(msg)
        _refresh_chat()

    _schedule(_apply)


def post_processing_message(content: str) -> None:
    text = (content or "").strip() or "LOMA is processing…"

    def _apply() -> None:
        from services.session import state

        state.messages.append(
            {"role": "assistant", "content": text, "processing": True}
        )
        _refresh_chat()

    _schedule(_apply)


def finish_assistant_message(content: str) -> None:
    text = (content or "").strip()
    if not text:
        return

    def _apply() -> None:
        from services.session import state

        if state.messages and (state.messages[-1].get("role") or "") == "assistant":
            state.messages[-1]["content"] = text
            state.messages[-1].pop("processing", None)
        else:
            state.messages.append({"role": "assistant", "content": text})
        _refresh_chat()

    _schedule(_apply)


def set_assistant_message(content: str, *, scroll_to: str | None = None) -> None:
    """Replace the last assistant bubble or create one (for streaming updates)."""
    text = (content or "").strip()
    if not text:
        return

    def _apply() -> None:
        from services.session import state

        if state.messages and (state.messages[-1].get("role") or "") == "assistant":
            state.messages[-1]["content"] = text
            if scroll_to:
                state.messages[-1]["scroll_to"] = scroll_to
        else:
            msg = {"role": "assistant", "content": text}
            if scroll_to:
                msg["scroll_to"] = scroll_to
            state.messages.append(msg)
        _refresh_chat()

    _schedule(_apply)


def _schedule(fn) -> None:
    try:
        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(fn)
    except Exception:
        fn()
