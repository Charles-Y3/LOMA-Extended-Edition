# -*- coding: utf-8 -*-
from nicegui import ui

from pipeline.i18n import language_select_options, t, theme_select_options, tts_rate_select_options, upload_retention_select_options, is_cjk_locale
from services.session import handlers, state
from config import get_installed_models
from services.model_router import (
    chat_capable_models,
    get_vision_capable_models,
    installed_image_generation_options,
)
from ui.components.model_library_panel import build_model_library_panel
from services.session.upload_cleanup import upload_retention_days
from ui.branding import LOMA_ICON_URL
from ui.themes import registry, tokens


def _vision_model_options() -> tuple[list[str], str]:
    """Installed-only, matching the whisper dropdown's pattern — no not-installed fallback."""
    capable = get_vision_capable_models(probe=False)
    current = (state.current_settings.get("default_vision_model") or "").strip()
    if not capable:
        return [], ""
    if current not in capable:
        current = capable[0]
        state.current_settings["default_vision_model"] = current
    return capable, current


def _image_model_options() -> tuple[dict[str, str], str]:
    """Installed-only, matching the vision/whisper dropdowns' pattern — no not-installed
    fallback (see services.model_router.installed_image_generation_options)."""
    options, current = installed_image_generation_options(state.current_settings)
    if current:
        state.current_settings["default_image_model"] = current
    return options, current


def refresh_default_model_select() -> None:
    """Drop deleted models from the General tab's Default Model dropdown.

    That select's options are a snapshot taken when the settings dialog was
    built, so a model deleted afterward in the Model Library subtab kept
    appearing there until a full page reload. Call this right after a model
    library delete to update it in place instead.
    """
    sel = registry.default_model_select
    if sel is None:
        return
    installed = chat_capable_models(get_installed_models(force_refresh=True))
    current = (sel.value or "").strip()
    if current and current not in installed:
        from services.session import settings as session_settings

        suggestions = tokens.ROLE_SUGGESTIONS.get("General", [])
        fallback = next((m for m in suggestions if m in installed), installed[0] if installed else "")
        sel.set_options(installed, value=fallback)
        if fallback:
            handlers.update_role_in_memory("General", fallback)
            handlers.update_role_in_memory("Specialist", fallback)
            session_settings.save_settings(state.current_settings, quiet=True)
    else:
        sel.set_options(installed)


def refresh_vision_model_select() -> None:
    """Same staleness problem as refresh_default_model_select(), for the Roles tab's
    "Vision" dropdown: downloading a new multimodal LLM via the Model Library used to
    only show up after a full reload."""
    fn = registry.refresh_vision_select_fn
    if fn:
        fn()


def refresh_image_select() -> None:
    """Same staleness problem as refresh_default_model_select(), for the Roles tab's
    "Image generation" dropdown: downloading or deleting a checkpoint via the Model
    Library used to only show up after a full reload."""
    fn = registry.refresh_image_select_fn
    if fn:
        fn()


def refresh_whisper_select() -> None:
    """Same staleness problem as refresh_default_model_select(), for the General tab's
    default-transcription-size dropdown: downloading a new Whisper size via the Model
    Library used to only show up after a full reload. Rebuilds the whole select/label
    (not just its options) since which one is shown depends on whether anything is
    installed at all."""
    fn = registry.refresh_whisper_select_fn
    if fn:
        fn()


def refresh_voice_select() -> None:
    """Same staleness problem as refresh_default_model_select(), for the "Reply voice"
    dropdown: downloading a new Piper voice used to only show up after a full reload."""
    sel = registry.tts_voice_select
    if sel is None:
        return
    from services.voice_reply import resolve_voice_id, tts_voice_options

    current = resolve_voice_id(state.current_settings)
    sel.set_options(tts_voice_options(), value=current)
    state.current_settings["tts_voice_id"] = current


