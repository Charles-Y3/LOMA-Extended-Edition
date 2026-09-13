# -*- coding: utf-8 -*-
"""First-run language picker — blocks workspace until locale is chosen."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import language_select_options, normalize_locale, t
from services.session import settings as session_settings
from services.session import state


def language_picked() -> bool:
    setup = (state.current_settings or {}).get("setup") or {}
    return bool(setup.get("language_picked"))


def mount_language_splash(on_continue) -> None:
    if language_picked():
        on_continue()
        return

    picked = {"locale": normalize_locale(state.current_settings.get("language"))}
    lang_options = language_select_options()

    with ui.column().classes(
        "fixed inset-0 z-[10050] items-center justify-center bg-[#0a0a0c] loma-language-gate"
    ):
        with ui.card().classes(
            "w-[440px] rounded-2xl p-8 bg-[#0f0f12] border border-white/10"
        ):
            ui.label(t("splash.language_title")).classes(
                "text-lg font-bold tracking-wide text-blue-400 mb-1 w-full text-center"
            )
            ui.label(t("splash.language_hint")).classes(
                "text-xs text-gray-400 mb-4 w-full text-center leading-relaxed"
            )
            ui.label(t("settings.language")).classes("text-xs text-gray-500 mb-2 w-full")
            radio = ui.radio(lang_options, value=picked["locale"]).classes("w-full gap-2")
            radio.on_value_change(
                lambda e: picked.__setitem__("locale", normalize_locale(e.value))
            )

            def _continue() -> None:
                loc = normalize_locale(picked["locale"] or radio.value)
                state.current_settings["language"] = loc
                setup = dict(state.current_settings.get("setup") or {})
                setup["language_picked"] = True
                state.current_settings["setup"] = setup
                session_settings.save_settings(state.current_settings, quiet=True)
                from pipeline.startup_locale import apply_startup_messages

                apply_startup_messages()
                # Reload at page root so overlay/workspace are not children of this gate.
                ui.navigate.to("/")

            ui.button(t("splash.continue"), on_click=_continue).props("color=primary").classes(
                "w-full mt-6"
            )
