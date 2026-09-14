# -*- coding: utf-8 -*-
from nicegui import ui

from services.session import settings as session_settings
from services.session import state
from ui.components.chat_freeze import build_freeze_dialog
from ui.components.chat_summarize import build_summarize_dialog
from ui.components.chat_message import render_chat
from ui.components.process_indicator import render_progress
from ui.layouts.extension_panel import build_extension_panel
from ui.layouts.nav_panel import build_nav_panel
from ui.components.usage_tips import build_tips_dialog
from ui.layouts.topbar import build_settings_dialog
from ui.layouts.workspace_panel import build_workspace_panel
from ui.themes import registry, tokens
from ui.themes.assets import inject_custom_assets, inject_splitter_resizable_script

__all__ = ["build_ui", "render_chat", "render_progress"]


async def _resolve_system_theme() -> None:
    """The "System" theme option follows the OS light/dark preference — resolved once per
    client connection via the browser's prefers-color-scheme media query (there's no
    server-side way to know this ahead of a real connected client). Cached in
    state.current_settings for the rest of this session so get_theme() doesn't need to be
    async everywhere else it's called."""
    if state.current_settings.get("theme") != "system":
        return
    if state.system_theme_is_dark is not None:
        return
    try:
        is_dark = await ui.run_javascript(
            "window.matchMedia('(prefers-color-scheme: dark)').matches", timeout=3.0
        )
    except Exception:
        is_dark = True
    state.system_theme_is_dark = bool(is_dark)


async def build_ui() -> None:
    await _resolve_system_theme()
    current_theme = state.current_settings.get("theme", "dark")
    t = tokens.get_theme(current_theme)

    from pipeline.i18n import get_locale

    lang_class = "loma-lang-zh" if get_locale(state.current_settings) in ("zh_tw", "zh_cn") else ""
    ui.query("body").classes(
        f'bg-[{t["bg"]}] overflow-hidden {t["text"]} antialiased loma-app {t["body_class"]} {lang_class}'
    )
    # Quasar body.dark fights Pure Light (invisible borders / grey-on-grey). Keep dark for
    # Deep Obsidian + Terminal Green (and "system" resolving to either) only.
    ui.dark_mode(t is not tokens.THEMES["light"])
    inject_custom_assets()

    def on_save_reload():
        session_settings.save_settings(state.current_settings)
        from pipeline.startup_locale import apply_startup_messages
        from services.inference.readiness import mark_inference_ready_if_available
        from services.startup_warmup import ensure_models_warm, reset_routing_warmup
        from services.voice_reply import stop_voice_reply
        from ui.components.startup_overlay import warm_tts_voice_background as _warm_tts_voice_background

        stop_voice_reply()
        if not mark_inference_ready_if_available():
            from services.inference.readiness import start_inference_readiness

            start_inference_readiness(background=True)
        # Without this, ensure_models_warm() is a no-op after the app's initial boot
        # warmup (its "already started" latch never resets on its own) — so a role change
        # here would silently skip warming the new model, and the next chat message
        # ("hi") would pay the full model-load cost instead of getting an instant reply.
        reset_routing_warmup()
        ensure_models_warm(background=True)
        _warm_tts_voice_background()
        apply_startup_messages()
        ui.navigate.to("/")

    settings_dialog = build_settings_dialog(on_save_reload, t)
    tips_dialog = build_tips_dialog(t)
    build_freeze_dialog()
    build_summarize_dialog()

    # App-level, not scoped to the Document Intelligence panel's own mount — that
    # extension's ui.timer only polls while its panel is actually open/mounted, so a
    # password prompt raised by background indexing after the user switched away (or
    # before ever opening that panel) had no live poller and just hung until the
    # 10-minute wait in passwords.request_password_dialog() gave up silently.
    from extensions.knowledge_vault.passwords import poll_password_dialog

    ui.timer(0.3, poll_password_dialog)

    with ui.column().classes(
        "flex-1 flex flex-col flex-nowrap overflow-hidden no-scrollbar min-h-0"
    ).props("id=loma-shell"):
        with ui.row().classes(
            "flex-1 flex flex-nowrap overflow-hidden no-scrollbar min-h-0 min-w-0"
        ).props("id=loma-panels-row"):
            build_nav_panel(t, settings_dialog, tips_dialog)

            # Extension and workspace share gap-0 (splitter only), like the old UI.
            with ui.row().classes("flex-1 h-full min-h-0 gap-0 flex-nowrap overflow-hidden no-scrollbar"):
                build_extension_panel(t)

                registry.ext_ws_splitter = (
                    ui.element("div")
                    .props("id=loma-ext-ws-splitter")
                    .classes(
                        "w-2 h-full shrink-0 cursor-col-resize hover:bg-blue-500/20 "
                        "transition-colors duration-150"
                    )
                )
                registry.ext_ws_splitter.set_visibility(False)

                with ui.row().classes("flex-1 h-full no-wrap gap-0 items-stretch min-h-0 min-w-0") as center_row:
                    registry.center_row = center_row

                    build_workspace_panel(t)

    inject_splitter_resizable_script()