def refresh_traditional_chinese_checkbox() -> None:
    """Same staleness problem as refresh_default_model_select(), for the General tab's
    "Traditional Chinese" checkbox: its visibility depends on sensevoice_downloaded() or
    installed_whisper_sizes(), which were only ever re-checked on a full page reload —
    downloading/deleting SenseVoice or a Whisper size via the Model Library left it stuck
    at whatever it was when the Settings dialog was first built."""
    fn = registry.refresh_traditional_chinese_fn
    if fn:
        fn()


def build_settings_dialog(on_save_reload, theme_tokens: dict) -> ui.dialog:
    tab_scroll = "w-full max-h-[62vh] overflow-y-auto overflow-x-hidden pr-1"
    hint_sm = f"text-[{'10px' if is_cjk_locale() else '9px'}]"
    section_sm = "text-xs" if is_cjk_locale() else "text-[10px]"
    with ui.dialog() as settings_dialog, ui.card().classes(
        f"w-[560px] max-h-[90vh] overflow-hidden rounded-2xl p-6 {theme_tokens['settings_card']}"
    ):
        ui.label(t("panel.settings")).classes(
            f"{'text-xs' if is_cjk_locale() else 'text-[11px]'} font-bold tracking-widest text-blue-400 mb-2"
        )

        with ui.tabs().classes("w-full") as tabs:
            tab_general = ui.tab(t("settings.tab.general"))
            tab_roles = ui.tab(t("settings.tab.roles"))
            tab_model_library = ui.tab(t("settings.tab.model_library"))
            tab_about = ui.tab(t("settings.tab.about"))

        with ui.tab_panels(tabs, value=tab_general).classes("w-full"):
            with ui.tab_panel(tab_general):
                with ui.column().classes(tab_scroll):
                    ui.label(t("settings.everyday_blurb")).classes(
                        f"{hint_sm} {theme_tokens['muted']} mb-3 leading-snug"
                    )
                    ui.select(
                        language_select_options(),
                        label=t("settings.language"),
                    ).props(theme_tokens["select_props"]).classes("w-full mb-4").bind_value(
                        state.current_settings, "language"
                    )
                    ui.select(
                        theme_select_options(),
                        label=t("settings.theme"),
                    ).props(theme_tokens["select_props"]).classes("w-full mb-4").bind_value(
                        state.current_settings, "theme"
                    )
                    retention_options = upload_retention_select_options()
                    ui.select(
                        retention_options,
                        label=t("settings.upload_retention_label"),
                        value=upload_retention_days(state.current_settings),
                        on_change=lambda e: state.current_settings.__setitem__(
                            "upload_retention_days", int(e.value)
                        ),
                    ).props(theme_tokens["select_props"]).classes("w-full mb-4").tooltip(
                        t("settings.upload_retention_hint")
                    )

                    def _toggle_web_grounding(e) -> None:
                        from services.session import settings as session_settings

                        state.current_settings["web_grounding_enabled"] = bool(e.value)
                        session_settings.save_settings(state.current_settings, quiet=True)

                    ui.switch(
                        t("settings.web_grounding"),
                        value=bool(state.current_settings.get("web_grounding_enabled")),
                        on_change=_toggle_web_grounding,
                    ).props("dense").classes("mb-4").tooltip(t("settings.web_grounding_hint"))

                    def _build_traditional_chinese_checkbox() -> None:
                        from services.media_transcription import installed_whisper_sizes
                        from services.voice_input import sensevoice_downloaded

                        if sensevoice_downloaded() or installed_whisper_sizes():
                            ui.checkbox(
                                t("settings.traditional_chinese"),
                                value=bool(state.current_settings.get("traditional_chinese", True)),
                                on_change=lambda e: state.current_settings.__setitem__(
                                    "traditional_chinese", bool(e.value)
                                ),
                            ).props("dense").classes("mb-4").tooltip(t("settings.traditional_chinese_tooltip"))

                    def _refresh_traditional_chinese_checkbox() -> None:
                        container = registry.traditional_chinese_container
                        if container is None:
                            return
                        container.clear()
                        with container:
                            _build_traditional_chinese_checkbox()

                    registry.refresh_traditional_chinese_fn = _refresh_traditional_chinese_checkbox
                    registry.traditional_chinese_container = ui.column().classes("w-full gap-0")
                    with registry.traditional_chinese_container:
                        _build_traditional_chinese_checkbox()

                    def _toggle_voice_reply(e) -> None:
                        from services.session import settings as session_settings
                        from services.voice_reply import resolve_voice_id, voice_reply_needs_pick

                        state.current_settings["voice_reply_enabled"] = bool(e.value)
                        session_settings.save_settings(state.current_settings, quiet=True)
                        tts_voice_row.set_visibility(bool(e.value))
                        tts_rate_row.set_visibility(bool(e.value))
                        if e.value and voice_reply_needs_pick(state.current_settings):
                            from pipeline.gap_handler import offer_voice_reply_installer

                            offer_voice_reply_installer()
                        elif e.value:
                            from ui.components.startup_overlay import warm_tts_voice_background

                            warm_tts_voice_background()

                    ui.switch(
                        t("settings.voice_reply"),
                        value=bool(state.current_settings.get("voice_reply_enabled")),
                        on_change=_toggle_voice_reply,
                    ).props("dense").classes("mb-2").tooltip(t("settings.voice_reply_hint"))
                    ui.label(t("settings.conversation_mode_hint")).classes(
                        "text-xs opacity-70 -mt-1 mb-2"
                    )

                    from services.voice_reply import resolve_voice_id, tts_voice_options

                    voice_on = bool(state.current_settings.get("voice_reply_enabled"))
                    rate_opts = tts_rate_select_options()
                    current_rate = state.current_settings.get("tts_rate") or "+0%"
                    if current_rate not in rate_opts:
                        current_rate = "+0%"
                    current_voice = resolve_voice_id(state.current_settings)
                    state.current_settings["tts_voice_id"] = current_voice
                    with ui.column().classes("w-full") as tts_voice_row:
                        tts_voice_row.set_visibility(voice_on)
                        registry.tts_voice_select = ui.select(
                            tts_voice_options(),
                            label=t("settings.tts_voice"),
                            value=current_voice,
                            on_change=lambda e: state.current_settings.__setitem__(
                                "tts_voice_id", e.value
                            ),
                        ).props(theme_tokens["select_props"]).classes("w-full mb-2")
                        ui.label(t("settings.tts_voice_hint")).classes("text-xs opacity-70 -mt-1 mb-2")

                    with ui.column().classes("w-full") as tts_rate_row:
                        tts_rate_row.set_visibility(voice_on)
                        ui.select(
                            rate_opts,
                            label=t("settings.tts_rate"),
                            value=current_rate,
                            on_change=lambda e: state.current_settings.__setitem__("tts_rate", e.value),
                        ).props(theme_tokens["select_props"]).classes("w-full mb-4")

                    with ui.row().classes("w-full gap-2"):
                        ui.button(t("settings.save_reload"), on_click=on_save_reload).props(
                            "flat color=primary"
                        )

                        def _rerun_setup_wizard() -> None:
                            from ui.components.model_library_panel import _rerun_setup

                            _rerun_setup()

                        ui.button(t("settings.rerun_setup"), on_click=_rerun_setup_wizard).props("flat")

            with ui.tab_panel(tab_roles):
                with ui.column().classes(tab_scroll):
                    ui.label(t("settings.advanced_models_blurb")).classes(
                        f"{hint_sm} {theme_tokens['muted']} mb-3 leading-snug"
                    )
                    ui.label(t("settings.multimodal_defaults")).classes(
                        f"{section_sm} {theme_tokens['muted']} tracking-widest mb-2"
                    )
                    def _on_vision_change(e) -> None:
                        state.current_settings["default_vision_model"] = e.value
                        state.current_settings["default_video_vision_model"] = e.value

                    def _build_vision_select() -> None:
                        vision_opts, vision_val = _vision_model_options()
                        if vision_opts:
                            ui.select(
                                vision_opts,
                                label=t("settings.vision_label"),
                                value=vision_val,
                                on_change=_on_vision_change,
                            ).props(theme_tokens["select_props"]).classes("w-full mb-2").tooltip(
                                t("settings.vision_label_tooltip")
                            )
                        else:
                            ui.label(t("settings.vision_missing_hint")).classes(
                                f"{hint_sm} {theme_tokens['muted']} mb-2"
                            )

                    def _refresh_vision_select() -> None:
                        container = registry.vision_default_select
                        if container is None:
                            return
                        container.clear()
                        with container:
                            _build_vision_select()

                    registry.refresh_vision_select_fn = _refresh_vision_select
                    registry.vision_default_select = ui.column().classes("w-full gap-0")
                    with registry.vision_default_select:
                        _build_vision_select()

                    def _build_image_select() -> None:
                        image_opts, image_val = _image_model_options()
                        if image_opts:
                            ui.select(
                                image_opts,
                                label=t("settings.image_gen_label"),
                                value=image_val,
                                on_change=lambda e: state.current_settings.__setitem__(
                                    "default_image_model", e.value
                                ),
                            ).props(theme_tokens["select_props"]).classes("w-full mb-2").tooltip(
                                t("settings.image_gen_label_tooltip")
                            )
                        else:
                            ui.label(t("settings.image_gen_missing_hint")).classes(
                                f"{hint_sm} {theme_tokens['muted']} mb-2"
                            )

                    def _refresh_image_select() -> None:
                        container = registry.image_default_select
                        if container is None:
                            return
                        container.clear()
                        with container:
                            _build_image_select()

                    registry.refresh_image_select_fn = _refresh_image_select
                    registry.image_default_select = ui.column().classes("w-full gap-0")
                    with registry.image_default_select:
                        _build_image_select()

                    def _build_whisper_select() -> None:
                        from services.media_transcription import (
                            installed_whisper_sizes,
                            normalize_whisper_model,
                        )

                        installed_sizes = installed_whisper_sizes()
                        if installed_sizes:
                            current_whisper = normalize_whisper_model(
                                state.current_settings.get("default_whisper_model") or installed_sizes[0]
                            )
                            if current_whisper not in installed_sizes:
                                current_whisper = installed_sizes[0]
                            ui.select(
                                installed_sizes,
                                label=t("settings.whisper_model_size"),
                                value=current_whisper,
                                on_change=lambda e: state.current_settings.__setitem__(
                                    "default_whisper_model", e.value
                                ),
                            ).props(theme_tokens["select_props"]).classes("w-full mb-2").tooltip(
                                t("settings.whisper_model_size_tooltip")
                            )
                        else:
                            ui.label(t("settings.whisper_none_installed")).classes(
                                f"{hint_sm} {theme_tokens['muted']} mb-2"
                            )

                    def _refresh_whisper_select() -> None:
                        container = registry.whisper_default_select
                        if container is None:
                            return
                        container.clear()
                        with container:
                            _build_whisper_select()

                    registry.refresh_whisper_select_fn = _refresh_whisper_select
                    registry.whisper_default_select = ui.column().classes("w-full gap-0")
                    with registry.whisper_default_select:
                        _build_whisper_select()
                    voice_langs = {
                        "auto": t("settings.voice_language_auto"),
                        "en": "English",
                        "ms": "Bahasa Melayu",
                        "id": "Bahasa Indonesia",
                        "zh": "中文",
                        "ta": "தமிழ்",
                        "ja": "日本語",
                        "ko": "한국어",
                        "es": "Español",
                        "fr": "Français",
                        "de": "Deutsch",
                        "hi": "हिन्दी",
                        "ar": "العربية",
                    }
                    ui.select(
                        voice_langs,
                        label=t("settings.voice_language"),
                        value=(state.current_settings.get("default_voice_language") or "auto"),
                        on_change=lambda e: state.current_settings.__setitem__("default_voice_language", e.value),
                    ).props(theme_tokens["select_props"]).classes("w-full mb-2").tooltip(
                        t("settings.voice_language_tooltip")
                    )

                    ui.label(t("settings.model_roles")).classes(
                        f"{section_sm} {theme_tokens['muted']} tracking-widest mb-1 mt-2"
                    )
                    ui.label(t("settings.model_roles_routing_hint")).classes(
                        f"{hint_sm} {theme_tokens['muted']} mb-2"
                    )
                    installed = chat_capable_models(get_installed_models())
                    with ui.column().classes("w-full gap-2 mb-4"):
                        suggestions = tokens.ROLE_SUGGESTIONS.get("General", [])
                        model_options = list(installed)
                        raw_val = state.current_settings["assignments"].get("General", "")
                        saved_val = (
                            raw_val.get("label") if isinstance(raw_val, dict) else str(raw_val or "")
                        ).strip()
                        display_val = saved_val
                        if display_val and display_val not in model_options:
                            model_options.append(display_val)
                        if not display_val or display_val not in model_options:
                            display_val = next(
                                (m for m in suggestions if m in model_options),
                                model_options[0] if model_options else "",
                            )

                        if not model_options:
                            ui.label(t("config.no_models")).classes(f"{hint_sm} {theme_tokens['muted']}")
                        else:
                            def _default_model_changed(msg) -> None:
                                from services.session import settings as session_settings

                                handlers.update_role_in_memory("General", msg.value)
                                handlers.update_role_in_memory("Specialist", msg.value)
                                session_settings.save_settings(state.current_settings, quiet=True)

                            registry.default_model_select = ui.select(
                                model_options,
                                value=display_val,
                                on_change=_default_model_changed,
                            ).props(theme_tokens["select_props"]).classes("w-full")

                    ui.button(t("settings.save_reload"), on_click=on_save_reload).props("flat color=primary")

            with ui.tab_panel(tab_model_library):
                with ui.column().classes(tab_scroll):
                    ui.label(t("settings.advanced_tools_blurb")).classes(
                        f"{hint_sm} {theme_tokens['muted']} mb-3 leading-snug"
                    )
                    model_library_container = ui.column().classes("w-full")
                    build_model_library_panel(
                        model_library_container,
                        theme_tokens=theme_tokens,
                        on_save_reload=on_save_reload,
                    )

            with ui.tab_panel(tab_about):
                with ui.column().classes(tab_scroll):
                    with ui.row().classes("items-center gap-2 mb-0"):
                        ui.image(LOMA_ICON_URL).classes("w-8 h-8 rounded shrink-0")
                        with ui.row().classes("items-baseline gap-1.5"):
                            ui.label(t("about.title")).classes("text-lg font-bold tracking-[0.35em]")
                            ui.label(t("about.edition_tag")).classes(
                                f"text-[10px] font-semibold tracking-wide px-1.5 py-0.5 rounded border border-[{theme_tokens['border']}] {theme_tokens['muted']}"
                            )
                    ui.label(t("about.tagline")).classes(
                        f"{section_sm} {theme_tokens['muted']} tracking-widest mb-4"
                    )
                    ui.label(t("about.intro")).classes(f"text-xs leading-relaxed mb-4")
                    ui.label(t("about.cap.heading")).classes(
                        f"{section_sm} {theme_tokens['muted']} tracking-widest mb-2"
                    )
                    for cap_key in (
                        "about.cap.chat",
                        "about.cap.deliverables",
                        "about.cap.multimodal",
                        "about.cap.modes",
                        "about.cap.extensions",
                        "about.cap.local",
                    ):
                        with ui.row().classes("items-start gap-2 mb-2 w-full"):
                            ui.icon("check_circle", size="xs").classes("text-blue-400/80 mt-0.5 shrink-0")
                            ui.label(t(cap_key)).classes(f"text-xs {theme_tokens['muted']} leading-snug")

                    ui.separator().classes("my-3")
                    _build_update_section()

    return settings_dialog


def _build_update_section() -> None:
    from config.app_version import APP_VERSION

    ui.label(t("about.update.version_label").format(version=APP_VERSION)).classes("text-xs mb-2")

    def _open_log_file() -> None:
        from services.app_log import open_log_file

        try:
            open_log_file()
        except Exception:
            ui.notify(t("settings.open_log_file_failed"), color="warning")

    with ui.row().classes("gap-2"):
        ui.button(t("settings.open_log_file"), on_click=_open_log_file).props("outline dense")


