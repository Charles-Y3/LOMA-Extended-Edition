# -*- coding: utf-8 -*-
"""Re-apply startup user-visible strings after locale changes."""
from __future__ import annotations

from pipeline.i18n import t as tr


def apply_startup_messages() -> None:
    from services.session import state

    state.orchestra_log = [tr("console.system_init"), tr("console.awaiting_input")]
    if state.messages and state.messages[0].get("role") == "assistant":
        state.messages[0]["content"] = tr("chat.online")
        state.messages[0]["bootstrap"] = True
    try:
        from ui.components.startup_overlay import refresh_startup_overlay

        refresh_startup_overlay()
    except Exception:
        pass
    try:
        from ui.themes import registry

        if getattr(registry, "log_inner", None) is not None:
            from ui.layouts.output_panel import render_logs

            render_logs.refresh()
    except Exception:
        pass
