# -*- coding: utf-8 -*-
"""On-demand vision model (Ollama) and image generation (pip) installers."""
from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Callable

from nicegui import ui

from config.model_catalog import MODEL_CATALOG, catalog_entry, entries_for_hardware_tier, model_option_label
from services.model_router import get_default_image_model_from_settings
from services.platform_paths import resource_root
from services.session import state
from services.system.profiler import get_system_profile
from ui.components.asset_downloader import AssetDownloader, get_system_ram_gb
from pipeline.i18n import t as tr
from ui.components.chat_model_picker import ChatModelTierPicker

_PROJECT_ROOT = Path(resource_root())
# Image generation, RAG, and Document Intelligence deps all live in the main
# requirements.txt now (installed unconditionally by run.bat/run.sh). These installer
# dialogs are kept as a recovery path for a venv that's missing packages (e.g. a user
# who deleted them manually), so they just reinstall the full requirements file.
_REQUIREMENTS_RAG = _PROJECT_ROOT / "requirements.txt"
_REQUIREMENTS_DOC_INTEL = _PROJECT_ROOT / "requirements.txt"


class OnDemandInstaller(AssetDownloader):
    def __init__(self, on_success: Callable[[], None] | None = None):
        super().__init__(on_success)
        self.system_ram = get_system_ram_gb()

    def show_vision_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.vision_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.vision_body", ram=self.system_ram)).classes("text-sm text-gray-600 my-2")

            ui.label(tr("installer.vision_select")).classes("text-xs font-bold text-gray-500 mt-4")

            profile = get_system_profile()
            options: dict[str, str] = {}
            default_selection = None
            for m in entries_for_hardware_tier(profile):
                options[m["name"]] = model_option_label(m)
                if default_selection is None:
                    default_selection = m["name"]
            if not default_selection and MODEL_CATALOG.get("vision_llm"):
                default_selection = MODEL_CATALOG["vision_llm"][0]["name"]

            select_element = ui.select(options=options, value=default_selection).classes("w-full mt-1")

            desc_box = ui.markdown("").classes("text-xs text-gray-500 italic mt-2 p-2 bg-gray-50 rounded")

            def update_description(val: str) -> None:
                entry = catalog_entry("vision", val)
                if entry:
                    from services.catalog_i18n import localized_desc

                    desc_box.set_content(tr("installer.vision_details", desc=localized_desc(entry)))

            select_element.on_value_change(lambda e: update_description(e.value))
            update_description(default_selection)

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-6")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_download")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.btn_download_install"), color="primary")
                confirm_btn.on_click(
                    lambda: self.pull_model_async(
                        select_element.value,
                        confirm_btn,
                        on_complete_settings_key="default_vision_model",
                    )
                )

            self.dialog.open()

    def show_chat_model_installer(self) -> None:
        """Shown when chat/an extension has no usable model. Branches on whether a
        local backend (Ollama/LM Studio) is even reachable — the tier picker can't
        help if there's nowhere to download to, so that case offers to install the
        backend instead, same as the first-run wizard's provider step."""
        from pipeline.i18n import t as tr
        from services.providers.registry import detect_providers, get_active_provider_id

        with ui.dialog() as self.dialog, ui.card().classes("w-[520px] p-6"):
            ui.label(tr("chat.install_model_title")).classes("text-xl font-bold text-primary")
            content = ui.column().classes("w-full")
            self.dialog.open()

        def _retry() -> None:
            content.clear()
            with content:
                ui.label(tr("setup.provider.checking")).classes("text-xs text-gray-500 mb-2")

            def _probe() -> None:
                providers = detect_providers(force_refresh=True)

                def _render() -> None:
                    content.clear()
                    with content:
                        available = [p for p in providers if p.available]
                        if not available:
                            self._render_no_provider(providers, _retry)
                        else:
                            provider_id = get_active_provider_id() or available[0].provider_id
                            self._render_chat_tier_picker(provider_id)

                self._schedule_ui(_render)

            threading.Thread(target=_probe, daemon=True).start()

        _retry()

    def _render_no_provider(self, providers, on_retry: Callable[[], None]) -> None:
        from pipeline.i18n import t as tr
        from services.providers.registry import get_provider

        ui.markdown(tr("setup.provider.none")).classes("text-sm mb-2")
        ui.markdown(tr("setup.provider.retry_hint")).classes("text-xs text-gray-400 mb-3")
        # Same reasoning as the setup wizard's provider step: don't promote a fresh
        # install of a BYOM backend LOMA can't fully drive yet — it still shows up
        # normally once detected, just isn't offered as something to go get.
        downloadable = [
            p for p in providers
            if (provider := get_provider(p.provider_id)) and provider.supports_remote_pull
        ]
        with ui.column().classes("gap-2"):
            for p in downloadable:
                with ui.row().classes("items-center gap-2"):
                    ui.button(
                        tr("setup.provider.download", label=p.label),
                        on_click=lambda url=p.install_url: webbrowser.open(url),
                    ).props("outline dense")
                    ui.label(p.install_hint).classes("text-[10px] text-gray-400")
                if p.error_hint:
                    ui.label(p.error_hint).classes("text-[10px] text-orange-400 ml-1 -mt-1")
        with ui.row().classes("w-full justify-end gap-2 mt-6"):
            ui.button(tr("config.cancel"), on_click=self.dialog.close).props("flat")
            ui.button(tr("setup.retry"), on_click=on_retry, color="primary")

    def _render_chat_tier_picker(self, provider_id: str) -> None:
        from pipeline.i18n import t as tr
        from services.providers.registry import get_provider

        picker = ChatModelTierPicker(
            self,
            profile=get_system_profile(),
            provider_id=provider_id,
            on_done=self._finish_chat_model_install,
        )

        active_provider = get_provider(provider_id)
        if active_provider and not active_provider.supports_remote_pull:
            ui.markdown(tr("setup.provider.lmstudio_caveats", label=active_provider.label)).classes(
                "text-xs text-orange-400 mb-2"
            )

        ui.markdown(tr("chat.install_model_blurb", ram=self.system_ram)).classes(
            "text-sm text-gray-600 my-2"
        )
        if picker.supports_remote_pull:
            ui.label(tr("chat.install_model_pick")).classes("text-xs font-bold text-gray-500 mt-4")

        picker.render()

        self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-6")
        self.progress_bar.set_visibility(False)
        self.status_label = ui.label(tr("chat.install_model_ready")).classes("text-xs text-gray-500 mt-1")
        self.status_label.set_visibility(False)

        with ui.row().classes("w-full justify-end gap-2 mt-6"):
            ui.button(tr("config.cancel"), on_click=self.dialog.close).props("flat")
            confirm_btn = ui.button(tr(picker.continue_button_label_key), color="primary")
            confirm_btn.on_click(lambda: picker.download_checked(confirm_btn))
            picker.bind_continue_button(confirm_btn)

    def _finish_chat_model_install(self) -> None:
        if self.dialog is not None:
            self.dialog.close()
        self.on_success()

    def show_image_gen_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.imagegen_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.imagegen_body", ram=self.system_ram)).classes("text-sm text-gray-600 my-2")

            ui.label(tr("installer.imagegen_checkpoint")).classes(
                "text-xs font-bold text-gray-500 mt-4"
            )
            try:
                model_id = get_default_image_model_from_settings(state.current_settings)
            except Exception:
                model_id = MODEL_CATALOG["image_generation"][0]["name"]
            entry = catalog_entry("image_generation", model_id) or MODEL_CATALOG["image_generation"][0]
            ui.markdown(tr("installer.imagegen_checkpoint_line", label=entry["label"], model_id=model_id)).classes(
                "text-xs mb-2"
            )

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_packages")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.btn_install_deps"), color="primary")
                confirm_btn.on_click(lambda: self.pip_install_async(confirm_btn))

            self.dialog.open()

    def show_background_removal_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.cutout_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.cutout_body", ram=self.system_ram)).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_packages")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.btn_install_deps"), color="primary")
                confirm_btn.on_click(lambda: self.pip_install_async(confirm_btn))

            self.dialog.open()

    def show_rag_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.rag_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.rag_body", ram=self.system_ram)).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_packages")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.btn_install_deps"), color="primary")
                confirm_btn.on_click(lambda: self._pip_rag_async(confirm_btn))

            self.dialog.open()

    def _pip_rag_async(self, button: ui.button) -> None:
        prev = self.on_success

        def _warm_then_success() -> None:
            try:
                if self.status_label is not None:
                    self.status_label.set_text(tr("installer.rag_downloading"))
                from services.rag_embeddings import get_embedding_backend, reset_embedding_cache

                reset_embedding_cache()
                get_embedding_backend(force_reload=True)
            except Exception as exc:
                ui.notify(tr("installer.rag_warmup_note", error=exc), type="warning")
            prev()

        self.on_success = _warm_then_success
        self.pip_install_async(button, _REQUIREMENTS_RAG)

    def show_gpu_torch_installer(self) -> None:
        """Offer to replace the CPU-only PyTorch with a CUDA build when a GPU is present
        but unused. GPU-bound work (image gen, voice cloning, vision) then runs on the GPU."""
        profile = get_system_profile()
        with ui.dialog() as self.dialog, ui.card().classes("w-[520px] p-6"):
            ui.label(tr("installer.gpu_title")).classes("text-xl font-bold text-primary")
            vram = f", {profile.vram_gb:g} GB VRAM" if profile.vram_gb else ""
            ui.markdown(tr("installer.gpu_body", gpu=profile.gpu_name or "NVIDIA GPU", vram=vram)).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_install")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.gpu_btn"), color="primary")
                confirm_btn.on_click(lambda: self._pip_gpu_torch_async(confirm_btn))

            self.dialog.open()

    def _pip_gpu_torch_async(self, button: ui.button) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
            self.status_label.set_text(tr("installer.gpu_installing"))
        import threading

        threading.Thread(target=self._run_gpu_torch_install, daemon=True).start()

    def _installed_torch_versions(self) -> dict[str, str]:
        """Base versions (no +local tag) of the installed torch packages, so the CUDA
        rebuild targets the SAME versions and doesn't drift the rest of the stack."""
        out: dict[str, str] = {}
        for pkg in ("torch", "torchvision", "torchaudio"):
            try:
                from importlib.metadata import version

                out[pkg] = version(pkg).split("+")[0]
            except Exception:
                pass
        return out

    def _resolve_cuda_tag(self, torch_version: str) -> str:
        """Pick a CUDA wheel index that actually hosts this torch version. The correct
        tag depends on the torch version (e.g. torch 2.6→cu124, 2.11→cu128, 2.12→cu126),
        so probe candidates instead of hardcoding one. LOMA_TORCH_CUDA overrides."""
        import os

        from services.pip_runner import pip_install

        forced = os.environ.get("LOMA_TORCH_CUDA", "").strip()
        candidates = [forced] if forced else ["cu126", "cu128", "cu130", "cu124", "cu121"]
        for tag in candidates:
            if not tag:
                continue
            index = f"https://download.pytorch.org/whl/{tag}"
            ok, _ = pip_install(
                [f"torch=={torch_version}"],
                extra_args=["--dry-run", "--no-deps", "--force-reinstall", "--index-url", index],
            )
            if ok:
                return tag
        return ""

    def _run_gpu_torch_install(self) -> None:
        from services.pip_runner import pip_install

        versions = self._installed_torch_versions()
        torch_ver = versions.get("torch", "")
        if not torch_ver:
            if self.status_label is not None:
                self.status_label.set_text(tr("installer.gpu_no_version"))
            return

        tag = self._resolve_cuda_tag(torch_ver)
        if not tag:
            if self.status_label is not None:
                self.status_label.set_text(tr("installer.gpu_no_wheel", version=torch_ver))
            return

        index = f"https://download.pytorch.org/whl/{tag}"
        # Reinstall the SAME versions as CUDA builds; --no-deps keeps the rest of the
        # dependency stack (diffusers/transformers/…) untouched.
        specs = [f"{pkg}=={ver}" for pkg, ver in versions.items()]
        try:
            ok, output = pip_install(
                specs, extra_args=["--force-reinstall", "--no-deps", "--index-url", index]
            )
            if not ok:
                raise RuntimeError(output[-300:] or "pip install failed")
            if self.status_label is not None:
                self.status_label.set_text(tr("installer.gpu_done", tag=tag))
            try:
                from services.system.profiler import refresh_system_profile

                refresh_system_profile()
            except Exception:
                pass
            self.on_success()
        except Exception as exc:
            if self.status_label is not None:
                self.status_label.set_text(tr("installer.failed", error=str(exc)[:200]))
        finally:
            if self.progress_bar is not None:
                self.progress_bar.set_value(1.0)

    def start_ollama_pull(self, model_name: str, button_to_disable: ui.button) -> None:
        self.pull_model_async(model_name, button_to_disable, on_complete_settings_key="default_vision_model")

    def start_pip_install(self, button_to_disable: ui.button) -> None:
        self.pip_install_async(button_to_disable)

    def show_transcription_installer(self) -> None:
        self.show_hybrid_whisper_installer()

    def show_voice_reply_installer(self) -> None:
        """Shown when a user turns on voice reply (Settings or conversation mode)
        without ever having picked a Piper voice in the wizard — offers the same
        male/female choice, but Cancel is a real option here since the system voice
        (SAPI/say/espeak) already covers voice reply either way."""
        from pipeline.i18n import get_locale, t as tr
        from services.tts_engines import spoken_lang_for_locale, wizard_voice_picks

        lang = spoken_lang_for_locale(get_locale(state.current_settings or {}))
        picks = wizard_voice_picks(lang)
        self._voice_reply_pick: str | None = None

        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("voice.pick_reply_voice_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("voice.pick_reply_voice_blurb")).classes("text-sm text-gray-600 my-2")

            pick_buttons: dict[str, ui.button] = {}

            def _select(gender: str) -> None:
                self._voice_reply_pick = gender
                for g, btn in pick_buttons.items():
                    btn.props(remove="outline" if g == gender else None)
                    btn.props(add=None if g == gender else "outline")

            with ui.row().classes("w-full gap-2 mb-2"):
                for gender, entry in picks.items():
                    label = tr(f"setup.voice_reply.{gender}", label=entry["label"], size=entry["size_mb"])
                    btn = ui.button(label, on_click=lambda g=gender: _select(g)).props("outline")
                    pick_buttons[gender] = btn

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label("").classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("voice.keep_system_voice"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("setup.voice_reply.download_continue"), color="primary")

                def _confirm() -> None:
                    gender = self._voice_reply_pick
                    if not gender:
                        ui.notify(tr("setup.voice_reply.no_pick"), type="warning")
                        return
                    entry = picks[gender]
                    confirm_btn.disable()
                    self.progress_bar.set_visibility(True)
                    self.status_label.set_visibility(True)

                    def _done(ok: bool, msg: str) -> None:
                        if ok:
                            from ui.layouts.topbar import refresh_voice_select

                            refresh_voice_select()
                            try:
                                self.dialog.close()
                            except Exception:
                                pass
                            self.on_success()
                        else:
                            confirm_btn.enable()
                            self.status_label.set_text(tr("voice.install_failed", error=msg[:200]))

                    from ui.components.voice_input_installer import install_voice_reply

                    install_voice_reply(
                        entry["id"],
                        on_status=self._set_status,
                        on_percent=self._set_progress,
                        on_done=_done,
                    )

                confirm_btn.on_click(_confirm)

            self.dialog.open()

    def show_hybrid_whisper_installer(self) -> None:
        from pipeline.i18n import t as tr

        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("voice.install_whisper_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("voice.install_whisper_blurb")).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("voice.install_ready")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("voice.install_whisper_btn"), color="primary")
                confirm_btn.on_click(lambda: self._pip_hybrid_whisper_async(confirm_btn))

            self.dialog.open()

    def show_sensevoice_installer(self) -> None:
        from pipeline.i18n import t as tr

        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("voice.install_sensevoice_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("voice.install_sensevoice_blurb")).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("voice.install_ready")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("voice.install_sensevoice_btn"), color="primary")
                confirm_btn.on_click(lambda: self._pip_sensevoice_async(confirm_btn))

            self.dialog.open()

    def show_doc_intel_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.docintel_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.docintel_body")).classes("text-sm text-gray-600 my-2")
            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_install")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)
            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.btn_install"), color="primary")
                confirm_btn.on_click(
                    lambda: self.pip_install_async(confirm_btn, _REQUIREMENTS_DOC_INTEL)
                )
            self.dialog.open()

    def show_rembg_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.rembg_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.rembg_body")).classes("text-sm text-gray-600 my-2")
            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_install")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)
            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.rembg_btn"), color="primary")
                confirm_btn.on_click(lambda: self._pip_rembg_async(confirm_btn))
            self.dialog.open()

    def show_ffmpeg_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.ffmpeg_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.ffmpeg_body")).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_install")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.ffmpeg_btn"), color="primary")
                confirm_btn.on_click(lambda: self._pip_ffmpeg_async(confirm_btn))

            self.dialog.open()

    def show_playwright_installer(self) -> None:
        with ui.dialog() as self.dialog, ui.card().classes("w-[500px] p-6"):
            ui.label(tr("installer.pw_title")).classes("text-xl font-bold text-primary")
            ui.markdown(tr("installer.pw_body")).classes("text-sm text-gray-600 my-2")

            self.progress_bar = ui.linear_progress(value=0, show_value=False).classes("w-full mt-4")
            self.progress_bar.set_visibility(False)
            self.status_label = ui.label(tr("installer.ready_install")).classes("text-xs text-gray-500 mt-1")
            self.status_label.set_visibility(False)

            with ui.row().classes("w-full justify-end gap-2 mt-6"):
                ui.button(tr("common.cancel"), on_click=self.dialog.close).props("flat")
                confirm_btn = ui.button(tr("installer.pw_btn"), color="primary")
                confirm_btn.on_click(lambda: self._pip_playwright_async(confirm_btn))

            self.dialog.open()

    def _pip_ffmpeg_async(self, button: ui.button) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
            self.status_label.set_text(tr("installer.ffmpeg_installing"))
        import threading

        threading.Thread(target=self._run_ffmpeg_install, daemon=True).start()

    def _run_ffmpeg_install(self) -> None:
        from services.pip_runner import pip_install

        try:
            ok, output = pip_install(["imageio-ffmpeg"], on_line=self._set_status)
            if not ok:
                raise RuntimeError(output[-300:] or "pip install failed")
            from services.ffmpeg_util import resolve_ffmpeg

            path = resolve_ffmpeg()
            if path:
                try:
                    from services.session import settings as session_settings

                    data = session_settings.load_settings()
                    data["ffmpeg_path"] = path
                    session_settings.save_settings(data)
                except Exception:
                    pass
            if self.status_label is not None:
                self.status_label.set_text(
                    tr("installer.ffmpeg_ready") if path else tr("installer.ffmpeg_missing")
                )
            try:
                if self.dialog is not None:
                    self.dialog.close()
            except Exception:
                pass
            self.on_success()
        except Exception as exc:
            if self.status_label is not None:
                self.status_label.set_text(tr("installer.failed", error=exc))
        finally:
            if self.progress_bar is not None:
                self.progress_bar.set_value(1.0)

    def _pip_playwright_async(self, button: ui.button) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
            self.status_label.set_text(tr("installer.pw_downloading"))
        import threading

        threading.Thread(target=self._run_playwright_install, daemon=True).start()

    def _run_playwright_install(self) -> None:
        from services.pip_runner import run_module

        try:
            # The `playwright` pip package is already a core dependency (requirements.txt);
            # only the browser binary itself needs a separate one-time download.
            ok, output = run_module("playwright", ["install", "chromium"], on_line=self._set_status)
            if not ok:
                raise RuntimeError(output[-300:] or "playwright install failed")
            from services.web_fetch import browser_automation_ready

            ok = browser_automation_ready(force_refresh=True)
            if self.status_label is not None:
                self.status_label.set_text(
                    tr("installer.pw_ready") if ok else tr("installer.pw_not_detected")
                )
            try:
                if self.dialog is not None:
                    self.dialog.close()
            except Exception:
                pass
            self.on_success()
        except Exception as exc:
            if self.status_label is not None:
                self.status_label.set_text(tr("installer.failed", error=exc))
        finally:
            if self.progress_bar is not None:
                self.progress_bar.set_value(1.0)

    def _pip_transcription_async(self, button: ui.button) -> None:
        self._pip_hybrid_whisper_async(button)

    def _pip_hybrid_whisper_async(self, button: ui.button) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
            self.status_label.set_text(tr("installer.whisper_installing"))
        import threading

        threading.Thread(target=self._run_hybrid_whisper_install, daemon=True).start()

    def _run_hybrid_whisper_install(self) -> None:
        from services.pip_runner import pip_install
        from services.session.workflow_control import schedule_on_ui

        try:
            ok, output = pip_install(["faster-whisper"], on_line=self._set_status)
            if not ok:
                raise RuntimeError(output[-300:] or "pip install failed")
            if self.status_label is not None:
                schedule_on_ui(lambda: self.status_label.set_text(tr("installer.whisper_caching")))
            from services.media_transcription import ensure_whisper_model

            ok, detail = ensure_whisper_model()
            if not ok:
                raise RuntimeError(detail or "whisper model ensure failed")

            def _done() -> None:
                if self.status_label is not None:
                    self.status_label.set_text(tr("installer.whisper_ready"))
                try:
                    if self.dialog is not None:
                        self.dialog.close()
                except Exception:
                    pass
                self.on_success()

            schedule_on_ui(_done)
        except Exception as exc:
            # Capture the message now — "except X as exc" unbinds exc as soon as this
            # block exits, but schedule_on_ui defers the lambda until later on the UI
            # loop, so referencing exc there would raise NameError instead of showing
            # the real error.
            err_msg = str(exc)
            if self.status_label is not None:
                schedule_on_ui(lambda: self.status_label.set_text(tr("installer.failed", error=err_msg)))
        finally:
            if self.progress_bar is not None:
                schedule_on_ui(lambda: self.progress_bar.set_value(1.0))

    def _pip_sensevoice_async(self, button: ui.button) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
            self.status_label.set_text(tr("installer.sensevoice_installing"))
        import threading

        threading.Thread(target=self._run_sensevoice_install, daemon=True).start()

    def _run_sensevoice_install(self) -> None:
        from services.pip_runner import pip_install
        from services.session.workflow_control import schedule_on_ui

        try:
            ok, output = pip_install(
                ["funasr", "huggingface_hub", "torchaudio"], on_line=self._set_status
            )
            if not ok:
                raise RuntimeError(output[-300:] or "pip install failed")
            if self.status_label is not None:
                schedule_on_ui(lambda: self.status_label.set_text(tr("installer.sensevoice_downloading")))
            from services.voice_input import ensure_sensevoice_model

            ok, detail = ensure_sensevoice_model()
            if not ok:
                raise RuntimeError(detail or "SenseVoice ensure failed")

            def _done() -> None:
                if self.status_label is not None:
                    self.status_label.set_text(tr("installer.sensevoice_ready"))
                try:
                    if self.dialog is not None:
                        self.dialog.close()
                except Exception:
                    pass
                self.on_success()

            schedule_on_ui(_done)
        except Exception as exc:
            # Capture the message now — "except X as exc" unbinds exc as soon as this
            # block exits, but schedule_on_ui defers the lambda until later on the UI
            # loop, so referencing exc there would raise NameError instead of showing
            # the real error.
            err_msg = str(exc)
            if self.status_label is not None:
                schedule_on_ui(lambda: self.status_label.set_text(tr("installer.failed", error=err_msg)))
        finally:
            if self.progress_bar is not None:
                schedule_on_ui(lambda: self.progress_bar.set_value(1.0))

    def _pip_rembg_async(self, button: ui.button) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
            self.status_label.set_text(tr("installer.rembg_installing"))
        import threading

        threading.Thread(target=self._run_rembg_install, daemon=True).start()

    def _run_rembg_install(self) -> None:
        from services.pip_runner import pip_install
        from services.session.workflow_control import schedule_on_ui

        try:
            ok, output = pip_install(["rembg"], on_line=self._set_status)
            if not ok:
                raise RuntimeError(output[-300:] or "pip install failed")

            def _done() -> None:
                if self.status_label is not None:
                    self.status_label.set_text(tr("installer.rembg_ready"))
                try:
                    if self.dialog is not None:
                        self.dialog.close()
                except Exception:
                    pass
                self.on_success()

            schedule_on_ui(_done)
        except Exception as exc:
            # Capture the message now — "except X as exc" unbinds exc as soon as this
            # block exits, but schedule_on_ui defers the lambda until later on the UI
            # loop, so referencing exc there would raise NameError instead of showing
            # the real error.
            err_msg = str(exc)
            if self.status_label is not None:
                schedule_on_ui(lambda: self.status_label.set_text(tr("installer.failed", error=err_msg)))
        finally:
            if self.progress_bar is not None:
                schedule_on_ui(lambda: self.progress_bar.set_value(1.0))

    def _run_transcription_pip(self) -> None:
        self._run_hybrid_whisper_install()
