# -*- coding: utf-8 -*-
"""First-run setup wizard: connectivity, provider, vision LLM, subsystem assets."""
from __future__ import annotations

import os
import threading
import webbrowser
from typing import Callable

from nicegui import ui

import config
from config.model_catalog import hardware_tier, recommend_three_tiers
from pipeline.i18n import t
from services.bootstrap.connectivity import check_connectivity
from services.providers.registry import detect_providers, get_provider, set_active_provider_id
from services.session import settings as session_settings
from services.session import state
from ui.components.asset_downloader import AssetDownloader, build_subsystem_rows
from ui.components.chat_model_picker import ChatModelTierPicker
from ui.components.hardware_profile_card import build_hardware_profile_card
from services.system.profiler import get_system_profile, refresh_system_profile


BASELINE_LLM_MODEL = "sorc/qwen3.5-instruct:2b"


def _setup_log(msg: str) -> None:
    print(f"[LOMA Setup] {msg}")
    try:
        state.add_log(msg)
    except Exception:
        pass


def _get_setup() -> dict:
    return dict(state.current_settings.get("setup") or {})


def _save_setup(**updates) -> None:
    setup = _get_setup()
    setup.update(updates)
    state.current_settings["setup"] = setup
    session_settings.save_settings(state.current_settings, quiet=True)


