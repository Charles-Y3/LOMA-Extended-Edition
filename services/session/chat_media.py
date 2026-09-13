# -*- coding: utf-8 -*-
"""Post a generated image into the workspace chat at full size.

Registers a ChartArtifact (so the chat renderer can resolve the file) and appends
an assistant message carrying a [[FIGURE:slot:title]] marker with the
chart_figures_ready flag the renderer requires to actually draw the image.
"""
from __future__ import annotations

from services.session.chat_post import _refresh_chat, _schedule


def post_image_to_chat(
    path: str, caption: str = "", *, title: str = "", chart_type: str = "generated_image"
) -> None:
    """Register an image artifact and post a chat message that renders it full size.

    ``caption`` is the message body shown above the figure (e.g. the raw prompt/instruction);
    ``title`` is the shorter "Figure N: ..." label — pass it separately when the caption is
    too long or verbatim-prompt-like to serve as a figure title. Falls back to ``caption``.

    Call from the UI loop (inside a ``with client:`` block). ``_refresh_chat`` needs
    the active client slot to repaint, so the state mutation runs synchronously here
    rather than being bounced to a threadsafe callback that would drop that context.
    """
    path = (path or "").strip()
    if not path:
        return
    title = (title or caption or "Generated image").strip()

    from services.graph_generation.models import ChartArtifact
    from services.session import state

    artifacts = getattr(state, "chart_artifacts", None)
    if artifacts is None:
        artifacts = []
        state.chart_artifacts = artifacts
    slot = max((int(getattr(a, "slot", 0) or 0) for a in artifacts), default=0) + 1
    artifacts.append(
        ChartArtifact(path=path, title=title, chart_type=chart_type, caption="", slot=slot)
    )
    body = (f"{caption.strip()}\n\n" if caption.strip() else "") + f"[[FIGURE:{slot}:{title}]]"
    state.messages.append({"role": "assistant", "content": body, "chart_figures_ready": True})

    # Repaint in the current client slot; if we're off the UI loop, reschedule the refresh only.
    try:
        _refresh_chat()
    except Exception:  # noqa: BLE001
        _schedule(_refresh_chat)
