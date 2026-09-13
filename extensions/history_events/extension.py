# -*- coding: utf-8 -*-
"""History Events — real encounters, one question, graded answer."""
from __future__ import annotations

import os
import threading

from nicegui import ui

from extensions.history_events.encounters import ERA_ANY
from extensions.history_events.era_i18n import era_bucket_options
from extensions.history_events.engine import (
    finalize_scores,
    get_state,
    start_encounter,
    submit_answer,
)
from extensions.ludicity_shared.panel import FOOTER_CLS, INTRO_CLS, PANEL_CLS
from pipeline.base.base_extension import BaseExtension
from pipeline.i18n import t as tr
from services.session.chat_post import post_assistant_message

_refresh_meta = None
_refresh_footer = None
_refresh_answer = None
_answer_input = None
_era_select = None
_draft: dict[str, str] = {"answer": ""}

_IMAGE_H = "320px"
_CRITERIA_PANEL = (
    ("engagement", "history_events.criterion_engagement"),
    ("reasoning", "history_events.criterion_reasoning"),
    ("factors", "history_events.criterion_factors"),
    ("historical_fit", "history_events.criterion_fit"),
)


def _ui_refresh() -> None:
    from services.session.workflow_control import schedule_on_ui

    if _refresh_meta:
        schedule_on_ui(_refresh_meta.refresh)
    if _refresh_footer:
        schedule_on_ui(_refresh_footer.refresh)
    if _refresh_answer:
        schedule_on_ui(_refresh_answer.refresh)


def _selected_era() -> str:
    if _era_select is None:
        return get_state().era_filter or ERA_ANY
    val = _era_select.value
    return str(val).strip() if val is not None else ERA_ANY


def _render_panel_scores(grade: str, scores: dict[str, int]) -> None:
    finalized = finalize_scores(scores)
    ui.label(tr("history_events.grade", grade=grade)).classes(
        "text-base font-bold text-amber-300 shrink-0 mt-4"
    )
    total = finalized.get("total", 0)
    ui.label(tr("history_events.total", total=total)).classes(
        "text-[11px] text-amber-200/90 shrink-0 mt-3 mb-2"
    )
    with ui.column().classes("w-full shrink-0 gap-1"):
        for key, label_key in _CRITERIA_PANEL:
            val = finalized.get(key, 0)
            ui.label(
                tr("history_events.criterion_score", label=tr(label_key), score=val)
            ).classes(
                "text-[10px] text-gray-300 shrink-0 leading-snug"
            )