class SetupWizard:
    _running = False
    _instance: "SetupWizard | None" = None

    def __init__(self, on_complete: Callable[[], None] | None = None, *, is_rerun: bool = False):
        self.on_complete = on_complete or (lambda: None)
        self.is_rerun = is_rerun
        self.dialog = None
        self.step_container = None
        self.online = True
        self.selected_provider_id = ""
        self.profile = get_system_profile()
        self.progress_bar = None
        self.status_label = None
        self._llm_picker: ChatModelTierPicker | None = None
        self._downloader = AssetDownloader(on_success=self._refresh_assets_step)

    @classmethod
    def maybe_run(cls) -> None:
        setup = _get_setup()
        if setup.get("completed"):
            pid = setup.get("provider")
            if pid and pid != "none":
                set_active_provider_id(str(pid))
            cls._running = False
            return
        if cls._running and not cls._is_stale():
            return
        cls._running = True
        cls._instance = cls()
        cls._instance.start()

    @classmethod
    def _is_stale(cls) -> bool:
        """True when the running wizard's dialog belongs to a different browser session than
        the page asking for it. A page reload (or reconnect) creates a new NiceGUI client and
        the old dialog is unreachable from it, so without this the class-level `_running` flag
        left the new page with no wizard until the app was restarted."""
        try:
            from nicegui import context

            return not cls.is_shown_on(context.client)
        except Exception:
            return cls._instance is None or cls._instance.dialog is None

    @classmethod
    def is_shown_on(cls, client) -> bool:
        """True when the current wizard's dialog is a live, open element of `client`."""
        inst = cls._instance
        dialog = inst.dialog if inst is not None else None
        if dialog is None:
            return False
        try:
            return bool(
                dialog.client is client
                and not dialog.is_deleted
                and dialog.id in client.elements
                and dialog.value
            )
        except Exception:
            return False

    def start(self) -> None:
        _setup_log("Starting first-run setup wizard…")
        try:
            with ui.dialog().props("persistent").classes("z-[10040]") as self.dialog, ui.card().classes(
                "w-[560px] p-6 max-h-[90vh] overflow-y-auto loma-setup-wizard"
            ):
                ui.label(t("setup.title")).classes("text-xl font-bold text-primary")
                self.step_container = ui.column().classes("w-full mt-2")
                self.dialog.open()
                # CI-only: lets the end-to-end test open the wizard straight on the step that
                # installs packages and downloads models (tests/e2e/voice_step_e2e.py).
                e2e_step = os.environ.get("LOMA_E2E_WIZARD_STEP", "")
                if e2e_step == "voice":
                    self._show_voice_input_step()
                elif e2e_step == "voice_reply":
                    self._show_voice_reply_step()
                elif e2e_step == "image":
                    self._show_image_model_step()
                else:
                    self._show_connectivity_step()
        except Exception:
            SetupWizard._running = False
            raise

    def _clear_step(self) -> None:
        if self.step_container:
            self.step_container.clear()

    def _show_connectivity_step(self) -> None:
        self._clear_step()
        with self.step_container:
            ui.label(t("setup.step.connectivity")).classes("text-sm font-bold mt-2")
            ui.label(t("setup.connectivity.checking")).classes("text-xs text-gray-500 mb-2")

        def _load() -> None:
            conn = check_connectivity()

            def _render() -> None:
                self.online = conn.online
                self._clear_step()
                with self.step_container:
                    ui.label(t("setup.step.connectivity")).classes("text-sm font-bold mt-2")
                    if conn.online:
                        ui.markdown(t("setup.connectivity.online")).classes("text-sm text-green-600")
                    else:
                        ui.markdown(t("setup.connectivity.offline")).classes("text-sm text-orange-600")
                    with ui.row().classes("w-full justify-end gap-2 mt-6"):
                        if not conn.online:
                            ui.button(t("setup.retry"), on_click=self._show_connectivity_step).props("flat")
                        ui.button(t("setup.continue"), color="primary", on_click=self._show_provider_step)

            self._schedule_ui(_render)

        threading.Thread(target=_load, daemon=True).start()

    def _show_provider_step(self, *, try_launch: bool = True) -> None:
        self._clear_step()
        with self.step_container:
            ui.label(t("setup.step.provider")).classes("text-sm font-bold mt-2")
            status = t("setup.provider.starting") if try_launch else t("setup.provider.checking")
            status_label = ui.label(status).classes("text-xs text-gray-500 mb-2")

        def _load() -> None:
            if try_launch:
                from services.providers.ollama_launch import try_start_ollama

                ok, launch_status = try_start_ollama()
                _setup_log(f"Ollama launch: {launch_status}")

                def _checking() -> None:
                    status_label.set_text(t("setup.provider.checking"))

                self._schedule_ui(_checking)

            providers = detect_providers(force_refresh=True)
            available = [p for p in providers if p.available]

            def _render() -> None:
                self._clear_step()
                self._render_provider_step(providers, available)

            self._schedule_ui(_render)

        threading.Thread(target=_load, daemon=True).start()

    def _schedule_ui(self, fn: Callable[[], None]) -> None:
        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(fn)

    def _render_provider_step(self, providers, available) -> None:
        with self.step_container:
            self.profile = refresh_system_profile()
            hw_card = ui.column().classes("w-full")
            build_hardware_profile_card(hw_card, self.profile)

            if not available:
                ui.markdown(t("setup.provider.none")).classes("text-sm mb-2")
                ui.markdown(t("setup.provider.retry_hint")).classes("text-xs text-gray-400 mb-3")
                # Only offer a fresh install for a backend LOMA can fully drive (pull
                # models, tune context, etc). A BYOM backend like LM Studio isn't hidden
                # from the app — if it's already on the machine it still shows up
                # normally below once detected — it's just not promoted as something to
                # go install fresh, since picking it today means a materially rougher
                # experience (see setup.provider.lmstudio_caveats).
                downloadable = [
                    p for p in providers
                    if (provider := get_provider(p.provider_id)) and provider.supports_remote_pull
                ]
                with ui.column().classes("gap-2"):
                    for p in downloadable:
                        with ui.row().classes("items-center gap-2"):
                            ui.button(
                                t("setup.provider.download", label=p.label),
                                on_click=lambda url=p.install_url: webbrowser.open(url),
                            ).props("outline dense")
                            if p.provider_id == "ollama":
                                ui.badge(t("setup.provider.recommended"), color="primary").props(
                                    "outline dense"
                                )
                            ui.label(p.install_hint).classes("text-[10px] text-gray-400")
                        if p.error_hint:
                            ui.label(p.error_hint).classes("text-[10px] text-orange-400 ml-1 -mt-1")
                ui.markdown(t("setup.provider.after_download")).classes("text-xs text-gray-400 mt-3")
                non_pull = next(
                    (p for p in providers if (pr := get_provider(p.provider_id)) and not pr.supports_remote_pull),
                    None,
                )
                if non_pull:
                    ui.markdown(t("setup.provider.lmstudio_server_hint", label=non_pull.label)).classes(
                        "text-xs text-gray-400 mt-2"
                    )
                with ui.row().classes("w-full justify-end gap-2 mt-6"):
                    ui.button(
                        t("setup.retry"),
                        on_click=lambda: self._show_provider_step(try_launch=True),
                    ).props("flat")
                    ui.button(t("setup.continue_offline"), on_click=self._finish)
                return

            options = {
                p.provider_id: (
                    f"{p.label} ({p.model_count} models)"
                    + (f" — {t('setup.provider.recommended')}" if p.provider_id == "ollama" else "")
                )
                for p in available
            }
            default = next((p.provider_id for p in available if p.provider_id == "ollama"), available[0].provider_id)
            picker = ui.radio(options, value=default).classes("w-full")
            for p in available:
                provider = get_provider(p.provider_id)
                if provider and not provider.supports_remote_pull:
                    ui.markdown(t("setup.provider.lmstudio_caveats", label=p.label)).classes(
                        "text-xs text-orange-400 mt-1"
                    )
            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(t("setup.skip"), on_click=self._skip_setup).props("flat")
                ui.button(
                    t("setup.continue"),
                    color="primary",
                    on_click=lambda: self._on_provider_chosen(picker.value),
                )

    def _on_provider_chosen(self, provider_id: str) -> None:
        self.selected_provider_id = provider_id
        provider = get_provider(provider_id)
        if provider:
            _save_setup(provider=provider_id, provider_url=provider.base_url)
            set_active_provider_id(provider_id)
            _setup_log(f"Selected provider: {provider.label}")
        self._show_llm_step()

    def _show_llm_step(self) -> None:
        self._clear_step()
        self.profile = refresh_system_profile()

        self._llm_picker = ChatModelTierPicker(
            self._downloader,
            profile=self.profile,
            provider_id=self.selected_provider_id,
            on_done=self._show_voice_input_step,
            log_fn=_setup_log,
        )
        # A BYOM backend (LM Studio) can't be driven to download anything — the picker
        # renders a "pick an already-loaded model" list instead, which works offline
        # and needs none of the catalog/offline framing below.
        needs_download = self._llm_picker.supports_remote_pull

        with self.step_container:
            ui.label(t("setup.step.llm")).classes("text-sm font-bold mt-2")
            ui.markdown(t("setup.vision_llm_blurb")).classes("text-xs text-gray-500 mb-2")
            if needs_download:
                ui.markdown(t("setup.llm.size_advice")).classes("text-xs text-gray-500 mb-2")
                if not self.online:
                    ui.markdown(t("setup.offline_no_download")).classes("text-xs text-orange-500 mb-2")

            self._llm_picker.render(default_tier1=BASELINE_LLM_MODEL)

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label("").classes("text-xs text-gray-500")
            self.status_label.set_visibility(False)
            self._downloader.dialog = self.dialog
            self._downloader.progress_bar = self.progress_bar
            self._downloader.status_label = self.status_label

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                # First-time setup must ensure at least one chat model gets downloaded;
                # Skip only reappears once a model is already installed, or on a rerun.
                if self.is_rerun or self._llm_picker.chat_model_installed():
                    ui.button(t("setup.skip"), on_click=self._show_voice_input_step).props("flat")
                dl_btn = ui.button(t(self._llm_picker.continue_button_label_key), color="primary")
                if needs_download and not self.online:
                    dl_btn.disable()
                dl_btn.on_click(
                    lambda: self._llm_picker.download_checked(dl_btn)
                    if (self.online or not needs_download)
                    else self._show_voice_input_step()
                )
                if self.online or not needs_download:
                    self._llm_picker.bind_continue_button(dl_btn)

    def _show_voice_input_step(self) -> None:
        from services.media_transcription import WHISPER_TRANSCRIPTION_SIZES
        from ui.components.voice_input_installer import install_speech_baseline, speech_baseline_ready

        self._clear_step()
        hw = hardware_tier(self.profile)

        # Live dictation is SenseVoice-only in this edition (see voice_controls.py) — no
        # whisper "fast preview" tier needed anymore, so there's no mandatory baseline.
        # The user picks whichever accurate Whisper size(s) they want for document/audio/
        # video transcription — at least one, any number.
        whisper_labels = {
            "base": t("setup.speech.transcription.base"),
            "small": t("setup.speech.transcription.small"),
            "turbo": t("setup.speech.transcription.turbo"),
            "large": t("setup.speech.transcription.large"),
        }
        whisper_desc = {
            "base": t("setup.speech.transcription.base_desc"),
            "small": t("setup.speech.transcription.small_desc"),
            "turbo": t("setup.speech.transcription.turbo_desc"),
            "large": t("setup.speech.transcription.large_desc"),
        }
        default_whisper = "turbo" if hw >= 3 else ("small" if hw == 2 else "base")

        with self.step_container:
            ui.label(t("setup.step.speech")).classes("text-sm font-bold mt-2")
            ui.markdown(t("setup.speech.blurb")).classes("text-xs text-gray-500 mb-2")
            if not self.online:
                ui.markdown(t("setup.offline_no_download")).classes("text-xs text-orange-500 mb-2")

            ui.label(t("setup.speech.whisper_required_title")).classes(
                "text-[10px] font-bold text-blue-400 mb-1"
            )
            ui.markdown(t("setup.speech.whisper_required_hint")).classes("text-[10px] text-gray-400 mb-1")

            whisper_checks: dict[str, ui.checkbox] = {}
            continue_btn: dict[str, ui.button] = {}

            def _selected_sizes() -> list[str]:
                return [size for size in WHISPER_TRANSCRIPTION_SIZES if whisper_checks[size].value]

            def _update_continue_enabled() -> None:
                btn = continue_btn.get("btn")
                if btn is None:
                    return
                if _selected_sizes():
                    btn.enable()
                else:
                    btn.disable()

            for size in WHISPER_TRANSCRIPTION_SIZES:
                cb = ui.checkbox(whisper_labels[size], value=(size == default_whisper)).props("dense")
                cb.on_value_change(lambda _e: _update_continue_enabled())
                whisper_checks[size] = cb
                ui.label(whisper_desc[size]).classes("text-[10px] text-gray-500 italic ml-6 -mt-1 mb-2")

            ui.separator().classes("my-2")

            ui.label(t("setup.speech.sensevoice_optional_title")).classes(
                "text-[10px] font-bold text-blue-400 mb-1"
            )
            sensevoice_check = ui.checkbox(t("setup.speech.sensevoice_limitation"), value=True).props("dense")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label("").classes("text-xs text-gray-500")
            self.status_label.set_visibility(False)

            def _persist_default_whisper_model(size: str) -> None:
                state.current_settings["default_whisper_model"] = size
                session_settings.save_settings(state.current_settings, quiet=True)

            def _download_speech() -> None:
                sizes = _selected_sizes()
                if not sizes:
                    ui.notify(t("setup.speech.no_model_selected"), type="warning")
                    return
                want_sv = bool(sensevoice_check.value)
                _persist_default_whisper_model(sizes[0])
                if speech_baseline_ready(sizes, want_sensevoice=want_sv):
                    self._show_voice_reply_step()
                    return
                if not self.online:
                    result = check_connectivity()
                    self.online = result.online
                if not self.online:
                    ui.notify(t("setup.offline_no_download"), type="warning")
                    if self.status_label is not None:
                        self.status_label.set_visibility(True)
                        self.status_label.set_text(t("voice.offline_retry"))
                    return
                if self.progress_bar is not None:
                    self.progress_bar.set_visibility(True)
                if self.status_label is not None:
                    self.status_label.set_visibility(True)
                    self.status_label.set_text(t("voice.installing"))
                btn = continue_btn.get("btn")
                if btn is not None:
                    btn.disable()

                def _done(ok: bool, msg: str) -> None:
                    if self.progress_bar is not None:
                        self.progress_bar.set_value(1.0)
                    if ok:
                        self._show_voice_reply_step()
                        return
                    if btn is not None:
                        btn.enable()
                    if msg == "offline":
                        ui.notify(t("setup.offline_no_download"), type="warning")
                        if self.status_label is not None:
                            self.status_label.set_text(t("voice.offline_retry"))
                    else:
                        ui.notify(t("voice.install_failed", error=msg[:120]), type="negative")
                        if self.status_label is not None:
                            self.status_label.set_text(t("voice.install_failed", error=msg[:200]))

                install_speech_baseline(
                    sizes,
                    want_sensevoice=want_sv,
                    on_status=lambda m: self.status_label.set_text(m) if self.status_label else None,
                    on_percent=lambda v: self.progress_bar.set_value(v) if self.progress_bar else None,
                    on_done=_done,
                )

            def _skip_speech() -> None:
                """Skip voice setup — matches every other wizard step's Skip button. Also
                the offline fallback: no download possible without a connection anyway."""
                sizes = _selected_sizes() or [default_whisper]
                _persist_default_whisper_model(sizes[0])
                skipped = list(_get_setup().get("skipped_steps") or [])
                if "speech" not in skipped:
                    skipped.append("speech")
                _save_setup(skipped_steps=skipped)
                self._show_voice_reply_step()

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                if self.is_rerun or speech_baseline_ready(
                    _selected_sizes() or [default_whisper], want_sensevoice=bool(sensevoice_check.value)
                ):
                    ui.button(t("setup.skip"), on_click=_skip_speech).props("flat")
                continue_btn["btn"] = ui.button(
                    t("setup.speech.download_continue"), color="primary", on_click=_download_speech
                )
                _update_continue_enabled()

    def _show_voice_reply_step(self) -> None:
        """Optional — voice reply defaults off and isn't a documented Core Edition
        capability, so this is skippable. Picking a voice here just pre-stages it (a
        one-time ~60MB download); voice reply itself still needs the separate Settings
        toggle. Skipping is fine too: turning that toggle on later, or activating
        conversation mode, prompts this same pick-a-voice flow then."""
        from services.tts_engines import spoken_lang_for_locale, wizard_voice_picks

        self._clear_step()
        from pipeline.i18n import get_locale

        lang = spoken_lang_for_locale(get_locale(state.current_settings))
        picks = wizard_voice_picks(lang)
        self._voice_reply_pick: str | None = None

        with self.step_container:
            ui.label(t("setup.step.voice_reply")).classes("text-sm font-bold mt-2")
            ui.markdown(t("setup.voice_reply.blurb")).classes("text-xs text-gray-500 mb-2")
            if not self.online:
                ui.markdown(t("setup.offline_no_download")).classes("text-xs text-orange-500 mb-2")

            pick_buttons: dict[str, ui.button] = {}

            def _select(gender: str) -> None:
                self._voice_reply_pick = gender
                for g, btn in pick_buttons.items():
                    btn.props(remove="outline" if g == gender else None)
                    btn.props(add=None if g == gender else "outline")

            with ui.row().classes("w-full gap-2 mb-2"):
                for gender, entry in picks.items():
                    label = t(f"setup.voice_reply.{gender}", label=entry["label"], size=entry["size_mb"])
                    btn = ui.button(label, on_click=lambda g=gender: _select(g)).props("outline")
                    pick_buttons[gender] = btn

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label("").classes("text-xs text-gray-500")
            self.status_label.set_visibility(False)

            def _download_voice() -> None:
                gender = self._voice_reply_pick
                if not gender:
                    ui.notify(t("setup.voice_reply.no_pick"), type="warning")
                    return
                if not self.online:
                    ui.notify(t("setup.offline_no_download"), type="warning")
                    return
                entry = picks[gender]
                if self.progress_bar is not None:
                    self.progress_bar.set_visibility(True)
                if self.status_label is not None:
                    self.status_label.set_visibility(True)

                def _done(ok: bool, msg: str) -> None:
                    if ok:
                        self._show_image_model_step()
                    else:
                        ui.notify(t("voice.install_failed", error=msg[:120]), type="negative")
                        if self.status_label is not None:
                            self.status_label.set_text(t("voice.install_failed", error=msg[:200]))

                from ui.components.voice_input_installer import install_voice_reply

                install_voice_reply(
                    entry["id"],
                    on_status=lambda m: self.status_label.set_text(m) if self.status_label else None,
                    on_percent=lambda v: self.progress_bar.set_value(v) if self.progress_bar else None,
                    on_done=_done,
                )

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(t("setup.skip"), on_click=self._show_image_model_step).props("flat")
                ui.button(
                    t("setup.voice_reply.download_continue"), color="primary", on_click=_download_voice
                )

    def _show_image_model_step(self) -> None:
        """Optional — image generation needs at least one local checkpoint downloaded;
        without one, generation silently fails (or auto-downloads on first use, which is
        surprising). Picking any number of models here downloads them in sequence with a
        shared progress bar, same spirit as the LLM tier step; skipping just means the
        user picks one later from Settings → Model Library, or the app auto-downloads
        DreamShaper 8 the first time it's needed."""
        from services.model_router import image_catalog_entries, installed_image_generation_options

        self._clear_step()
        entries = image_catalog_entries()
        already_installed = bool(installed_image_generation_options(state.current_settings)[0])

        with self.step_container:
            ui.label(t("setup.step.image_model")).classes("text-sm font-bold mt-2")
            ui.markdown(t("setup.image_model.blurb")).classes("text-xs text-gray-500 mb-2")
            if not self.online:
                ui.markdown(t("setup.offline_no_download")).classes("text-xs text-orange-500 mb-2")

            checks: dict[str, ui.checkbox] = {}
            for entry in entries:
                name = entry["name"]
                label = f"{entry.get('label', name)} ({entry.get('size', '?')})"
                cb = ui.checkbox(label).props("dense")
                checks[name] = cb
                from services.catalog_i18n import localized_desc

                desc = localized_desc(entry)
                if desc:
                    ui.label(desc).classes("text-[10px] text-gray-500 italic ml-6 -mt-1 mb-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label("").classes("text-xs text-gray-500")
            self.status_label.set_visibility(False)
            self._downloader.dialog = self.dialog
            self._downloader.progress_bar = self.progress_bar
            self._downloader.status_label = self.status_label

            def _download_all(names: list[str], button: ui.button) -> None:
                from services.model_router import download_image_model
                from services.session.workflow_control import schedule_on_ui

                failures: list[str] = []
                for i, name in enumerate(names):
                    self._downloader._set_status(
                        t("setup.image_model.downloading", model=name, current=i + 1, total=len(names))
                    )
                    self._downloader._set_progress(0)
                    try:
                        download_image_model(
                            name, on_percent=lambda v: self._downloader._set_progress(v)
                        )
                    except Exception as exc:
                        failures.append(name)
                        _setup_log(f"Image model download failed for {name}: {exc}")

                def _done() -> None:
                    self.progress_bar.set_value(1.0)
                    if failures:
                        ui.notify(
                            t("setup.image_model.some_failed", models=", ".join(failures)),
                            type="negative",
                        )
                    else:
                        ui.notify(t("assets.download_complete"), type="positive")
                    self._finish()

                schedule_on_ui(_done)

            def _download() -> None:
                names = [name for name, cb in checks.items() if cb.value]
                if not names:
                    ui.notify(t("setup.image_model.no_model_selected"), type="warning")
                    return
                state.current_settings["default_image_model"] = names[0]
                session_settings.save_settings(state.current_settings, quiet=True)
                dl_btn.disable()
                threading.Thread(target=_download_all, args=(names, dl_btn), daemon=True).start()

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                if self.is_rerun or already_installed or not entries:
                    ui.button(t("setup.skip"), on_click=self._finish).props("flat")
                dl_btn = ui.button(t("setup.image_model.download_continue"), color="primary")
                if not self.online or not entries:
                    dl_btn.disable()
                else:
                    dl_btn.on_click(_download)

    def _show_assets_step(self) -> None:
        self._clear_step()
        with self.step_container:
            ui.label(t("setup.step.assets")).classes("text-sm font-bold mt-2")
            ui.markdown(t("setup.assets.blurb")).classes("text-xs text-gray-500 mb-2")
            rows = ui.column().classes("w-full")
            build_subsystem_rows(rows, online=self.online, on_install=self._install_subsystem)

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label("").classes("text-xs text-gray-500")
            self._downloader.dialog = self.dialog
            self._downloader.progress_bar = self.progress_bar
            self._downloader.status_label = self.status_label

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(t("setup.finish"), color="primary", on_click=self._finish)

    def _install_subsystem(self, category: str) -> None:
        if not self.online:
            ui.notify(t("setup.offline_no_download"), type="warning")
            return
        if category == "image_generation":
            tiers = recommend_three_tiers("image_generation", self.profile)
            entry = tiers.get("recommended") or tiers.get("fast")
            if entry:
                state.current_settings["default_image_model"] = entry["name"]
                session_settings.save_settings(state.current_settings, quiet=True)
            self._downloader.pip_install_async(None)

    def _refresh_assets_step(self) -> None:
        self._show_assets_step()

    def _finish(self) -> None:
        _save_setup(completed=True)
        _setup_log("Setup wizard completed.")
        SetupWizard._running = False
        if self.status_label is not None:
            self.status_label.set_text(t("setup.finishing_reload"))
            self.status_label.set_visibility(True)
        if self.progress_bar is not None:
            self.progress_bar.set_value(1.0)
        from services.bootstrap.post_setup import reload_after_setup

        reload_after_setup()
        self.on_complete()

    def _skip_setup(self) -> None:
        _save_setup(completed=True, provider="none", skipped_steps=["all"])
        _setup_log("Setup skipped by user.")
        SetupWizard._running = False
        if self.dialog:
            self.dialog.close()
        from services.bootstrap.post_setup import reload_after_setup

        reload_after_setup()


SetupBootstrap = SetupWizard
