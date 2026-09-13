# -*- coding: utf-8 -*-
"""Shared tier-1/2/3 chat model picker + downloader.

Used by both the first-run setup wizard (ui/components/setup_wizard.py) and the
on-demand "no chat model" dialog (ui/components/capability_installer.py) so a user
sees the same hardware-appropriate recommendations either way, and downloading here
always assigns the starter bundle roles (General/Specialist/Verifier/Orchestrator) the
same way the wizard does.
"""
from __future__ import annotations

import json
import re
import threading
from typing import Callable

from nicegui import ui

import config
from config.model_catalog import (
    hardware_tier,
    model_option_label,
    recommend_starter_optional,
    recommend_tier_dropdown,
)
from pipeline.i18n import t
from services.model_assignments import assign_starter_bundle
from services.providers.registry import get_provider
from services.system.profiler import SystemProfile
from ui.components.asset_downloader import AssetDownloader


def _copy_to_clipboard(text: str) -> None:
    ui.run_javascript(f"navigator.clipboard.writeText({json.dumps(text)})")
    ui.notify(t("config.copied_to_clipboard"), color="positive", timeout=1500)


_SEARCH_TERM_QUALIFIERS = re.compile(r"\b(instruct|chat|vision)\b", re.IGNORECASE)
_SEARCH_TERM_PARENS = re.compile(r"\s*\([^)]*\)")


def _short_search_term(label: str) -> str:
    """Strip qualifiers like "Instruct"/"(2B)" from a catalog label so the copied
    search term is a bare family name (e.g. "Qwen 3.5" not "Qwen 3.5 Instruct
    (2B)") — LM Studio's own Explore search already surfaces size/quant variants
    once you search a family name; baking a size/variant into the term over-narrows
    or mismatches the search there."""
    text = _SEARCH_TERM_PARENS.sub("", label)
    text = _SEARCH_TERM_QUALIFIERS.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def render_lmstudio_suggestions(profile: SystemProfile, provider_label: str) -> None:
    """Hardware-matched "what to search for" suggestions for a backend that can't be
    driven to download anything (LM Studio today). We can't verify a specific
    HuggingFace repo still exists, so we hand the user a family/size search term for
    the backend's own model browser instead of a link that might 404 — provider_id=""
    below deliberately skips the catalog's "provider": "ollama" tag, since that tag
    only matters for whether LOMA can pull the model itself, not whether the family is
    a reasonable thing to search for elsewhere."""
    tier1 = recommend_tier_dropdown(1, profile, provider_id="", limit=1)
    optional = recommend_starter_optional(profile, provider_id="")
    suggestions: list[tuple[str, dict]] = []
    if tier1:
        suggestions.append((t("setup.llm.fast_chat"), tier1[0]))
    if optional.get("balanced"):
        suggestions.append((t("setup.llm.opt_balanced_cb"), optional["balanced"]))
    if optional.get("quality"):
        suggestions.append((t("setup.llm.opt_quality_cb"), optional["quality"]))
    if not suggestions:
        return

    ui.label(t("setup.llm.lmstudio_suggest_intro", label=provider_label)).classes(
        "text-xs font-bold text-gray-500 mt-2 mb-1"
    )
    for tier_label, entry in suggestions:
        search_term = _short_search_term(entry["label"])
        with ui.row().classes("w-full items-center justify-between gap-2 py-1"):
            with ui.column().classes("gap-0"):
                ui.label(tier_label).classes("text-[10px] text-gray-400")
                ui.label(f"{entry['label']} ({entry.get('size', '?')})").classes("text-sm")
            ui.button(
                t("setup.llm.copy_search_term"),
                on_click=lambda term=search_term: _copy_to_clipboard(term),
            ).props("flat dense")
    ui.markdown(t("setup.llm.lmstudio_suggest_steps", label=provider_label)).classes(
        "text-xs text-gray-400 mt-2"
    )
    ui.label(t("setup.llm.lmstudio_quant_hint")).classes("text-[10px] text-gray-400 mt-1")


