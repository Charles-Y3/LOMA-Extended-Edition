# -*- coding: utf-8 -*-
"""Shared asset download helpers for setup wizard and on-demand installers."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

from nicegui import ui

import config
from config.model_catalog import MODEL_CATALOG, SUBSYSTEM_CATEGORIES, catalog_entry, hardware_tag
from pipeline.i18n import t as tr
from services.model_router import image_generation_deps_available
from services.platform_paths import resource_root
from services.providers.registry import detect_providers, get_active_provider
from services.system.profiler import get_system_profile

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(resource_root())
# Image generation deps live in the main requirements.txt now (installed unconditionally
# by run.bat/run.sh). This default is kept as a recovery path for a venv that's missing
# packages (e.g. a user who deleted them manually) — it just reinstalls requirements.txt.
_REQUIREMENTS_IMAGE = _PROJECT_ROOT / "requirements.txt"


def get_system_ram_gb() -> float:
    return get_system_profile().total_ram_gb


def subsystem_asset_status(category: str) -> str:
    """Return Ready | Missing | Planned for a catalog category."""
    entries = MODEL_CATALOG.get(category, [])
    if not entries or entries[0].get("enabled") is False:
        return "Planned"

    if category == "vision":
        from services.model_router import get_vision_capable_models

        return "Ready" if get_vision_capable_models(probe=False) else "Missing"

    if category == "image_generation":
        ok, _ = image_generation_deps_available()
        return "Ready" if ok else "Missing"

    if category == "voice_input":
        from services.media_transcription import whisper_ready
        from services.voice_input import sensevoice_ready

        if whisper_ready() or sensevoice_ready():
            return "Ready"
        return "Missing"

    return "Planned"


class AssetDownloader:
    """Base download UI for Ollama pulls and pip installs."""

    def __init__(self, on_success: Callable[[], None] | None = None):
        self.on_success = on_success or (lambda: None)
        self.dialog = None
        self.progress_bar = None
        self.status_label = None
        self.profile = get_system_profile()

    def _schedule_ui(self, fn: Callable[[], None]) -> None:
        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(fn)

    def _set_status(self, text: str) -> None:
        if self.status_label is not None:
            self._schedule_ui(lambda: self.status_label.set_text(text))

    def _set_progress(self, value: float) -> None:
        if self.progress_bar is not None:
            self._schedule_ui(lambda: self.progress_bar.set_value(value))

    def _scroll_progress_into_view(self) -> None:
        """The progress bar/status label sit once near the top of a (potentially long,
        scrollable) model list — clicking a download button further down leaves them
        off-screen, so a user who scrolled down to find a model has no visible sign
        anything is happening. Scroll them into view the moment a download starts."""
        bar = self.progress_bar
        if bar is None:
            return
        self._schedule_ui(
            lambda: ui.run_javascript(
                f"getHtmlElement('{bar.id}')?.scrollIntoView({{behavior: 'smooth', block: 'center'}})"
            )
        )

    def pull_model_async(
        self,
        model_name: str,
        button: ui.button | None,
        *,
        on_complete_settings_key: str | None = None,
        on_complete: Callable[[], None] | None = None,
        on_ui_complete: Callable[[], None] | None = None,
    ) -> None:
        """`on_complete` runs synchronously in the background thread right after the pull
        succeeds — for state mutation (role assignment, settings save) that must happen
        even if the browser tab disconnected during a long download. `on_ui_complete` is
        best-effort UI feedback (notify/reload), scheduled onto the UI loop afterward."""
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
        self._scroll_progress_into_view()
        threading.Thread(
            target=self._run_pull_thread,
            args=(model_name, on_complete_settings_key, True, True, on_complete, on_ui_complete),
            daemon=True,
        ).start()

    def pull_models_sequence_async(
        self,
        model_names: list[str],
        button: ui.button | None,
        *,
        close_dialog_on_success: bool = False,
        notify_on_success: bool = False,
    ) -> None:
        names = [str(n).strip() for n in model_names if str(n).strip()]
        if not names:
            return
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
        self._scroll_progress_into_view()
        threading.Thread(
            target=self._run_pull_sequence_thread,
            args=(names, close_dialog_on_success, notify_on_success),
            daemon=True,
        ).start()

    def _run_pull_sequence_thread(
        self,
        model_names: list[str],
        close_dialog_on_success: bool,
        notify_on_success: bool,
    ) -> None:
        total = len(model_names)
        try:
            provider = get_active_provider()
            probe = next(
                (p for p in detect_providers(force_refresh=True) if p.provider_id == provider.provider_id),
                None,
            )
            if probe and not probe.available:
                raise RuntimeError(tr("assets.provider_unavailable", label=probe.label))

            for idx, model_name in enumerate(model_names):
                self._set_status(tr("assets.connecting", model=model_name))
                for progress in provider.pull(model_name, stream=True):
                    status = progress.get("status", "")
                    completed = int(progress.get("completed") or 0)
                    chunk_total = int(progress.get("total") or 0)
                    if chunk_total > 0:
                        pct = (idx + completed / chunk_total) / total
                        self._set_progress(pct)
                        self._set_status(
                            tr(
                                "assets.downloading_pct",
                                model=model_name,
                                status=status,
                                pct=round(pct * 100, 1),
                            )
                        )
                    else:
                        self._set_status(
                            tr("assets.pulling_seq", model=model_name, current=idx + 1, total=total)
                            if total > 1
                            else (status or tr("assets.pulling", model=model_name))
                        )

            try:
                from services.model_router import _vision_capability_cache

                _vision_capability_cache.clear()
                config.invalidate_models_cache()
            except Exception:
                pass

            self._set_progress(1.0)
            self._set_status(tr("assets.download_complete"))
            time.sleep(0.8)

            def _finish_ok() -> None:
                if close_dialog_on_success and self.dialog is not None:
                    self.dialog.close()
                if notify_on_success:
                    ui.notify(tr("assets.installed", model=model_names[-1]), type="positive")
                self.on_success()

            self._schedule_ui(_finish_ok)
        except Exception as exc:
            err = str(exc)[:160]

            def _finish_err() -> None:
                self._set_status(tr("assets.error", error=err))
                self._set_progress(0)
                ui.notify(tr("assets.download_failed", error=err), type="negative")

            self._schedule_ui(_finish_err)

    def _run_pull_thread(
        self,
        model_name: str,
        settings_key: str | None,
        close_dialog_on_success: bool = True,
        notify_on_success: bool = True,
        on_complete: Callable[[], None] | None = None,
        on_ui_complete: Callable[[], None] | None = None,
    ) -> None:
        try:
            provider = get_active_provider()
            probe = next(
                (p for p in detect_providers(force_refresh=True) if p.provider_id == provider.provider_id),
                None,
            )
            if probe and not probe.available:
                raise RuntimeError(tr("assets.provider_unavailable", label=probe.label))

            self._set_status(tr("assets.connecting", model=model_name))
            for progress in provider.pull(model_name, stream=True):
                status = progress.get("status", "")
                completed = int(progress.get("completed") or 0)
                total = int(progress.get("total") or 0)
                if total > 0:
                    pct = completed / total
                    self._set_progress(pct)
                    self._set_status(
                        tr(
                            "assets.downloading_pct",
                            model=model_name,
                            status=status,
                            pct=round(pct * 100, 1),
                        )
                    )
                else:
                    self._set_status(status or tr("assets.pulling", model=model_name))

            if settings_key:
                try:
                    from services.model_router import _vision_capability_cache
                    from services.session import settings as session_settings
                    from services.session import state

                    _vision_capability_cache.clear()
                    state.current_settings[settings_key] = model_name
                    session_settings.save_settings(state.current_settings, quiet=True)
                except Exception:
                    pass

            try:
                from services.model_router import _vision_capability_cache

                _vision_capability_cache.clear()
                config.invalidate_models_cache()
            except Exception:
                pass

            # Run the caller's completion logic (role assignment, settings save) HERE, in
            # the worker thread — not scheduled onto the UI loop. A multi-minute download
            # can outlast the browser tab that started it (reconnects, closed tab, a stale
            # client picked up by the UI-thread fallback), so state mutation must not
            # depend on a live UI context to take effect.
            if on_complete is not None:
                try:
                    on_complete()
                except Exception:
                    logger.exception("on_complete callback failed after model pull")

            self._set_status(tr("assets.download_complete"))
            time.sleep(0.8)

            def _finish_ok() -> None:
                if close_dialog_on_success and self.dialog is not None:
                    self.dialog.close()
                if notify_on_success:
                    ui.notify(tr("assets.installed", model=model_name), type="positive")
                if on_ui_complete is not None:
                    try:
                        on_ui_complete()
                    except Exception:
                        logger.exception("on_ui_complete callback failed after model pull")
                self.on_success()

            self._schedule_ui(_finish_ok)
        except Exception as exc:
            err = str(exc)[:160]

            def _finish_err() -> None:
                self._set_status(tr("assets.error", error=err))
                self._set_progress(0)
                ui.notify(tr("assets.download_failed", error=err), type="negative")

            self._schedule_ui(_finish_err)

    def hf_download_async(
        self,
        repo_id: str,
        button: ui.button | None,
        *,
        progress_bar: ui.element | None = None,
        status_label: ui.element | None = None,
    ) -> None:
        """Fetch a HuggingFace checkpoint's weights only (snapshot_download — no pipeline
        load), reusing this class's progress-bar/status-label/thread wiring. Pass explicit
        `progress_bar`/`status_label` to target a card-local pair instead of the shared
        `self.progress_bar`/`self.status_label` (e.g. when another section of the same
        panel already owns those)."""
        bar = progress_bar if progress_bar is not None else self.progress_bar
        label = status_label if status_label is not None else self.status_label
        if button is not None:
            button.disable()
        if bar is not None:
            bar.set_visibility(True)
            self._schedule_ui(
                lambda: ui.run_javascript(
                    f"getHtmlElement('{bar.id}')?.scrollIntoView({{behavior: 'smooth', block: 'center'}})"
                )
            )
        if label is not None:
            label.set_visibility(True)
        threading.Thread(
            target=self._run_hf_download_thread,
            args=(repo_id, bar, label, button),
            daemon=True,
        ).start()

    def _run_hf_download_thread(
        self,
        repo_id: str,
        bar: ui.element | None,
        label: ui.element | None,
        button: ui.button | None,
    ) -> None:
        def _status(text: str) -> None:
            if label is not None:
                self._schedule_ui(lambda: label.set_text(text))

        def _progress(value: float) -> None:
            if bar is not None:
                self._schedule_ui(lambda: bar.set_value(value))

        try:
            from services.model_router import download_image_model

            _status(tr("assets.connecting", model=repo_id))
            _status(tr("assets.pulling", model=repo_id))
            download_image_model(repo_id)
            _progress(1.0)
            _status(tr("assets.download_complete"))
            time.sleep(0.8)

            def _finish_ok() -> None:
                ui.notify(tr("assets.installed", model=repo_id), type="positive")
                self.on_success()

            self._schedule_ui(_finish_ok)
        except Exception as exc:
            err = str(exc)[:160]

            def _finish_err() -> None:
                _status(tr("assets.error", error=err))
                _progress(0)
                ui.notify(tr("assets.download_failed", error=err), type="negative")
                if button is not None:
                    button.enable()

            self._schedule_ui(_finish_err)

    def pip_install_async(self, button: ui.button | None, req_path: Path = _REQUIREMENTS_IMAGE) -> None:
        if button is not None:
            button.disable()
        if self.progress_bar is not None:
            self.progress_bar.set_visibility(True)
        if self.status_label is not None:
            self.status_label.set_visibility(True)
        threading.Thread(target=self._run_pip_thread, args=(req_path,), daemon=True).start()

    def _run_pip_thread(self, req: Path = _REQUIREMENTS_IMAGE) -> None:
        if not req.is_file():
            self._set_status(tr("assets.requirements_missing"))
            return
        try:
            self._set_status(tr("assets.pip_installing"))
            from services.pip_runner import pip_install_requirements

            lines = 0
            tail: list[str] = []

            def _on_line(line: str) -> None:
                nonlocal lines
                lines += 1
                if lines % 8 == 0:
                    self._set_progress(min(0.95, lines / 120))
                self._set_status(line[:80])
                tail.append(line[:160])
                del tail[:-6]

            ok, _output = pip_install_requirements(str(req), on_line=_on_line)
            if not ok:
                # pip's exit code alone tells the user nothing actionable — surface the
                # actual failure reason (missing wheel, network error, permission denied…)
                # from its own output instead.
                detail = " | ".join(tail) or "no output captured"
                raise RuntimeError(f"pip install failed: {detail}")
            self._set_progress(1.0)
            self._set_status(tr("assets.pip_done"))
            time.sleep(0.8)

            def _finish_ok() -> None:
                if self.dialog is not None:
                    self.dialog.close()
                ui.notify(tr("assets.pip_success"), type="positive")
                self.on_success()

            self._schedule_ui(_finish_ok)
        except Exception as exc:
            err = str(exc)[:400]

            def _finish_err() -> None:
                self._set_status(tr("assets.error", error=err))
                ui.notify(tr("assets.pip_failed", error=err), type="negative")

            self._schedule_ui(_finish_err)


def build_subsystem_rows(container, *, online: bool, on_install: Callable[[str], None] | None = None) -> None:
    """Render subsystem status rows inside a NiceGUI container."""
    for cat in SUBSYSTEM_CATEGORIES:
        key = cat["key"]
        label = cat["label"]
        status = subsystem_asset_status(key)
        entries = MODEL_CATALOG.get(key, [])
        enabled = bool(entries and entries[0].get("enabled", True))
        with container:
            with ui.row().classes("w-full items-center justify-between py-1 border-b border-gray-200/20"):
                ui.label(label).classes("text-sm")
                chip_color = {"Ready": "green", "Missing": "orange", "Planned": "gray"}.get(status, "gray")
                ui.badge(status, color=chip_color).props("outline")
                if status == "Missing" and enabled and online:
                    ui.button(
                        "Install",
                        on_click=lambda k=key: on_install(k) if on_install else None,
                    ).props("dense flat size=sm color=primary")
                elif not enabled:
                    ui.label(entries[0].get("badge", "Planned") if entries else "").classes(
                        "text-[10px] text-gray-400"
                    )
