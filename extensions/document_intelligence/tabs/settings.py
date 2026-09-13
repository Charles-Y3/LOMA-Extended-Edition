# -*- coding: utf-8 -*-
"""Advanced settings tab."""
from __future__ import annotations

from nicegui import ui

from extensions.document_intelligence.settings import DEFAULTS, load_settings, save_settings
from extensions.document_intelligence.ui.constants import setting_tooltips
from pipeline.i18n import t as tr


def render_settings_tab() -> None:
    cfg = load_settings()
    tooltips = setting_tooltips()

    with ui.column().classes("w-full gap-3 p-2 overflow-y-auto loma-scroll"):
        ui.label(tr("di.settings_title")).classes("text-sm font-medium text-gray-300")
        ui.label(tr("di.settings_intro")).classes("text-[11px] text-gray-500")

        fields: dict = {}

        def _section(title_key: str, hint_key: str | None = None) -> None:
            ui.separator().classes("my-1")
            ui.label(tr(title_key)).classes("text-[11px] font-bold text-blue-400 tracking-widest uppercase")
            if hint_key:
                ui.label(tr(hint_key)).classes("text-[10px] text-gray-500 -mt-2")

        from config import get_installed_models
        from services.model_router import chat_capable_models

        _section("di.settings.section_model")
        default_opt = tr("di.settings.answer_model_default")
        model_options = {"": default_opt, **{m: m for m in chat_capable_models(get_installed_models())}}
        current_model = str(cfg.get("answer_model") or "")
        if current_model and current_model not in model_options:
            model_options[current_model] = current_model
        fields["answer_model"] = (
            ui.select(
                model_options,
                label=tr("di.settings.answer_model"),
                value=current_model,
            )
            .props("dense outlined dark")
            .classes("w-full text-xs")
            .tooltip(tooltips.get("answer_model", ""))
        )

        def _num(
            key: str,
            label: str,
            *,
            min_v: float = 0,
            max_v: float = 1000,
            step: float = 1,
        ):
            tip = tooltips.get(key, "")
            fields[key] = (
                ui.number(
                    label,
                    value=float(cfg.get(key) or DEFAULTS[key]),
                    min=min_v,
                    max=max_v,
                    step=step,
                )
                .props("dense outlined dark")
                .classes("w-full text-xs")
                .tooltip(tip)
            )

        def _bool(key: str, label: str):
            tip = tooltips.get(key, "")
            fields[key] = (
                ui.checkbox(label, value=bool(cfg.get(key)))
                .classes("text-xs")
                .tooltip(tip)
            )

        _section("di.settings.section_indexing", "di.settings.section_indexing_hint")
        _num("chunk_size_tokens", tr("di.settings.chunk_size_tokens"), min_v=60, max_v=800)
        _num("min_chunk_tokens", tr("di.settings.min_chunk_tokens"), min_v=20, max_v=200)
        _num("min_retrieval_tokens", tr("di.settings.min_retrieval_tokens"), min_v=0, max_v=100)

        _section("di.settings.section_retrieval")
        _num("max_sources", tr("di.settings.max_sources"), min_v=1, max_v=20)
        _num("chunks_per_document", tr("di.settings.chunks_per_document"), min_v=1, max_v=10)
        _num("max_chunks_returned", tr("di.settings.max_chunks_returned"), min_v=5, max_v=200)
        _num("score_cutoff", tr("di.settings.score_cutoff"), min_v=0, max_v=10, step=0.05)
        _num("retrieval_depth", tr("di.settings.retrieval_depth"), min_v=5, max_v=150)
        _num("fast_score_gap_ratio", tr("di.settings.fast_score_gap_ratio"), min_v=1, max_v=5, step=0.1)

        _section("di.settings.section_agent")
        _num("max_agent_iterations", tr("di.settings.max_agent_iterations"), min_v=1, max_v=15)
        _num(
            "agent_confidence_threshold",
            tr("di.settings.agent_confidence_threshold"),
            min_v=0,
            max_v=1,
            step=0.05,
        )

        _section("di.settings.section_display")
        _num("results_display_count", tr("di.settings.results_display_count"), min_v=5, max_v=100)
        _bool("citation_required", tr("di.settings.citation_required"))

        _section("di.settings.section_security")
        fields["document_passwords"] = (
            ui.input(
                tr("di.settings.document_passwords"),
                value=str(cfg.get("document_passwords") or ""),
                password=True,
                password_toggle_button=True,
            )
            .props("dense outlined dark")
            .classes("w-full text-xs")
            .tooltip(tooltips.get("document_passwords", ""))
        )

        with ui.row().classes("w-full justify-end pt-2 gap-2"):
            def _restore_defaults() -> None:
                for key, widget in fields.items():
                    widget.value = DEFAULTS[key]
                ui.notify(tr("di.settings_restore"), color="info")

            ui.button(tr("di.settings_restore_btn"), on_click=_restore_defaults).props("flat dense no-caps")
            ui.button(tr("common.save"), on_click=lambda: _save(fields, cfg)).props(
                "flat dense no-caps color=primary"
            )


def _save(fields: dict, cfg: dict) -> None:
    data = dict(cfg)
    for key, widget in fields.items():
        val = widget.value
        if key == "citation_required":
            data[key] = bool(val)
        elif key in ("document_passwords", "answer_model"):
            data[key] = str(val or "")
        else:
            data[key] = (
                int(val)
                if key.endswith("_tokens")
                or key
                in (
                    "chunks_per_document",
                    "max_sources",
                    "max_chunks_returned",
                    "retrieval_depth",
                    "max_agent_iterations",
                    "results_display_count",
                )
                else float(val)
            )
    save_settings(data)
    ui.notify(tr("di.settings_saved"), color="positive")
