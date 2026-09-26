# -*- coding: utf-8 -*-
"""Model Library tab: hardware profile + language/image/whisper models in one place —
status, download, delete, and per-model inference tuning all live together here.

Replaces the old split between ui.components.config_panel (Downloads tab) and
ui.components.configuration_panel (Configuration tab)."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

import config
from config.model_catalog import MODEL_CATALOG, model_option_label, recommend_tier_dropdown
from pipeline.i18n import section_label_class, t
from services.inference.defaults import (
    drop_model_inference_settings,
    get_model_inference_settings,
    set_model_inference_settings,
)
from services.media_transcription import (
    WHISPER_TRANSCRIPTION_SIZES,
    _whisper_size_cached,
    evict_whisper_model_cache,
)
from services.model_assignments import (
    _installed_match,
    custom_installed_models,
    delete_ollama_model,
    models_referencing,
)
from services.model_router import (
    delete_cached_hf_model,
    huggingface_repo_cached,
    image_generation_deps_available,
)
from services.providers.registry import detect_providers, get_active_provider_id, get_provider
from services.session import settings as session_settings
from services.session import state
from services.system.profiler import refresh_system_profile
from ui.components.asset_downloader import AssetDownloader
from ui.components.chat_model_picker import render_lmstudio_suggestions
from ui.components.hardware_profile_card import build_hardware_profile_card
from ui.components.voice_input_installer import install_speech_baseline
from services.net_errors import friendly_net_error


_ROLE_LABEL_KEYS = {
    "Orchestrator": "config.orchestrator",
    "Specialist": "config.specialist",
    "General": "config.general",
    "Verifier": "config.verifier",
}


def _friendly_ref_text(refs: list[str]) -> str:
    """models_referencing() returns a mix of role names and raw internal settings
    keys (default_vision_model/default_video_vision_model — see
    assign_starter_bundle in services/model_assignments.py, which sets both to the
    General model) — map both onto translated, user-facing labels instead of
    printing the raw key, and dedupe since the two vision keys are always the
    same model in practice."""
    labels: list[str] = []
    for ref in refs:
        if ref in _ROLE_LABEL_KEYS:
            label = t(_ROLE_LABEL_KEYS[ref])
        elif ref in ("default_vision_model", "default_video_vision_model"):
            label = t("config.vision_default_role")
        else:
            label = ref
        if label not in labels:
            labels.append(label)
    return ", ".join(labels) if labels else t("config.unused")


def _confirm_delete(title: str, body: str, on_confirm: Callable[[], None]) -> None:
    with ui.dialog() as dlg, ui.card().classes("w-[420px] p-6"):
        ui.label(title).classes("text-lg font-bold")
        ui.markdown(body).classes("text-sm mt-2")
        with ui.row().classes("w-full justify-end gap-2 mt-4"):
            ui.button(t("config.cancel"), on_click=dlg.close).props("flat")

            def _go():
                dlg.close()
                on_confirm()

            ui.button(t("config.delete_confirm"), color="negative", on_click=_go)
    dlg.open()


def _render_model_inference_controls(
    model_name: str,
    *,
    theme_tokens: dict,
    muted: str,
    supports_tuning: bool = True,
    provider_label: str = "",
) -> None:
    inf = get_model_inference_settings(model_name)
    thinking_opts = {
        t("config.thinking_off"): False,
        t("config.thinking_on"): True,
    }
    thinking_default = (
        t("config.thinking_on") if inf.get("enable_thinking") else t("config.thinking_off")
    )

    def _persist() -> None:
        session_settings.save_settings(state.current_settings, quiet=True)

    def _on_thinking(e, mn=model_name) -> None:
        set_model_inference_settings(
            mn,
            {**get_model_inference_settings(mn), "enable_thinking": thinking_opts.get(e.value, False)},
        )
        _persist()

    def _on_num_ctx(e, mn=model_name) -> None:
        set_model_inference_settings(
            mn,
            {**get_model_inference_settings(mn), "num_ctx": int(e.value or 4096)},
        )
        _persist()

    def _on_max_tokens(e, mn=model_name) -> None:
        set_model_inference_settings(
            mn,
            {**get_model_inference_settings(mn), "max_tokens": int(e.value or 4096)},
        )
        _persist()

    # None of these three settings have a working per-request equivalent for a
    # backend LOMA doesn't fully drive (LM Studio's OpenAI-compatible API ignores
    # "think", has no way to widen context per-request, and — even though max_tokens
    # itself does technically forward correctly — showing one working control next
    # to two dead ones is more confusing than helpful. All tuning for such a
    # backend happens in its own app instead, so hide all three consistently rather
    # than picking and choosing by what happens to work under the hood.
    if not supports_tuning:
        ui.markdown(t("config.tune_externally_hint", label=provider_label)).classes(
            f"text-xs {muted} mb-2"
        )
        return

    with ui.row().classes("w-full gap-2 flex-nowrap items-end"):
        thinking_sel = ui.select(
            options=list(thinking_opts.keys()),
            value=thinking_default,
            label=t("config.thinking_mode"),
        ).classes("flex-1 min-w-0").props(theme_tokens.get("select_props", "dense dark standout"))
        thinking_sel.tooltip(t("config.thinking_mode_tooltip"))
        thinking_sel.on_value_change(_on_thinking)

        num_ctx_input = ui.number(
            label=t("config.num_ctx"),
            value=int(inf.get("num_ctx", 4096)),
            min=512,
            max=131072,
            step=512,
        ).classes("flex-1 min-w-0").props("dense dark standout")
        num_ctx_input.tooltip(t("config.num_ctx_tooltip"))
        num_ctx_input.on_value_change(_on_num_ctx)

        max_tokens_input = ui.number(
            label=t("config.max_output_tokens"),
            value=int(inf.get("max_tokens", 4096)),
            min=64,
            max=32768,
            step=64,
        ).classes("flex-1 min-w-0").props("dense dark standout")
        max_tokens_input.tooltip(t("config.max_output_tokens_tooltip"))
        max_tokens_input.on_value_change(_on_max_tokens)


def build_model_library_panel(
    container,
    *,
    theme_tokens: dict,
    on_save_reload: Callable[[], None] | None = None,
) -> AssetDownloader:
    """Renders the merged Model Library tab: hardware profile + language/image/whisper
    models, each with status, download, delete, and (for LLMs) inference tuning."""

    def _on_llm_success() -> None:
        from ui.layouts.topbar import refresh_default_model_select, refresh_vision_model_select

        refresh_default_model_select()
        refresh_vision_model_select()
        _refresh_panel(container, theme_tokens, on_save_reload, downloader)

    downloader = AssetDownloader(on_success=_on_llm_success)
    _render_panel(container, theme_tokens, downloader, on_save_reload)
    return downloader


def _refresh_panel(container, theme_tokens, on_save_reload, downloader) -> None:
    container.clear()
    _render_panel(container, theme_tokens, downloader, on_save_reload)


def _delete_llm(name, refs, container, theme_tokens, on_save_reload, downloader) -> None:
    body = t("config.delete_body", model=name)
    if refs:
        body += "\n\n" + t("config.delete_refs", refs=_friendly_ref_text(refs))

    def _run() -> None:
        resolved = _installed_match(name, config.get_installed_models(force_refresh=True)) or name
        ok, msg = delete_ollama_model(resolved)
        if ok:
            drop_model_inference_settings(resolved)
            config.invalidate_models_cache()
            from ui.layouts.topbar import refresh_default_model_select, refresh_vision_model_select

            refresh_default_model_select()
            refresh_vision_model_select()
            ui.notify(t("config.deleted", model=resolved), type="positive")
            _refresh_panel(container, theme_tokens, on_save_reload, downloader)
        else:
            ui.notify(t("config.delete_failed", error=friendly_net_error(msg)), type="negative")

    _confirm_delete(t("config.delete_title"), body, _run)


def _delete_whisper_model(size, container, theme_tokens, on_save_reload, downloader) -> None:
    def _run() -> None:
        ok, msg = delete_cached_hf_model(f"Systran/faster-whisper-{size}")
        if ok:
            evict_whisper_model_cache(size)
            config.invalidate_models_cache()
            ui.notify(t("config.deleted", model=size), type="positive")
            from ui.layouts.topbar import refresh_traditional_chinese_checkbox, refresh_whisper_select

            refresh_whisper_select()
            refresh_traditional_chinese_checkbox()
            _refresh_panel(container, theme_tokens, on_save_reload, downloader)
        else:
            ui.notify(t("config.delete_failed", error=friendly_net_error(msg)), type="negative")

    _confirm_delete(
        t("config.delete_whisper_title"),
        t("config.delete_whisper_body", model=size),
        _run,
    )


def _delete_image_model(repo_id, container, theme_tokens, on_save_reload, downloader) -> None:
    def _run() -> None:
        from services.model_assignments import delete_image_checkpoint

        ok, msg = delete_image_checkpoint(repo_id)
        if ok:
            config.invalidate_models_cache()
            ui.notify(t("config.deleted", model=repo_id), type="positive")
            from ui.layouts.topbar import refresh_image_select

            refresh_image_select()
            _refresh_panel(container, theme_tokens, on_save_reload, downloader)
        else:
            ui.notify(t("config.delete_failed", error=friendly_net_error(msg)), type="negative")

    _confirm_delete(
        t("config.delete_image_title"),
        t("config.delete_image_body", model=repo_id),
        _run,
    )


_QUALITY_MODE_LABEL_KEYS = {
    "low": "config.image_quality_low",
    "high": "config.image_quality_high",
}
_RESOLUTION_PRESET_LABEL_KEYS = {
    "square": "config.image_preset_square",
    "portrait": "config.image_preset_portrait",
    "landscape": "config.image_preset_landscape",
}


def _render_image_model_presets(model_id: str, *, theme_tokens: dict, muted: str) -> None:
    """Quality mode (Low/High) + resolution pickers for one downloaded image checkpoint
    — the concrete step counts and pixel sizes are resolved per model family by
    services.image_generation.resolve_image_presets() at generation time; here we only
    store the chosen preference *name* per model."""
    from services.image_generation import quality_modes_for_model, resolution_presets_for_model
    from services.image_model_prefs import get_image_model_prefs, set_image_model_prefs

    prefs = get_image_model_prefs(model_id)
    quality_options = {
        mode: t(_QUALITY_MODE_LABEL_KEYS[mode], steps=steps)
        for mode, steps in quality_modes_for_model(model_id).items()
    }
    res_options = {k: t(_RESOLUTION_PRESET_LABEL_KEYS[k]) for k in resolution_presets_for_model(model_id)}
    quality_default = prefs["quality_mode"] or "low"
    res_default = prefs["resolution_preset"] or "square"

    with ui.row().classes("w-full items-center gap-2 pl-0.5"):
        # Some models (e.g. FLUX.2 Klein's distilled checkpoint) have no Low/High
        # choice at all — quality_modes_for_model() returns {} for those, so the
        # dropdown is skipped entirely rather than showing an empty/single fake option.
        if quality_options:
            ui.select(
                quality_options,
                label=t("config.image_quality_label"),
                value=quality_default,
                on_change=lambda e, n=model_id: set_image_model_prefs(n, quality_mode=e.value),
            ).props(theme_tokens["select_props"]).classes("flex-1 min-w-0")
        ui.select(
            res_options,
            label=t("config.image_resolution_label"),
            value=res_default,
            on_change=lambda e, n=model_id: set_image_model_prefs(n, resolution_preset=e.value),
        ).props(theme_tokens["select_props"]).classes("flex-1 min-w-0")


def _delete_piper_voice(voice_id, container, theme_tokens, on_save_reload, downloader) -> None:
    def _run() -> None:
        from services.tts_engines import delete_piper_voice

        ok, msg = delete_piper_voice(voice_id)
        if ok:
            ui.notify(t("config.deleted", model=voice_id), type="positive")
            from ui.layouts.topbar import refresh_voice_select

            refresh_voice_select()
            _refresh_panel(container, theme_tokens, on_save_reload, downloader)
        else:
            ui.notify(t("config.delete_failed", error=friendly_net_error(msg)), type="negative")

    _confirm_delete(
        t("config.delete_voice_title"),
        t("config.delete_voice_body", model=voice_id),
        _run,
    )


def _delete_sensevoice_model(container, theme_tokens, on_save_reload, downloader) -> None:
    def _run() -> None:
        from services.voice_input import delete_sensevoice_model

        ok, msg = delete_sensevoice_model()
        if ok:
            ui.notify(t("config.deleted", model="SenseVoice"), type="positive")
            from ui.layouts.topbar import refresh_traditional_chinese_checkbox

            refresh_traditional_chinese_checkbox()
            _refresh_panel(container, theme_tokens, on_save_reload, downloader)
        else:
            ui.notify(t("config.delete_failed", error=friendly_net_error(msg)), type="negative")

    _confirm_delete(
        t("config.delete_sensevoice_title"),
        t("config.delete_sensevoice_body"),
        _run,
    )


def _render_speaker_picker(entry, theme_tokens, muted) -> None:
    """Multi-speaker voices (VCTK: 109 speakers in one file) are downloaded once, then
    configured afterward — same "download, then configure" shape as LLM role assignment.
    Preview streams a small sample clip straight from HF (no download needed to audition
    a speaker) before committing a pick — and calls .play() itself so Preview alone is
    enough to hear it (a manual play-button click after Preview was confusing users into
    thinking every speaker sounded the same, when they'd just never pressed play)."""
    from services.tts_engines import make_voice_id, piper_sample_url

    voice_id = entry["id"]
    prefs = state.current_settings.setdefault("piper_speaker_choices", {})
    current_speaker = int(prefs.get(voice_id, 0))

    with ui.column().classes("w-full gap-1 pl-2 mt-1"):
        with ui.row().classes("w-full items-center gap-2"):
            speaker_input = ui.number(
                label=t("config.voice_speaker_label"),
                value=current_speaker,
                min=0,
                max=entry["num_speakers"] - 1,
                precision=0,
            ).props(theme_tokens["select_props"]).classes("w-32")
            audio_player = ui.audio(piper_sample_url(voice_id, current_speaker)).classes("h-8")

            def _preview() -> None:
                n = int(speaker_input.value or 0)
                audio_player.set_source(piper_sample_url(voice_id, n))
                audio_player.play()

            ui.button(t("config.voice_preview"), on_click=_preview).props("flat dense")

            def _use_this_speaker() -> None:
                from services.session import settings as session_settings
                from ui.layouts.topbar import refresh_voice_select

                n = int(speaker_input.value or 0)
                prefs[voice_id] = n
                state.current_settings["tts_voice_id"] = make_voice_id(voice_id, n)
                session_settings.save_settings(state.current_settings, quiet=True)
                refresh_voice_select()
                ui.notify(t("config.voice_speaker_saved", n=n), type="positive")

            ui.button(t("config.voice_use_speaker"), on_click=_use_this_speaker, color="primary").props("flat dense")
        ui.label(t("config.voice_speaker_hint", count=entry["num_speakers"])).classes(f"text-xs {muted}")
        labels = entry.get("speaker_labels")
        if labels:
            known = ", ".join(f"{gender}={sid}" for gender, sid in labels.items())
            ui.label(t("config.voice_speaker_known", labels=known)).classes(f"text-xs {muted}")


def _render_panel(container, theme_tokens, downloader, on_save_reload) -> None:
    muted = theme_tokens.get("muted", "text-gray-500")
    installed = config.get_installed_models()
    profile = refresh_system_profile()
    providers = detect_providers()
    active_provider_id = get_active_provider_id() or "ollama"
    active_info = next((p for p in providers if p.provider_id == active_provider_id), None)
    active_provider = get_provider(active_provider_id)
    # A BYOM backend (LM Studio) has no download/delete API — the catalog's tier
    # entries are all Ollama registry tags anyway, so offering a "download" button for
    # them here would either silently no-op or (now) raise. Installed models for such
    # a backend still show up below under "Custom models" since they're just whatever
    # config.get_installed_models() returns, regardless of provider.
    supports_pull = active_provider.supports_remote_pull if active_provider else True

    with container:
        build_hardware_profile_card(
            container,
            profile,
            theme_tokens=theme_tokens,
            on_refresh=lambda: _refresh_panel(container, theme_tokens, on_save_reload, downloader),
        )

        with ui.expansion(t("config.language_models")).classes(
            f"w-full border border-white/5 rounded {section_label_class(muted)} mt-3"
        ):
            if not active_info or not active_info.available:
                active_label = active_info.label if active_info else active_provider_id
                ui.markdown(t("assets.provider_unavailable", label=active_label)).classes(
                    "text-sm text-orange-400 mb-3"
                )
                with ui.row().classes("gap-2 mb-2"):
                    ui.button(
                        t("setup.provider.download", label=active_label),
                        on_click=lambda url=(
                            active_info.install_url if active_info else "https://ollama.com/download"
                        ): __import__("webbrowser").open(url),
                    ).props("outline dense color=primary")
                    ui.button(t("setup.retry"), on_click=_rerun_setup).props("flat dense")

            ui.label(t("config.per_model_inference_hint")).classes(f"text-sm {muted} mb-2")
            llm_progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            llm_progress.set_visibility(False)
            llm_status = ui.label("").classes(f"text-sm {muted}")
            llm_status.set_visibility(False)
            downloader.progress_bar = llm_progress
            downloader.status_label = llm_status

            if not supports_pull:
                ui.markdown(
                    t("config.manage_models_externally_hint", label=active_provider.label)
                ).classes(f"text-sm {muted} mb-2")
                ui.markdown(
                    t("setup.provider.lmstudio_caveats", label=active_provider.label)
                ).classes("text-xs text-orange-400 mb-2")
                render_lmstudio_suggestions(profile, active_provider.label)

            for tier in (1, 2, 3):
                tier_pool = recommend_tier_dropdown(tier, profile, limit=0)
                if not tier_pool:
                    continue
                tier_rows = []
                for entry in tier_pool:
                    name = entry["name"]
                    resolved = _installed_match(name, installed)
                    if resolved or supports_pull:
                        tier_rows.append((entry, resolved))
                if not tier_rows:
                    continue
                ui.label(t(f"config.tier_{tier}_label")).classes(
                    "text-sm font-bold text-blue-400 mt-2 mb-1"
                )
                for entry, resolved in tier_rows:
                    name = entry["name"]
                    if resolved:
                        refs = models_referencing(state.current_settings, resolved)
                        ref_text = _friendly_ref_text(refs)
                        with ui.expansion(model_option_label(entry)).classes(
                            "w-full border border-white/5 rounded mb-1"
                        ):
                            with ui.row().classes("w-full items-center justify-between gap-2 mb-2"):
                                ui.label(ref_text).classes(f"text-xs {muted} flex-1")
                                if supports_pull:
                                    ui.button(
                                        icon="delete",
                                        on_click=lambda n=resolved, r=refs: _delete_llm(
                                            n, r, container, theme_tokens, on_save_reload, downloader
                                        ),
                                    ).props("flat dense round color=negative")
                            _render_model_inference_controls(
                                resolved,
                                theme_tokens=theme_tokens,
                                muted=muted,
                                supports_tuning=supports_pull,
                                provider_label=active_provider.label if active_provider else "",
                            )
                    else:
                        with ui.row().classes(
                            "w-full items-center justify-between py-1 border-b border-gray-200/10"
                        ):
                            ui.label(model_option_label(entry)).classes("text-sm")
                            dl_btn = ui.button(icon="download").props("flat dense round color=primary")
                            dl_btn.tooltip(t("config.download_model_tooltip", model=name))
                            dl_btn.on_click(
                                lambda n=name, b=dl_btn: downloader.pull_model_async(n, b)
                            )

            custom = custom_installed_models(installed)
            if custom:
                ui.label(t("config.custom_models")).classes(f"{section_label_class(muted)} mt-3 mb-1")
                # "Text-only or third-party" was written back when "custom" meant a
                # manually-pulled Ollama model — for a BYOM backend like LM Studio,
                # EVERYTHING lands here regardless of true capability (including
                # genuinely vision-capable models), so that framing is actively wrong
                # there. Use a neutral note instead when the active provider isn't
                # Ollama's curated catalog source.
                hint_key = "config.custom_models_hint" if supports_pull else "config.custom_models_hint_generic"
                ui.label(t(hint_key)).classes(f"text-sm {muted} mb-1")
                for name in custom:
                    refs = models_referencing(state.current_settings, name)
                    ref_text = _friendly_ref_text(refs)
                    with ui.expansion(name).classes("w-full border border-white/5 rounded mb-1"):
                        with ui.row().classes("w-full items-center justify-between gap-2 mb-2"):
                            ui.label(ref_text).classes(f"text-xs {muted} flex-1")
                            if supports_pull:
                                ui.button(
                                    icon="delete",
                                    on_click=lambda n=name, r=refs: _delete_llm(
                                        n, r, container, theme_tokens, on_save_reload, downloader
                                    ),
                                ).props("flat dense round color=negative")
                        _render_model_inference_controls(
                            name,
                            theme_tokens=theme_tokens,
                            muted=muted,
                            supports_tuning=supports_pull,
                            provider_label=active_provider.label if active_provider else "",
                        )

        with ui.expansion(t("config.download_image_models")).classes(
            f"w-full border border-white/5 rounded {section_label_class(muted)} mt-3"
        ):
            ui.label(t("config.download_image_models_hint")).classes(f"text-sm {muted} mb-2")
            image_deps_ok, image_missing = image_generation_deps_available()
            if not image_deps_ok:
                ui.markdown(t("config.image_deps_missing", missing=image_missing)).classes(
                    "text-sm text-orange-400 mb-2"
                )
            # Shared progress bar/status label for the whole section — same pattern as
            # the Whisper models below. HuggingFace's snapshot/file download doesn't
            # report byte-level progress through the simple API used here, so this runs
            # indeterminate (like the SenseVoice installer) rather than sitting frozen.
            image_progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            image_progress.set_visibility(False)
            image_status = ui.label("").classes(f"text-sm {muted}")
            image_status.set_visibility(False)
            for image_entry in MODEL_CATALOG.get("image_generation", []):
                if image_entry.get("enabled", True) is False:
                    continue
                image_name = image_entry["name"]
                image_is_cached = huggingface_repo_cached(image_name)
                with ui.column().classes("w-full gap-1 py-1 border-b border-gray-200/10"):
                    with ui.row().classes("w-full items-center justify-between"):
                        with ui.column().classes("gap-0"):
                            ui.label(
                                f"{image_entry.get('label', image_name)} ({image_entry.get('size', '')})"
                            ).classes("text-sm")
                            if image_entry.get("desc"):
                                from services.catalog_i18n import localized_desc

                                ui.label(localized_desc(image_entry)).classes(f"text-xs {muted}")
                        if image_is_cached:
                            with ui.row().classes("items-center gap-1"):
                                ui.badge(t("config.installed_badge"), color="green").props("outline")
                                ui.button(
                                    icon="delete",
                                    on_click=lambda n=image_name: _delete_image_model(
                                        n, container, theme_tokens, on_save_reload, downloader
                                    ),
                                ).props("flat dense round color=negative")
                        else:
                            img_dl_btn = ui.button(icon="download").props("flat dense round color=primary")
                            img_dl_btn.tooltip(t("config.download_model_tooltip", model=image_name))

                            def _download_image(name=image_name, btn=img_dl_btn) -> None:
                                from services.session.workflow_control import schedule_on_ui

                                btn.disable()
                                image_progress.set_visibility(True)
                                image_progress.set_value(0)
                                image_status.set_visibility(True)
                                image_status.set_text(t("assets.connecting", model=name))
                                ui.run_javascript(
                                    f"getHtmlElement('{image_progress.id}')?.scrollIntoView("
                                    "{behavior: 'smooth', block: 'center'})"
                                )

                                def _on_percent(value: float) -> None:
                                    schedule_on_ui(
                                        lambda v=value: (
                                            image_progress.set_value(v),
                                            image_status.set_text(
                                                t("assets.downloading_model_pct", model=name, pct=round(v * 100, 1))
                                            ),
                                        )
                                    )

                                def _run() -> None:
                                    from services.model_router import download_image_model

                                    try:
                                        download_image_model(name, on_percent=_on_percent)
                                        ok, msg = True, ""
                                    except Exception as exc:
                                        ok, msg = False, str(exc)

                                    def _on_ui() -> None:
                                        if ok:
                                            image_progress.set_value(1.0)
                                            image_status.set_text(t("assets.download_complete"))
                                            config.invalidate_models_cache()
                                            ui.notify(t("assets.installed", model=name), type="positive")
                                            from ui.layouts.topbar import refresh_image_select

                                            refresh_image_select()
                                            _refresh_panel(container, theme_tokens, on_save_reload, downloader)
                                        else:
                                            image_progress.set_value(0)
                                            image_status.set_text(t("assets.error", error=friendly_net_error(msg)))
                                            ui.notify(t("assets.download_failed", error=friendly_net_error(msg)), type="negative")
                                            btn.enable()

                                    from services.session.workflow_control import schedule_on_ui

                                    schedule_on_ui(_on_ui)

                                import threading

                                threading.Thread(target=_run, daemon=True).start()

                            img_dl_btn.on_click(_download_image)

                    if image_is_cached:
                        _render_image_model_presets(image_name, theme_tokens=theme_tokens, muted=muted)

        with ui.expansion(t("config.download_whisper_models")).classes(
            f"w-full border border-white/5 rounded {section_label_class(muted)} mt-2"
        ):
            whisper_progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            whisper_progress.set_visibility(False)
            whisper_status = ui.label("").classes(f"text-sm {muted}")
            whisper_status.set_visibility(False)
            for w_size in WHISPER_TRANSCRIPTION_SIZES:
                with ui.row().classes("w-full items-center justify-between py-1 border-b border-gray-200/10"):
                    with ui.column().classes("gap-0"):
                        ui.label(t(f"setup.speech.transcription.{w_size}")).classes("text-sm")
                        ui.label(t(f"setup.speech.transcription.{w_size}_desc")).classes(
                            f"text-xs {muted}"
                        )
                    if _whisper_size_cached(w_size):
                        with ui.row().classes("items-center gap-1"):
                            ui.badge(t("config.installed_badge"), color="green").props("outline")
                            ui.button(
                                icon="delete",
                                on_click=lambda s=w_size: _delete_whisper_model(
                                    s, container, theme_tokens, on_save_reload, downloader
                                ),
                            ).props("flat dense round color=negative")
                    else:
                        w_dl_btn = ui.button(icon="download").props("flat dense round color=primary")
                        w_dl_btn.tooltip(t("config.download_model_tooltip", model=w_size))

                        def _download_whisper_size(size=w_size, btn=w_dl_btn) -> None:
                            btn.disable()
                            whisper_progress.set_visibility(True)
                            whisper_status.set_visibility(True)
                            ui.run_javascript(
                                f"getHtmlElement('{whisper_progress.id}')?.scrollIntoView("
                                "{behavior: 'smooth', block: 'center'})"
                            )

                            def _on_status(msg: str) -> None:
                                whisper_status.set_text(msg)

                            def _on_percent(value: float) -> None:
                                whisper_progress.set_value(value)

                            def _on_done(ok: bool, msg: str) -> None:
                                if ok:
                                    whisper_progress.set_value(1.0)
                                    whisper_status.set_text(t("assets.download_complete"))
                                    ui.notify(t("assets.installed", model=size), type="positive")
                                    from ui.layouts.topbar import (
                                        refresh_traditional_chinese_checkbox,
                                        refresh_whisper_select,
                                    )

                                    refresh_whisper_select()
                                    refresh_traditional_chinese_checkbox()
                                    _refresh_panel(container, theme_tokens, on_save_reload, downloader)
                                else:
                                    whisper_progress.set_value(0)
                                    whisper_status.set_text(t("assets.error", error=friendly_net_error(msg)))
                                    ui.notify(t("assets.download_failed", error=friendly_net_error(msg)), type="negative")
                                    btn.enable()

                            install_speech_baseline(
                                [size], on_status=_on_status, on_percent=_on_percent, on_done=_on_done
                            )

                        w_dl_btn.on_click(_download_whisper_size)

        with ui.expansion(t("setup.speech.sensevoice_optional_title")).classes(
            f"w-full border border-white/5 rounded {section_label_class(muted)} mt-2"
        ):
            from services.voice_input import sensevoice_downloaded

            sv_progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            sv_progress.set_visibility(False)
            # Fixed-height scroll box, not a bare label — same fix as the mic-trigger
            # install dialog (ui/components/voice_input_installer.py): pip/funasr status
            # lines vary wildly in length, and a growing label made this card's height
            # jump around on every status update instead of staying put. Visibility is
            # toggled on the BOX itself, not just the label inside it — toggling only the
            # label left this h-12 (48px) box reserved at all times, showing as a large
            # empty gap under the card whenever it was idle (i.e. always, except mid-download).
            with ui.column().classes("w-full h-12 overflow-y-auto") as sv_status_box:
                sv_status = ui.label("").classes(f"text-sm {muted}")
            sv_status_box.set_visibility(False)
            with ui.row().classes("w-full items-center justify-between py-1"):
                with ui.column().classes("gap-0"):
                    ui.label(t("setup.speech.sensevoice_limitation")).classes(f"text-xs {muted}")
                if sensevoice_downloaded():
                    with ui.row().classes("items-center gap-1"):
                        ui.badge(t("config.installed_badge"), color="green").props("outline")
                        ui.button(
                            icon="delete",
                            on_click=lambda: _delete_sensevoice_model(
                                container, theme_tokens, on_save_reload, downloader
                            ),
                        ).props("flat dense round color=negative")
                else:
                    sv_dl_btn = ui.button(icon="download").props("flat dense round color=primary")
                    sv_dl_btn.tooltip(t("config.download_model_tooltip", model="SenseVoice"))

                    def _download_sensevoice(btn=sv_dl_btn) -> None:
                        from ui.components.voice_input_installer import install_sensevoice

                        btn.disable()
                        sv_progress.set_visibility(True)
                        # The download step (funasr/HF/ModelScope) reports no incremental
                        # byte-level progress — an indeterminate/animated bar at least
                        # shows it's alive instead of sitting frozen at 0% for however
                        # long the download takes (same pattern already used by the
                        # mic-trigger install dialog for this exact reason).
                        sv_progress.props("indeterminate")
                        sv_status_box.set_visibility(True)
                        ui.run_javascript(
                            f"getHtmlElement('{sv_progress.id}')?.scrollIntoView("
                            "{behavior: 'smooth', block: 'center'})"
                        )

                        def _on_status(msg: str) -> None:
                            sv_status.set_text(msg)

                        def _on_done(ok: bool, msg: str) -> None:
                            sv_progress.props(remove="indeterminate")
                            sv_progress.set_value(1.0 if ok else 0)
                            if ok:
                                sv_status.set_text(t("assets.download_complete"))
                                ui.notify(t("assets.installed", model="SenseVoice"), type="positive")
                                from ui.layouts.topbar import refresh_traditional_chinese_checkbox

                                refresh_traditional_chinese_checkbox()
                                _refresh_panel(container, theme_tokens, on_save_reload, downloader)
                            else:
                                sv_status.set_text(t("assets.error", error=friendly_net_error(msg)))
                                ui.notify(t("assets.download_failed", error=friendly_net_error(msg)), type="negative")
                                btn.enable()

                        install_sensevoice(on_status=_on_status, on_done=_on_done)

                    sv_dl_btn.on_click(_download_sensevoice)

        with ui.expansion(t("config.download_voice_models")).classes(
            f"w-full border border-white/5 rounded {section_label_class(muted)} mt-2"
        ):
            from services.tts_engines import (
                PIPER_VOICE_CATALOG,
                SPOKEN_LANGUAGES,
                piper_package_ready,
                piper_voice_path,
            )

            ui.label(t("config.download_voice_models_hint")).classes(f"text-xs {muted} mb-1")
            voice_progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
            voice_progress.set_visibility(False)
            voice_status = ui.label("").classes(f"text-sm {muted}")
            voice_status.set_visibility(False)
            for lang in SPOKEN_LANGUAGES:
                ui.label(t(f"config.voice_lang_{lang}")).classes(f"{section_label_class(muted)} mt-2 mb-1")
                for entry in PIPER_VOICE_CATALOG.get(lang, []):
                    voice_id = entry["id"]
                    installed = piper_voice_path(voice_id) is not None
                    with ui.column().classes("w-full gap-0 py-1 border-b border-gray-200/10"):
                        with ui.row().classes("w-full items-center justify-between"):
                            with ui.column().classes("gap-0"):
                                gender_tag = t(f"config.voice_gender_{entry['gender']}")
                                voice_name = entry["label"].rsplit(" — ", 1)[0]
                                quality_key = entry["hf_subpath"].rsplit("/", 1)[-1]
                                quality_tag = t(f"config.voice_quality_{quality_key}")
                                ui.label(f"{voice_name} — {quality_tag} — {gender_tag}").classes("text-sm")
                            if installed:
                                with ui.row().classes("items-center gap-1"):
                                    ui.badge(t("config.installed_badge"), color="green").props("outline")
                                    ui.button(
                                        icon="delete",
                                        on_click=lambda vid=voice_id: _delete_piper_voice(
                                            vid, container, theme_tokens, on_save_reload, downloader
                                        ),
                                    ).props("flat dense round color=negative")
                            else:
                                v_dl_btn = ui.button(icon="download").props("flat dense round color=primary")
                                v_dl_btn.tooltip(t("config.download_model_tooltip", model=voice_id))

                                def _download_voice(vid=voice_id, btn=v_dl_btn) -> None:
                                    btn.disable()
                                    voice_progress.set_visibility(True)
                                    voice_status.set_visibility(True)
                                    ui.run_javascript(
                                        f"getHtmlElement('{voice_progress.id}')?.scrollIntoView("
                                        "{behavior: 'smooth', block: 'center'})"
                                    )
                                    import threading

                                    def _work() -> None:
                                        from services.session.workflow_control import schedule_on_ui
                                        from services.tts_engines import download_piper_voice

                                        def _status(msg: str) -> None:
                                            schedule_on_ui(lambda m=msg: voice_status.set_text(m))

                                        def _percent(value: float) -> None:
                                            schedule_on_ui(lambda v=value: voice_progress.set_value(v))

                                        ok, detail = True, ""
                                        if not piper_package_ready():
                                            from services.pip_runner import pip_install

                                            _status(t("config.installing_piper_package"))
                                            ok, output = pip_install(["piper-tts[zh]"], on_line=_status)
                                            if not ok:
                                                detail = output[-300:] or "pip install failed"
                                        if ok:
                                            ok, detail = download_piper_voice(
                                                vid, on_progress=_status, on_percent=_percent
                                            )

                                        def _done() -> None:
                                            if ok:
                                                voice_progress.set_value(1.0)
                                                voice_status.set_text(t("assets.download_complete"))
                                                ui.notify(t("assets.installed", model=vid), type="positive")
                                                from ui.layouts.topbar import refresh_voice_select

                                                refresh_voice_select()
                                                _refresh_panel(
                                                    container, theme_tokens, on_save_reload, downloader
                                                )
                                            else:
                                                voice_progress.set_value(0)
                                                voice_status.set_text(t("assets.error", error=friendly_net_error(detail)))
                                                ui.notify(
                                                    t("assets.download_failed", error=friendly_net_error(detail)),
                                                    type="negative",
                                                )
                                                btn.enable()

                                        schedule_on_ui(_done)

                                    threading.Thread(target=_work, daemon=True).start()

                                v_dl_btn.on_click(_download_voice)

                        if installed and entry["num_speakers"] > 1:
                            _render_speaker_picker(entry, theme_tokens, muted)

        with ui.row().classes("w-full gap-2 mt-4"):

            def _save():
                session_settings.save_settings(state.current_settings, quiet=True)
                if on_save_reload:
                    on_save_reload()

            ui.button(t("settings.save_reload"), on_click=_save).props("flat color=primary")


def _rerun_setup() -> None:
    state.current_settings.setdefault("setup", {})["completed"] = False
    from ui.components.setup_wizard import SetupWizard

    SetupWizard(is_rerun=True).start()