def build_history_events_panel() -> None:
    global _refresh_meta, _refresh_footer, _refresh_answer, _answer_input, _era_select

    def _new_encounter() -> None:
        state = get_state()
        if state.busy:
            return
        era = _selected_era()
        state.era_filter = era
        state.busy = True
        ui.notify(tr("history_events.loading"), color="info")
        _ui_refresh()

        def _work() -> None:
            try:
                start_encounter(state, era_bucket=era)
            except Exception as ex:
                post_assistant_message(f"Could not load encounter: {ex}")
            finally:
                state.busy = False
                _ui_refresh()

        threading.Thread(target=_work, daemon=True).start()

    def _submit() -> None:
        state = get_state()
        if state.busy:
            return
        if not state.encounter:
            ui.notify(tr("history_events.notify_start_first"), color="warning")
            return
        if state.answered:
            ui.notify(tr("history_events.notify_new_for_another"), color="info")
            return
        answer = (_answer_input.value if _answer_input else _draft.get("answer") or "").strip()
        if not answer:
            ui.notify(tr("history_events.notify_write_answer"), color="warning")
            return
        state.busy = True
        _draft["answer"] = ""
        if _answer_input:
            _answer_input.value = ""
        _ui_refresh()

        def _work() -> None:
            try:
                submit_answer(state, answer=answer)
            except Exception as ex:
                post_assistant_message(f"Could not grade answer: {ex}")
            finally:
                state.busy = False
                _ui_refresh()

        threading.Thread(target=_work, daemon=True).start()

    def _set_draft(e) -> None:
        _draft["answer"] = (e.value or "").strip()

    def _set_era(e) -> None:
        val = e.value if e.value is not None else ERA_ANY
        get_state().era_filter = str(val).strip() or ERA_ANY

    with ui.column().classes(PANEL_CLS):
        ui.label(tr("history_events.intro")).classes(INTRO_CLS)

        initial_era = get_state().era_filter or ERA_ANY
        _era_select = ui.select(
            era_bucket_options(),
            label=tr("history_events.era"),
            value=initial_era,
            on_change=_set_era,
        ).props("dense dark standout").classes("w-full shrink-0 px-2")

        with ui.column().classes(
            "w-full flex-1 min-h-0 flex flex-col overflow-hidden gap-1"
        ):
            @ui.refreshable
            def render_meta() -> None:
                state = get_state()
                enc = state.encounter

                # Before the first "New encounter" click there's nothing to show yet —
                # skip the illustration box and status text entirely rather than greeting
                # the user with an empty placeholder frame and "no encounter" messaging.
                if not enc and not state.busy:
                    return

                with ui.element("div").classes(
                    "w-full shrink-0 rounded-lg border border-white/10 overflow-hidden "
                    "bg-black/40 flex items-center justify-center"
                ).style(
                    f"height: {_IMAGE_H}; min-height: {_IMAGE_H}; max-height: {_IMAGE_H};"
                ):
                    if state.image_path and os.path.isfile(state.image_path):
                        ui.image(state.image_path).classes("w-full h-full").style(
                            "object-fit: contain; display: block;"
                        )
                    elif state.busy and enc:
                        ui.label(tr("history_events.loading")).classes(
                            "text-[10px] text-amber-300/90 italic"
                        )
                    else:
                        ui.label(tr("history_events.illustration_hint")).classes(
                            "text-[10px] text-gray-500 italic px-2 text-center"
                        )

                with ui.column().classes("w-full shrink-0 gap-1 px-0.5"):
                    if enc:
                        ui.label(enc.title).classes("text-xs font-bold text-sky-200 shrink-0")
                        ui.label(f"{enc.era} · {enc.category}").classes(
                            "text-[10px] text-gray-400 shrink-0"
                        )
                    else:
                        ui.label(tr("history_events.no_encounter")).classes(
                            "text-xs text-gray-500 italic shrink-0"
                        )

                    if state.answered and state.grade:
                        _render_panel_scores(state.grade, state.score_breakdown)
                    elif state.busy:
                        ui.label(tr("history_events.working")).classes(
                            "text-xs text-amber-300/90 italic shrink-0 mt-2"
                        )
                    elif enc and not state.answered:
                        ui.label(tr("history_events.read_in_chat")).classes(
                            "text-[10px] text-gray-500 shrink-0 mt-2"
                        )
                    elif not enc:
                        ui.label(tr("history_events.click_new")).classes(
                            "text-xs text-gray-500 italic shrink-0 mt-2"
                        )

            render_meta()
            _refresh_meta = render_meta

            @ui.refreshable
            def render_answer() -> None:
                global _answer_input
                state = get_state()
                if state.answered or not state.encounter or state.busy:
                    _answer_input = None
                    return
                _answer_input = ui.textarea(
                    label=tr("history_events.your_answer"),
                    placeholder=tr("history_events.answer_placeholder"),
                    value=_draft.get("answer") or "",
                    on_change=_set_draft,
                ).props("outlined dense dark standout").classes(
                    "w-full flex-1 min-h-0 text-xs px-2"
                ).style("min-height: 80px; resize: none;")

            render_answer()
            _refresh_answer = render_answer

        @ui.refreshable
        def render_footer() -> None:
            state = get_state()
            enc = state.encounter
            with ui.row().classes(FOOTER_CLS):
                ui.button(tr("history_events.new_encounter"), icon="history_edu", on_click=_new_encounter).props(
                    "flat dense no-caps color=primary"
                )
                if enc and not state.answered:
                    ui.button(tr("history_events.submit"), icon="send", on_click=_submit).props(
                        "flat dense no-caps color=primary"
                    )

        render_footer()
        _refresh_footer = render_footer

        def _sync_answer_enabled() -> None:
            state = get_state()
            if _answer_input:
                _answer_input.set_enabled(
                    not state.busy
                    and state.encounter is not None
                    and not state.answered
                )

        _base_ui_refresh = _ui_refresh

        def _ui_refresh_hook() -> None:
            _base_ui_refresh()
            from services.session.workflow_control import schedule_on_ui

            schedule_on_ui(_sync_answer_enabled)

        globals()["_ui_refresh"] = _ui_refresh_hook
        _sync_answer_enabled()


class HistoryEventsExtension(BaseExtension):
    extension_id = "history_events"
    label = "History Events"
    show_in_dropdown = True

    def mount(self, container) -> None:
        with container:
            build_history_events_panel()
