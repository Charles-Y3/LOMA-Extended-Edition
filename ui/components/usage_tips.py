# -*- coding: utf-8 -*-
"""Rotating usage tips (top bar) and full tips dialog."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import is_cjk_locale, section_label_class, t

ROTATING_TIP_KEYS: tuple[str, ...] = (
    "tips.banner.sources",
    "tips.banner.extensions",
    "tips.banner.research",
    "tips.banner.highlight",
)

TIP_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "tips.section.chat",
        (
            "tips.item.chat_ask",
            "tips.item.chat_sources",
            "tips.item.chat_web",
        ),
    ),
    (
        "tips.section.sources",
        (
            "tips.item.sources_drop",
            "tips.item.sources_highlight",
        ),
    ),
    (
        "tips.section.extensions",
        (
            "tips.item.ext_research",
            "tips.item.ext_docintel",
            "tips.item.ext_more",
        ),
    ),
    (
        "tips.section.settings",
        (
            "tips.item.set_models",
            "tips.item.set_about",
        ),
    ),
)


def build_tips_dialog(theme_tokens: dict) -> ui.dialog:
    muted = theme_tokens["muted"]
    tab_scroll = "w-full max-h-[62vh] overflow-y-auto overflow-x-hidden pr-1"
    with ui.dialog() as tips_dialog, ui.card().classes(
        f"w-[520px] max-h-[85vh] overflow-hidden rounded-2xl p-6 {theme_tokens['settings_card']}"
    ):
        with ui.row().classes("w-full items-center gap-2 mb-3"):
            ui.icon("tips_and_updates", color="amber").classes("text-xl")
            ui.label(t("tips.dialog.title")).classes("text-sm font-bold tracking-wide")
        ui.label(t("tips.dialog.subtitle")).classes(
            f"{'text-xs' if is_cjk_locale() else 'text-[10px]'} {muted} mb-4 leading-snug"
        )
        with ui.column().classes(tab_scroll):
            for section_key, item_keys in TIP_SECTIONS:
                ui.label(t(section_key)).classes(
                    f"{section_label_class('text-amber-500/90 mt-3 mb-1')}"
                )
                for item_key in item_keys:
                    ui.label(t(item_key)).classes(
                        f"{'text-sm' if is_cjk_locale() else 'text-xs'} {muted} mb-1.5 leading-snug pl-2"
                    )
        ui.button(t("library.close"), on_click=tips_dialog.close).props("flat color=primary").classes("mt-4")
    return tips_dialog


def mount_rotating_tip(theme_tokens: dict, *, on_open_dialog) -> None:
    """Ad-style rotating tip row; place under the progress indicators."""
    muted = theme_tokens["muted"]
    idx = {"n": 0}

    banner = ui.row().classes(
        "items-center gap-1.5 px-2.5 py-0.5 rounded-full max-w-[min(480px,92vw)] "
        "bg-amber-500/10 border border-amber-500/25 cursor-pointer hover:bg-amber-500/15 transition-colors"
    ).tooltip(t("tips.dialog.tooltip"))

    with banner:
        ui.icon("campaign", size="xs").classes("text-amber-400/90 shrink-0")
        tip_label = ui.label(t(ROTATING_TIP_KEYS[0])).classes(
            f"text-[8px] tracking-wide leading-snug {muted} truncate"
        )

    def _rotate() -> None:
        idx["n"] = (idx["n"] + 1) % len(ROTATING_TIP_KEYS)
        tip_label.set_text(t(ROTATING_TIP_KEYS[idx["n"]]))

    banner.on("click", on_open_dialog)
    ui.timer(10.0, _rotate)
