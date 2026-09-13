# -*- coding: utf-8 -*-
"""Research extension — guided clarify → gather → report."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any

from nicegui import ui

from pipeline.i18n import t as tr
from extensions.research.brief import (
    ResearchBrief,
    estimate_research_seconds,
    format_eta,
    parse_clarify_response,
    rows_to_brief,
)
from extensions.research.clarify_i18n import localize_clarify_row
from extensions.research.llm import author_deliverable, generate_clarify_plan
from extensions.research.runner import ResearchProgress, run_research, save_research_markdown
from extensions.research.sources import ResearchSource, append_references
from pipeline.base.base_extension import BaseExtension
from services.web_fetch_policy import research_mode_notice

_PANEL = "w-full flex-1 min-h-0 flex flex-col gap-2 min-w-0 overflow-hidden"
_SCROLL = "w-full flex-1 soma-scroll min-h-0 overflow-y-auto overflow-x-hidden"

UPLOAD_DIR = os.path.join("data", "uploads", "research")

_state: dict[str, Any] = {
    "screen": "brief",
    "busy": False,
    "prompt": "",
    "clarify_rows": [],
    "brief": None,
    "uploads": [],
    "use_session_uploads": True,
    "log": [],
    "progress": 0.0,
    "phase": "",
    "eta_seconds": 0,
    "started_at": 0.0,
    "result_md": "",
    "notes_md": "",
    "sources_used": [],
    "saved_path": "",
}

_panel_refresh = None
_client_ref = None


def _safe_refresh() -> None:
    from services.session.workflow_control import schedule_on_ui

    client = _client_ref

    def _do() -> None:
        fn = _panel_refresh
        if not fn:
            return
        try:
            if client:
                with client:
                    fn()
            else:
                fn()
        except Exception:
            pass

    schedule_on_ui(_do)


def _session_upload_paths() -> list[tuple[str, str]]:
    from services.session import state

    out: list[tuple[str, str]] = []
    for entry in state.active_context_files or []:
        path = (entry.get("path") or entry.get("filepath") or "").strip()
        name = (entry.get("name") or os.path.basename(path) or "file").strip()
        if path and os.path.isfile(path):
            out.append((path, name))
    return out


def build_research_panel() -> None:
    global _panel_refresh, _client_ref
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    @ui.refreshable
    def render_panel() -> None:
        global _client_ref
        try:
            from nicegui import context

            _client_ref = context.client
        except Exception:
            pass
        with ui.column().classes(_PANEL):
            ui.label(tr("research.intro")).classes("text-[12px] text-gray-400 shrink-0 leading-relaxed pt-1")
            ui.label(research_mode_notice()).classes(
                "text-[12px] text-gray-500 shrink-0 leading-relaxed"
            )
            _render_header()
            screen = _state["screen"]
            if screen == "brief":
                _render_brief_screen(_refresh)
            elif screen == "clarify":
                with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2"):
                    _render_clarify_screen(_refresh)
            elif screen == "running":
                with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2"):
                    _render_running_screen(_refresh)
            elif screen == "results":
                with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2"):
                    _render_results_screen(_refresh)

    def _refresh() -> None:
        render_panel.refresh()

    _panel_refresh = _refresh
    _client_ref = None
    render_panel()


def _render_header() -> None:
    steps = (
        tr("research.step_brief"),
        tr("research.step_clarify"),
        tr("research.step_research"),
        tr("research.step_results"),
    )
    screen = _state["screen"]
    idx = {"brief": 0, "clarify": 1, "running": 2, "results": 3}.get(screen, 0)
    with ui.row().classes("w-full items-center gap-2 shrink-0 flex-wrap"):
        for i, label in enumerate(steps):
            active = i == idx
            ui.label(label).classes(
                "text-[10px] px-2 py-0.5 rounded-full "
                + ("bg-emerald-600/30 text-emerald-200" if active else "bg-white/5 text-gray-400")
            )
            if i < len(steps) - 1:
                ui.label("›").classes("text-gray-600 text-xs")


def _render_brief_screen(refresh) -> None:
    with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2"):
        ui.label(tr("research.what")).classes(
            "text-xs font-medium text-gray-300 shrink-0 w-full"
        )
        prompt = ui.textarea(
            value=_state.get("prompt") or "",
            placeholder=tr("research.placeholder"),
        ).props("outlined dense autogrow=false").classes(
            "w-full text-xs flex-1 min-h-[140px] loma-hint-placeholder"
        ).style("resize: none;")
        prompt.on("update:model-value", lambda e: _state.__setitem__("prompt", _coerce(e)))

        async def on_plan() -> None:
            text = (prompt.value or _state.get("prompt") or "").strip()
            if len(text) < 8:
                ui.notify(tr("research.notify_goal_short"), color="warning")
                return
            _state["prompt"] = text
            _state["busy"] = True
            refresh()
            try:
                raw = await asyncio.to_thread(generate_clarify_plan, text)
                _state["clarify_rows"] = parse_clarify_response(raw, text)
                _state["screen"] = "clarify"
            except Exception as exc:
                ui.notify(tr("research.notify_clarify_failed", error=exc), color="negative")
            finally:
                _state["busy"] = False
                refresh()

        with ui.row().classes("w-full justify-end items-center gap-2 shrink-0"):
            if _state.get("busy"):
                ui.spinner(size="sm")
            ui.button(
                tr("research.plan"),
                on_click=on_plan,
                icon="psychology",
            ).props("flat dense no-caps color=primary")


def _resolve_clarify_option(answer: str, options: list[str]) -> str:
    if not options:
        return (answer or "").strip()
    ans = (answer or "").strip()
    if ans in options:
        return ans
    low = ans.lower()
    for opt in options:
        if opt.lower() == low:
            return opt
    import re

    nums = re.findall(r"\d+", ans)
    if nums:
        for opt in options:
            if nums[0] in opt:
                return opt
    for opt in options:
        if low and (low in opt.lower() or opt.lower() in low):
            return opt
    return options[0]


def _render_clarify_screen(refresh) -> None:
    rows = [localize_clarify_row(r) for r in (_state.get("clarify_rows") or [])]
    field_refs: dict[str, Any] = {}

    with ui.column().classes(_SCROLL + " gap-2 flex-1 min-h-0"):
        ui.label(tr("research.confirm_clarify")).classes(
            "text-[10px] text-gray-400 shrink-0"
        )
        for row in rows:
            rid = row.get("id") or ""
            with ui.column().classes("w-full gap-1 rounded-lg border border-white/10 bg-white/5 p-3"):
                ui.label(str(row.get("question") or row.get("label"))).classes(
                    "text-[11px] font-medium"
                )
                opts = list(row.get("options") or [])
                if opts:
                    opt_map = {o: o for o in opts}
                    sel = ui.select(
                        options=opt_map,
                        value=_resolve_clarify_option(str(row.get("answer") or ""), opts),
                        label=row.get("label"),
                    ).props("dense outlined").classes("w-full text-xs")
                    field_refs[rid] = sel
                else:
                    ta = ui.textarea(value=row.get("answer") or "").props(
                        "outlined dense autogrow"
                    ).classes("w-full text-xs")
                    field_refs[rid] = ta

        include_uploads_cb = ui.checkbox(
            tr("research.include_uploads"),
            value=bool(_state.get("use_session_uploads", True)),
        ).classes("text-[10px] shrink-0")
        include_uploads_cb.on_value_change(
            lambda v: _state.__setitem__("use_session_uploads", bool(v))
        )

    brief_preview = rows_to_brief(rows, original_prompt=_state.get("prompt") or "")
    upload_count = len(_collect_upload_paths())
    eta = estimate_research_seconds(brief_preview, upload_count=upload_count)
    _state["eta_seconds"] = eta

    with ui.row().classes("w-full items-center justify-between gap-2 shrink-0 flex-wrap"):
        ui.label(tr("research.estimated_time", eta=format_eta(eta))).classes("text-[10px] text-emerald-300/90")
        with ui.row().classes("gap-2"):
            ui.button(tr("research.back"), on_click=lambda: _go_brief(refresh)).props("flat dense no-caps")

            async def on_start() -> None:
                for row in rows:
                    rid = row.get("id") or ""
                    widget = field_refs.get(rid)
                    if widget is not None:
                        row["answer"] = str(getattr(widget, "value", "") or "").strip()
                await _start_research(rows, refresh)

            ui.button(
                tr("research.start"),
                icon="play_arrow",
                on_click=on_start,
            ).props("flat dense no-caps color=primary")


def _collect_upload_paths() -> list[tuple[str, str]]:
    paths: list[tuple[str, str]] = []
    if _state.get("use_session_uploads"):
        paths.extend(_session_upload_paths())
    for item in _state.get("uploads") or []:
        p = item.get("path") or ""
        n = item.get("name") or os.path.basename(p)
        if p and (p, n) not in paths:
            paths.append((p, n))
    return paths


def _go_brief(refresh) -> None:
    _state["screen"] = "brief"
    refresh()


async def _start_research(rows, refresh) -> None:
    global _client_ref
    from nicegui import context
    from services.session.chat_post import post_assistant_message
    from services.session.workflow_control import schedule_on_ui

    try:
        _client_ref = context.client
    except Exception:
        pass

    run_id = int(_state.get("_run_id") or 0) + 1
    _state["_run_id"] = run_id

    brief = rows_to_brief(rows, original_prompt=_state.get("prompt") or "")
    if not brief.topic:
        ui.notify(tr("research.topic_required"), color="warning")
        return
    _state["brief"] = brief
    _state["screen"] = "running"
    _state["busy"] = True
    _state["progress"] = 0.0
    _state["phase"] = "starting"
    _state["log"] = []
    _state["started_at"] = time.time()
    _safe_refresh()

    def on_prog(prog: ResearchProgress) -> None:
        if int(_state.get("_run_id") or 0) != run_id:
            return
        _state["progress"] = prog.fraction
        _state["phase"] = prog.phase
        _state["log"] = list(prog.log[-80:])
        _safe_refresh()

    err_msg = ""
    try:
        md, notes, _, sources = await asyncio.to_thread(
            run_research,
            brief,
            upload_paths=_collect_upload_paths(),
            on_progress=on_prog,
        )
        if int(_state.get("_run_id") or 0) != run_id:
            return
        _state["result_md"] = md
        _state["notes_md"] = notes
        _state["sources_used"] = [s.to_dict() for s in sources]
        stem = brief.topic[:50]
        _state["saved_path"] = await asyncio.to_thread(save_research_markdown, md, stem=stem)

        def _finish_ok() -> None:
            if int(_state.get("_run_id") or 0) != run_id:
                return
            _state["screen"] = "results"
            _state["busy"] = False
            post_assistant_message(md)
            from ui.themes.assets import schedule_scroll_chat

            schedule_scroll_chat()
            refresh()
            ui.notify(tr("research.complete"), color="positive")

        def _run_finish_ok() -> None:
            if _client_ref:
                with _client_ref:
                    _finish_ok()
            else:
                _finish_ok()

        schedule_on_ui(_run_finish_ok)
    except Exception as exc:
        err_msg = str(exc)
        if int(_state.get("_run_id") or 0) != run_id:
            return

        def _finish_err() -> None:
            if int(_state.get("_run_id") or 0) != run_id:
                return
            _state["screen"] = "clarify"
            _state["busy"] = False
            refresh()
            ui.notify(tr("research.failed", error=err_msg), color="negative")

        def _run_finish_err() -> None:
            if _client_ref:
                with _client_ref:
                    _finish_err()
            else:
                _finish_err()

        schedule_on_ui(_run_finish_err)


def _render_running_screen(refresh) -> None:
    from extensions.research.progress_i18n import localize_progress_line, phase_label

    eta = int(_state.get("eta_seconds") or 60)
    elapsed = int(time.time() - float(_state.get("started_at") or time.time()))
    remaining = max(0, eta - elapsed)
    pct = int(float(_state.get("progress") or 0) * 100)
    phase = phase_label(_state.get("phase") or "starting")

    ui.label(phase).classes("text-xs font-medium text-gray-300 shrink-0")

    with ui.element("div").classes("relative w-full h-7 rounded overflow-hidden bg-black/40 shrink-0"):
        ui.element("div").classes("absolute inset-y-0 left-0 bg-sky-500/50 transition-all").style(
            f"width: {pct}%"
        )
        ui.label(tr("research.remaining", pct=pct, remaining=remaining)).classes(
            "absolute inset-0 flex items-center justify-center text-[10px] text-gray-200 z-10"
        )

    log_text = "\n".join(localize_progress_line(ln) for ln in (_state.get("log") or []))
    with ui.column().classes(
        "research-progress-log w-full flex-1 min-h-0 overflow-y-auto soma-scroll "
        "border border-white/30 rounded-lg p-2"
    ):
        ui.label(log_text or tr("research.starting")).classes(
            "text-[10px] text-gray-500 font-mono whitespace-pre-wrap w-full leading-snug"
        )
    ui.run_javascript(
        "const el=document.querySelector('.research-progress-log');"
        "if(el)el.scrollTop=el.scrollHeight;"
    )

    with ui.row().classes("w-full justify-end shrink-0 gap-2"):
        ui.button(
            tr("research.stop"),
            on_click=lambda: _abort_research(refresh),
            icon="stop",
        ).props("flat dense no-caps color=negative")


def _render_results_screen(refresh) -> None:
    brief: ResearchBrief | None = _state.get("brief")
    sources_raw = _state.get("sources_used") or []

    ui.label(tr("research.output_in_chat")).classes(
        "text-[10px] text-gray-400 shrink-0"
    )

    with ui.column().classes("w-full flex-1 min-h-0 overflow-y-auto soma-scroll gap-2"):
        if sources_raw:
            with ui.expansion(tr("research.sources_used"), icon="library_books").classes(
                "w-full shrink-0 text-xs border border-white/10 rounded-lg"
            ).props("dense"):
                for row in sources_raw:
                    sid = row.get("source_id") or "?"
                    title = row.get("title") or tr("research.untitled")
                    url = row.get("url") or ""
                    cred = row.get("credibility_score")
                    note = row.get("credibility_note") or ""
                    with ui.row().classes("w-full gap-2 items-start py-1 border-b border-white/5"):
                        ui.label(sid).classes("text-emerald-400 font-mono text-[10px] shrink-0")
                        with ui.column().classes("min-w-0 flex-1"):
                            if url:
                                ui.link(title, url, new_tab=True).classes(
                                    "text-[11px] break-words"
                                )
                            else:
                                ui.label(title).classes("text-[11px] break-words")
                            meta = f"credibility {cred:.0%}" if isinstance(cred, (int, float)) else ""
                            if note:
                                meta = f"{meta} — {note}" if meta else note
                            if meta:
                                ui.label(meta).classes("text-[9px] text-gray-500")

    with ui.row().classes(
        "w-full gap-2 shrink-0 flex-wrap items-end justify-between mt-auto pt-1"
    ):
        with ui.row().classes("gap-2 flex-wrap items-end"):
            fmt = ui.select(
                [tr("research.fmt_executive"), tr("research.fmt_full"), tr("research.fmt_bullet")],
                value=(brief.output_format if brief else tr("research.fmt_executive")),
                label=tr("research.format"),
            ).props("dense dark standout").classes("text-xs min-w-[140px]")
            tone = ui.select(
                [
                    tr("research.tone_formal"),
                    tr("research.tone_academic"),
                    tr("research.tone_executive"),
                    tr("research.tone_casual"),
                ],
                value=(brief.tone if brief else tr("research.tone_formal")),
                label=tr("research.tone"),
            ).props("dense dark standout").classes("text-xs min-w-[120px]")

        with ui.row().classes("gap-1 shrink-0 items-center"):

            async def regenerate() -> None:
                if not brief:
                    return
                notes = (_state.get("notes_md") or "").strip()
                if not notes:
                    ui.notify(tr("research.no_synthesis"), color="warning")
                    return
                src_objs = [
                    ResearchSource(
                        source_id=str(r.get("source_id") or ""),
                        title=str(r.get("title") or ""),
                        url=str(r.get("url") or ""),
                        credibility_score=float(r.get("credibility_score") or 0.5),
                        credibility_note=str(r.get("credibility_note") or ""),
                        selected=True,
                    )
                    for r in sources_raw
                ]
                _state["busy"] = True
                refresh()
                try:
                    body = await asyncio.to_thread(
                        author_deliverable,
                        brief,
                        notes,
                        sources=src_objs,
                        output_format=str(fmt.value),
                        tone=str(tone.value),
                    )
                    out = append_references(body, src_objs)
                    _state["result_md"] = out
                    _state["saved_path"] = await asyncio.to_thread(
                        save_research_markdown, out, stem=brief.topic[:50]
                    )
                    post_assistant_message(out)
                    from ui.themes.assets import schedule_scroll_chat

                    schedule_scroll_chat()
                    ui.notify(tr("research.regenerated"), color="positive")
                except Exception as exc:
                    ui.notify(str(exc), color="negative")
                finally:
                    _state["busy"] = False
                    refresh()

            ui.button(tr("research.regenerate"), icon="refresh", on_click=regenerate).props(
                "flat dense no-caps color=primary"
            )
            path = _state.get("saved_path") or ""
            if path and os.path.isfile(path):

                def download() -> None:
                    ui.download(path, os.path.basename(path))

                ui.button(tr("research.download_txt"), icon="download", on_click=download).props("flat dense no-caps")
            ui.button(
                tr("research.new"),
                on_click=lambda: _reset(refresh),
                icon="add",
            ).props("flat dense no-caps")


def _abort_research(refresh) -> None:
    _state["_run_id"] = int(_state.get("_run_id") or 0) + 1
    _state.update(
        {
            "screen": "brief",
            "busy": False,
            "log": [],
            "progress": 0.0,
            "phase": "",
        }
    )
    refresh()


def _reset(refresh) -> None:
    _state["_run_id"] = int(_state.get("_run_id") or 0) + 1
    _state.update(
        {
            "screen": "brief",
            "busy": False,
            "clarify_rows": [],
            "brief": None,
            "log": [],
            "progress": 0.0,
            "result_md": "",
            "notes_md": "",
            "sources_used": [],
            "saved_path": "",
        }
    )
    refresh()


def _coerce(e) -> str:
    if isinstance(e, str):
        return e
    args = getattr(e, "args", None)
    if isinstance(args, str):
        return args
    return str(args or "")


class ResearchExtension(BaseExtension):
    extension_id = "research"
    label = "Research"

    def metadata(self) -> dict:
        base = super().metadata()
        base["description"] = (
            "Guided research: clarify your goal, gather web and file sources, "
            "and produce a summary or full report."
        )
        return base

    def mount(self, container) -> None:
        with container:
            build_research_panel()