class ChatModelTierPicker:
    """Renders tier checkboxes and wires a download button that pulls the checked
    models, then assigns them as the starter bundle roles."""

    def __init__(
        self,
        downloader: AssetDownloader,
        *,
        profile: SystemProfile,
        provider_id: str = "",
        on_done: Callable[[], None] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        self._downloader = downloader
        self.profile = profile
        self.provider_id = provider_id
        self.on_done = on_done or (lambda: None)
        self._log = log_fn or (lambda msg: None)
        self._tier1: dict[str, ui.checkbox] = {}
        self._tier2: dict[str, ui.checkbox] = {}
        self._tier3: dict[str, ui.checkbox] = {}
        self.continue_btn: ui.button | None = None
        self._installed_container: ui.column | None = None
        self._installed_select: ui.select | None = None

    @property
    def _provider(self):
        return get_provider(self.provider_id) if self.provider_id else None

    @property
    def supports_remote_pull(self) -> bool:
        """False for a BYOM backend like LM Studio, which has no download endpoint —
        the catalog's tier lists are all Ollama registry tags (e.g. "llava:13b") that
        mean nothing to it, so offering them as a "download" silently no-ops instead
        of installing anything. Render an already-loaded-model picker instead."""
        provider = self._provider
        return provider is None or provider.supports_remote_pull

    @property
    def continue_button_label_key(self) -> str:
        return "setup.download_continue" if self.supports_remote_pull else "setup.llm.use_selected_model"

    def render(self, *, default_tier1: str | None = None) -> None:
        if not self.supports_remote_pull:
            self._installed_container = ui.column().classes("w-full")
            self._render_installed_picker()
            return

        optional = recommend_starter_optional(self.profile, provider_id=self.provider_id)
        hw = hardware_tier(self.profile)

        ui.label(t("setup.llm.fast_chat_hint")).classes("text-[10px] text-gray-400 mb-1")
        ui.label(t("setup.llm.fast_chat")).classes("text-[10px] font-bold text-blue-400 mb-1")
        tier1_pool = recommend_tier_dropdown(1, self.profile, provider_id=self.provider_id, limit=4)
        with ui.column().classes("w-full pl-1 mt-1"):
            for entry in tier1_pool:
                cb = ui.checkbox(
                    model_option_label(entry),
                    value=entry["name"] == default_tier1,
                ).props("dense")
                cb.on_value_change(lambda _e: self._update_continue_enabled())
                self._tier1[entry["name"]] = cb

        if optional:
            ui.label(t("setup.llm.optional_upgrades")).classes("text-xs font-bold text-gray-500 mt-4")
            ui.label(t("setup.llm.optional_upgrades_hint")).classes("text-[10px] text-gray-400 mb-1")
            if optional.get("balanced"):
                tier2_pool = recommend_tier_dropdown(2, self.profile, provider_id=self.provider_id, limit=4)
                default_t2 = (optional.get("balanced") or {}).get("name")
                with ui.column().classes("w-full pl-1 mt-1"):
                    for entry in tier2_pool:
                        cb = ui.checkbox(
                            model_option_label(entry),
                            value=hw >= 2 and entry["name"] == default_t2,
                        ).props("dense")
                        cb.on_value_change(lambda _e: self._update_continue_enabled())
                        self._tier2[entry["name"]] = cb

            if optional.get("quality"):
                tier3_pool = recommend_tier_dropdown(3, self.profile, provider_id=self.provider_id, limit=4)
                default_t3 = (optional.get("quality") or {}).get("name")
                with ui.column().classes("w-full pl-1 mt-1"):
                    for entry in tier3_pool:
                        cb = ui.checkbox(
                            model_option_label(entry),
                            value=hw >= 3 and entry["name"] == default_t3,
                        ).props("dense")
                        cb.on_value_change(lambda _e: self._update_continue_enabled())
                        self._tier3[entry["name"]] = cb

    def _render_installed_picker(self) -> None:
        assert self._installed_container is not None
        self._installed_container.clear()
        label = self._provider.label if self._provider else ""
        with self._installed_container:
            try:
                from services.model_router import chat_capable_models

                installed = chat_capable_models(self._provider.list_models() if self._provider else [])
            except Exception:
                installed = []
            if not installed:
                ui.markdown(t("setup.llm.no_local_models_hint", label=label)).classes(
                    "text-sm text-orange-500 mb-2"
                )
                render_lmstudio_suggestions(self.profile, label)
                ui.button(t("setup.retry"), on_click=self._render_installed_picker).props(
                    "flat dense mt-2"
                )
                return
            ui.label(t("setup.llm.pick_installed", label=label)).classes(
                "text-xs font-bold text-gray-500 mb-1"
            )
            self._installed_select = ui.select(
                options={name: name for name in installed}, value=installed[0]
            ).classes("w-full mt-1")
            self._installed_select.on_value_change(lambda _e: self._update_continue_enabled())
        self._update_continue_enabled()

    def bind_continue_button(self, btn: ui.button) -> None:
        self.continue_btn = btn
        self._update_continue_enabled()

    def _update_continue_enabled(self) -> None:
        if self.continue_btn is None:
            return
        if not self.supports_remote_pull:
            enabled = bool(self._installed_select and self._installed_select.value)
        else:
            enabled = any(
                cb.value for checks in (self._tier1, self._tier2, self._tier3) for cb in checks.values()
            )
        if enabled:
            self.continue_btn.enable()
        else:
            self.continue_btn.disable()

    def chat_model_installed(self) -> bool:
        from services.model_router import chat_capable_models

        try:
            installed = chat_capable_models(
                get_provider(self.provider_id).list_models()
                if self.provider_id
                else config.get_installed_models(force_refresh=False)
            )
        except Exception:
            installed = []
        return bool(installed)

    def _match_installed_name(self, model_name: str, installed: list[str]) -> str:
        if model_name in installed:
            return model_name
        return next((m for m in installed if model_name in m or m in model_name), model_name)

    def download_checked(self, button: ui.button) -> bool:
        """Pulls every checked model, then assigns the starter bundle roles — or, for a
        BYOM backend, assigns whichever already-loaded model was selected (no pull is
        possible). Returns False (and shows a warning) if nothing is selected."""
        if not self.supports_remote_pull:
            return self._assign_selected_installed()

        checked_tier1 = [name for name, cb in self._tier1.items() if cb.value]
        checked_tier2 = [name for name, cb in self._tier2.items() if cb.value]
        checked_tier3 = [name for name, cb in self._tier3.items() if cb.value]
        if not (checked_tier1 or checked_tier2 or checked_tier3):
            ui.notify(t("setup.llm.no_model_selected"), type="warning")
            return False

        general = (checked_tier1 or checked_tier2 or checked_tier3)[0]
        coordinator = checked_tier2[0] if checked_tier2 else None
        orchestrator = checked_tier3[0] if checked_tier3 else None

        to_download: list[str] = []
        for name in checked_tier1 + checked_tier2 + checked_tier3:
            if name not in to_download:
                to_download.append(name)

        def _assign() -> None:
            self._downloader._set_status(t("setup.applying_models"))

            def _work() -> None:
                try:
                    installed = (
                        get_provider(self.provider_id).list_models()
                        if self.provider_id
                        else config.get_installed_models(force_refresh=True)
                    )
                    general_m = self._match_installed_name(general, installed)
                    coord_m = self._match_installed_name(coordinator, installed) if coordinator else None
                    orch_m = self._match_installed_name(orchestrator, installed) if orchestrator else None
                    assign_starter_bundle(general_m, coordinator=coord_m, orchestrator=orch_m)
                    parts = [f"General={general_m}"]
                    if coord_m:
                        parts.append(f"workers={coord_m}")
                    if orch_m:
                        parts.append(f"Orchestrator={orch_m}")
                    self._log(f"Starter bundle: {', '.join(parts)}.")
                except Exception as exc:
                    self._log(f"Role assignment note: {exc}")
                self._downloader._schedule_ui(self.on_done)

            threading.Thread(target=_work, daemon=True).start()

        self._downloader.on_success = _assign
        self._downloader.pull_models_sequence_async(
            to_download,
            button,
            close_dialog_on_success=False,
            notify_on_success=False,
        )
        return True

    def _assign_selected_installed(self) -> bool:
        general = self._installed_select.value if self._installed_select else None
        if not general:
            ui.notify(t("setup.llm.no_model_selected"), type="warning")
            return False

        def _work() -> None:
            try:
                assign_starter_bundle(general)
                self._log(f"Starter bundle ({self._provider.label if self._provider else ''}, "
                          f"already loaded): General={general}.")
            except Exception as exc:
                self._log(f"Role assignment note: {exc}")
            self._downloader._schedule_ui(self.on_done)

        threading.Thread(target=_work, daemon=True).start()
        return True
