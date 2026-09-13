# -*- coding: utf-8 -*-
"""LOMA icon easter egg — click the sidebar icon for a random, useful "spark" in the
workspace chat, plus a brief glow on the icon itself. Ported from LOMA1's
ui/components/loma_spark.py, adapted to this edition's own capabilities."""
from __future__ import annotations

import random

from nicegui import ui

from pipeline.i18n import t
from services.session import state

_SPARK_COUNT = 12


def _random_spark_text() -> str:
    keys = [f"loma.spark.{i}" for i in range(_SPARK_COUNT)]
    last = getattr(state, "_last_spark_key", None)
    choices = [k for k in keys if k != last] or keys
    key = random.choice(choices)
    state._last_spark_key = key
    return t(key)


def _post_spark_to_chat() -> None:
    state.messages.append({"role": "assistant", "content": _random_spark_text(), "spark": True})
    try:
        ui_mod = state.get_ui_module()
        if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):
            ui_mod.render_chat.refresh()
    except Exception:
        pass
    try:
        from ui.themes.assets import schedule_scroll_chat

        schedule_scroll_chat(0.03)
    except Exception:
        pass


def bind_loma_icon_spark(icon_element) -> None:
    """Make the LOMA icon clickable: glow briefly and drop a useful hint in chat."""
    icon_element.classes("cursor-pointer transition-transform hover:scale-105")
    icon_element.tooltip(t("loma.spark.tooltip"))

    def _on_click() -> None:
        icon_element.classes(add="loma-icon-glow")
        ui.timer(2.6, lambda: icon_element.classes(remove="loma-icon-glow"), once=True)
        _post_spark_to_chat()

    icon_element.on("click", _on_click)
