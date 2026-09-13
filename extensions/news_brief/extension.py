# -*- coding: utf-8 -*-
"""News Brief — filtered news digest."""
from __future__ import annotations

import threading
from typing import Any

from nicegui import ui

from pipeline.i18n import t as tr
from extensions.ludicity_shared.panel import FOOTER_BTN_PROPS, FOOTER_CLS
from extensions.news_brief.brief import compose_brief
from extensions.news_brief.options_i18n import (
    localized_news_categories,
    localized_news_countries,
    localized_timeline_options,
)
from extensions.news_brief.queries import (
    build_search_queries,
)
from extensions.news_brief.search import (
    NewsBriefAborted,
    load_news_sources,
    search_news_batch,
)
from pipeline.base.base_extension import BaseExtension

_PANEL = "w-full flex-1 min-h-0 flex flex-col gap-2 min-w-0 overflow-hidden"

_state: dict[str, Any] = {
    "busy": False,
    "log": "",
    "brief_md": "",
    "sources": [],
    "last_filters": {},
    "progress_lines": [],
    "source_rows": [],
    "_run_id": 0,
}


def build_news_brief_panel() -> None:
    with ui.column().classes(_PANEL):
        ui.label(tr("news_brief.intro")).classes("text-[12px] text-gray-400 shrink-0 leading-relaxed")

        cat_sel = ui.select(
            options=localized_news_categories(),
            label=tr("news_brief.categories"),
            multiple=True,
            value=["world", "business"],
        ).props("dense dark standout use-chips").classes("w-full shrink-0")
        country_sel = ui.select(
            options=localized_news_countries(),
            label=tr("news_brief.regions"),
            multiple=True,
            value=["global"],
        ).props("dense dark standout use-chips").classes("w-full shrink-0")
        timeline_sel = ui.select(
            options=localized_timeline_options(),
            label=tr("news_brief.timeline"),
            value="week",
        ).props("dense dark standout").classes("w-full shrink-0")
        topics_in = ui.input(
            tr("news_brief.topics"),
            placeholder=tr("news_brief.topics_placeholder"),
        ).props("dense dark standout").classes("w-full shrink-0")

        def _topics_list() -> list[str]:
            return [t.strip() for t in (topics_in.value or "").split(",") if t.strip()]

        @ui.refreshable
        def progress_panel() -> None:
            lines = _state.get("progress_lines") or []
            if not lines:
                ui.label(tr("news_brief.progress_empty")).classes(
                    "text-[10px] text-gray-500 italic p-2"
                )
                return
            ui.label("\n".join(lines[-40:])).classes(
                "text-[10px] font-mono text-gray-400 leading-snug px-2 whitespace-pre-wrap w-full"
            )
            ui.run_javascript(
                "const el = document.querySelector('.news-brief-progress-scroll');"
                "if (el) el.scrollTop = el.scrollHeight;"
            )

        def _push_progress() -> None:
            progress_panel.refresh()

        def stop_brief() -> None:
            _state["_run_id"] = int(_state.get("_run_id") or 0) + 1
            _state["busy"] = False
            footer_panel.refresh()
            ui.notify(tr("news_brief.stopped"), color="warning")

        def generate_brief() -> None:
            if _state["busy"]:
                return
            run_id = int(_state.get("_run_id") or 0) + 1
            _state["_run_id"] = run_id
            should_abort = lambda: int(_state.get("_run_id") or 0) != run_id
            _state["busy"] = True
            _state["progress_lines"] = []
            _state["source_rows"] = []
            _push_progress()
            footer_panel.refresh()
            from services.session.chat_post import set_assistant_message

            set_assistant_message(tr("news_brief.log_collecting"))
            client = ui.context.client
            cats = list(cat_sel.value or [])
            countries = list(country_sel.value or [])
            topics = _topics_list()
            timeline = timeline_sel.value or "week"
            queries = build_search_queries(cats, countries, topics, timeline=timeline)
            _state["last_filters"] = {
                "categories": cats,
                "countries": countries,
                "topics": topics,
                "timeline": timeline,
            }

            def worker() -> None:
                def log(msg: str) -> None:
                    _state["progress_lines"].append(msg)
                    _state["log"] = msg

                    def upd() -> None:
                        with client:
                            _push_progress()

                    from services.session.workflow_control import schedule_on_ui

                    schedule_on_ui(upd)

                try:
                    from pipeline.state_machine import extension_synthesizing

                    with extension_synthesizing():
                        hits = search_news_batch(
                            queries, max_per_query=4, log_fn=log, should_abort=should_abort
                        )
                        for hit in hits[:12]:
                            _state["source_rows"].append(
                                {
                                    "title": hit.get("title") or hit.get("url") or "Source",
                                    "url": hit.get("url") or "",
                                    "status": "pending",
                                }
                            )
                        log(tr("news_brief.log_found_candidates", count=len(hits)))

                        def on_loaded(src: dict[str, Any]) -> None:
                            for row in _state["source_rows"]:
                                if row.get("url") == src.get("url"):
                                    row["status"] = "loaded"
                                    break
                            else:
                                _state["source_rows"].append(
                                    {
                                        "title": src.get("title") or "Source",
                                        "url": src.get("url") or "",
                                        "status": "loaded",
                                    }
                                )

                            def upd() -> None:
                                with client:
                                    _push_progress()

                            from services.session.workflow_control import schedule_on_ui

                            schedule_on_ui(upd)

                        loaded = load_news_sources(
                            hits,
                            max_sources=10,
                            log_fn=log,
                            on_loaded=on_loaded,
                            should_abort=should_abort,
                        )
                        if not loaded:
                            raise RuntimeError(
                                "No news articles could be loaded. Try different topics or timeline."
                            )
                        md = compose_brief(
                            categories=cats,
                            countries=countries,
                            topics=topics,
                            sources=loaded,
                            timeline=timeline,
                            log_fn=log,
                            stream=True,
                            should_abort=should_abort,
                        )
                    _state["brief_md"] = md
                    _state["sources"] = loaded
                    _state["source_rows"] = [
                        {"title": s.get("title"), "url": s.get("url"), "status": "loaded"}
                        for s in loaded
                    ]
                    log(tr("news_brief.log_streamed"))
                    msg = tr("news_brief.generated")
                    color = "positive"
                except NewsBriefAborted:
                    msg = tr("news_brief.stopped")
                    color = "warning"
                    md = ""
                except Exception as exc:
                    _state["log"] = str(exc)
                    log(str(exc))
                    msg = str(exc)[:160]
                    color = "negative"
                    md = ""

                def done() -> None:
                    if should_abort():
                        return
                    with client:
                        _state["busy"] = False
                        _push_progress()
                        footer_panel.refresh()
                        ui.notify(msg, color=color)

                from services.session.workflow_control import schedule_on_ui

                schedule_on_ui(done)

            threading.Thread(target=worker, daemon=True).start()

        with ui.column().classes(
            "w-full flex-1 min-h-0 border border-white/10 rounded overflow-y-auto "
            "soma-scroll news-brief-progress-scroll p-1"
        ):
            progress_panel()

        @ui.refreshable
        def footer_panel() -> None:
            with ui.row().classes(FOOTER_CLS):
                if _state["busy"]:
                    ui.button(
                        tr("news_brief.stop"), icon="stop", on_click=stop_brief
                    ).props(f"{FOOTER_BTN_PROPS} color=negative")
                else:
                    ui.button(
                        tr("news_brief.generate"), icon="newspaper", on_click=generate_brief
                    ).props(f"{FOOTER_BTN_PROPS} color=primary")

        footer_panel()


class NewsBriefExtension(BaseExtension):
    extension_id = "news_brief"
    label = "News Brief"

    def metadata(self) -> dict:
        base = super().metadata()
        base["description"] = (
            "Choose news categories, regions, and topics; get a summary brief in chat."
        )
        base["category"] = "utility"
        return base

    def mount(self, container) -> None:
        with container:
            build_news_brief_panel()
