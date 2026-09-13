# -*- coding: utf-8 -*-
"""Token Usage — per-model and date-range usage dashboard."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from nicegui import ui

from pipeline.base.base_extension import BaseExtension
from pipeline.i18n import t as tr
from services.session import token_usage

_PANEL = "w-full flex-1 min-h-0 flex flex-col gap-2 min-w-0 overflow-hidden"
_CARD = "w-full rounded-lg border border-white/10 bg-white/5 p-3"
_SCROLL = "w-full flex-1 soma-scroll min-h-0 overflow-y-auto overflow-x-hidden pr-2"

_MODEL_ALL = "__all__"


def _fmt(n: int) -> str:
    return f"{int(n):,}"


def _backend_models() -> list[str]:
    try:
        from services.llm_bridge import list_models

        return sorted(m for m in (list_models() or []) if m)
    except Exception:
        return []


def _chart_options(labels: list[str], values: list[int], *, title: str) -> dict[str, Any]:
    return {
        "title": {"text": title, "textStyle": {"color": "#94a3b8", "fontSize": 11}},
        "tooltip": {"trigger": "axis"},
        "grid": {"left": 48, "right": 12, "top": 36, "bottom": 28},
        "xAxis": {
            "type": "category",
            "data": labels,
            "axisLabel": {"color": "#94a3b8", "fontSize": 10, "rotate": 30 if len(labels) > 8 else 0},
        },
        "yAxis": {
            "type": "value",
            "axisLabel": {"color": "#94a3b8", "fontSize": 10},
            "splitLine": {"lineStyle": {"color": "rgba(255,255,255,0.06)"}},
        },
        "series": [
            {
                "type": "bar",
                "data": values,
                "itemStyle": {"color": "#38bdf8"},
            }
        ],
    }


def _fmt_cost(amount: float) -> str:
    if amount <= 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    return f"${amount:,.2f}"


def build_token_tracker_panel() -> None:
    today = date.today()
    default_start = today - timedelta(days=30)

    with ui.column().classes(_PANEL):
        summary_holder: dict[str, Any] = {
            "data": {"totals": {}, "by_model": {}, "by_day": {}},
            "events": [],
        }

        def _parse_date(raw: str, fallback: date) -> date:
            try:
                return date.fromisoformat((raw or "").strip()[:10])
            except Exception:
                return fallback

        def load_summary() -> dict[str, Any]:
            start = _parse_date(start_in.value or "", default_start)
            end = _parse_date(end_in.value or "", today)
            if start > end:
                start, end = end, start
            sel = model_sel.value or _MODEL_ALL
            model = None if sel == _MODEL_ALL else str(sel).strip() or None
            events = token_usage.load_events(start=start, end=end, model=model)
            summary_holder["events"] = events
            return token_usage.summarize_events(events)

        @ui.refreshable
        def stats_area() -> None:
            totals = summary_holder["data"].get("totals") or {}
            with ui.row().classes("w-full gap-2 flex-wrap shrink-0"):
                for label, key, fmt in (
                    (tr("token_tracker.total_tokens"), "total_tokens", _fmt),
                    (tr("token_tracker.prompt"), "prompt_tokens", _fmt),
                    (tr("token_tracker.completion"), "completion_tokens", _fmt),
                    (tr("token_tracker.calls"), "calls", _fmt),
                    (tr("token_tracker.est_cost"), "cost", _fmt_cost),
                ):
                    with ui.card().classes(_CARD + " flex-1 min-w-[100px]"):
                        ui.label(label).classes("text-[10px] text-gray-400 uppercase")
                        val = totals.get(key, 0)
                        ui.label(fmt(val)).classes("text-lg font-bold text-sky-200")

        @ui.refreshable
        def model_table() -> None:
            by_model = summary_holder["data"].get("by_model") or {}
            ui.label(tr("token_tracker.by_model")).classes(
                "text-xs font-bold text-cyan-400 uppercase mt-1 shrink-0"
            )
            if not by_model:
                ui.label(tr("token_tracker.no_usage")).classes("text-xs opacity-60 shrink-0")
                return
            with ui.column().classes(_SCROLL + " gap-1"):
                for model, row in by_model.items():
                    with ui.row().classes(
                        "w-full items-center justify-between gap-2 text-[11px] "
                        "border-b border-white/5 py-1"
                    ):
                        ui.label(model).classes("truncate flex-1 font-mono")
                        cost = float(row.get("cost") or 0)
                        ui.label(
                            tr(
                                "token_tracker.row_stats",
                                tokens=_fmt(row["total_tokens"]),
                                calls=_fmt(row["calls"]),
                                cost=_fmt_cost(cost),
                            )
                        ).classes("text-gray-400 shrink-0")

        @ui.refreshable
        def day_table() -> None:
            by_day = summary_holder["data"].get("by_day") or {}
            ui.label(tr("token_tracker.by_day")).classes(
                "text-xs font-bold text-cyan-400 uppercase mt-2 shrink-0"
            )
            if not by_day:
                ui.label(tr("token_tracker.no_daily")).classes("text-xs opacity-60 shrink-0")
                return
            with ui.column().classes(_SCROLL + " gap-1"):
                for day, row in reversed(list(by_day.items())):
                    with ui.row().classes(
                        "w-full items-center justify-between gap-2 text-[11px] "
                        "border-b border-white/5 py-1"
                    ):
                        ui.label(day).classes("font-mono")
                        cost = float(row.get("cost") or 0)
                        ui.label(
                            tr(
                                "token_tracker.row_stats",
                                tokens=_fmt(row["total_tokens"]),
                                calls=_fmt(row["calls"]),
                                cost=_fmt_cost(cost),
                            )
                        ).classes("text-gray-400")

        chart_host: dict[str, Any] = {"element": None}

        def _render_chart(period: str) -> None:
            if chart_host["element"] is None:
                return
            chart_host["element"].clear()
            if period == "model":
                by_model = summary_holder["data"].get("by_model") or {}
                rows = sorted(
                    ((model, row["total_tokens"]) for model, row in by_model.items()),
                    key=lambda kv: kv[1],
                    reverse=True,
                )
            else:
                rows = token_usage.aggregate_period(summary_holder["events"], period=period)
            with chart_host["element"]:
                if not rows:
                    ui.label(tr("token_tracker.no_data")).classes("text-xs opacity-60 py-8 text-center w-full")
                    return
                labels = [k for k, _ in rows]
                values = [v for _, v in rows]
                titles = {
                    "day": tr("token_tracker.chart_day"),
                    "month": tr("token_tracker.chart_month"),
                    "year": tr("token_tracker.chart_year"),
                    "model": tr("token_tracker.chart_model"),
                }
                ui.echart(_chart_options(labels, values, title=titles.get(period, "Tokens"))).classes(
                    "w-full h-full"
                )

        def refresh_data() -> None:
            summary_holder["data"] = load_summary()
            stats_area.refresh()
            model_table.refresh()
            day_table.refresh()
            _render_chart(period_sel.value or "month")

        def set_range(days: int) -> None:
            start_in.set_value((today - timedelta(days=days)).isoformat())
            end_in.set_value(today.isoformat())
            refresh_data()

        def set_today() -> None:
            start_in.set_value(today.isoformat())
            end_in.set_value(today.isoformat())
            refresh_data()

        def set_all_time_range() -> None:
            events = token_usage.load_events()
            if not events:
                start_in.set_value(default_start.isoformat())
                end_in.set_value(today.isoformat())
            else:
                dates: list[date] = []
                for row in events:
                    raw = str(row.get("ts") or "").strip()
                    if not raw:
                        continue
                    try:
                        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        if ts.tzinfo is not None:
                            ts = ts.astimezone()
                        dates.append(ts.date())
                    except Exception:
                        continue
                if dates:
                    start_in.set_value(min(dates).isoformat())
                    end_in.set_value(max(dates).isoformat())
            refresh_data()

        stats_area()

        cost_cfg = token_usage.load_cost_settings()
        with ui.row().classes("w-full gap-2 shrink-0 items-end flex-wrap"):
            prompt_cost_in = ui.number(
                tr("token_tracker.prompt_cost"),
                value=float(cost_cfg.get("prompt_per_million") or 0),
                min=0,
                step=0.01,
            ).props("dense dark standout").classes("flex-1 min-w-0")
            completion_cost_in = ui.number(
                tr("token_tracker.completion_cost"),
                value=float(cost_cfg.get("completion_per_million") or 0),
                min=0,
                step=0.01,
            ).props("dense dark standout").classes("flex-1 min-w-0")

            def _save_costs() -> None:
                token_usage.save_cost_settings(
                    {
                        "prompt_per_million": float(prompt_cost_in.value or 0),
                        "completion_per_million": float(completion_cost_in.value or 0),
                    }
                )
                refresh_data()
                ui.notify(tr("token_tracker.rates_saved"), color="positive")

            ui.button(tr("token_tracker.save_rates"), on_click=_save_costs).props("flat dense color=primary")

        with ui.row().classes("w-full gap-2 shrink-0 items-end flex-wrap"):
            start_in = ui.input(tr("token_tracker.from"), value=default_start.isoformat()).props(
                "dense dark standout type=date"
            ).classes("flex-1 min-w-0")
            end_in = ui.input(tr("token_tracker.to"), value=today.isoformat()).props(
                "dense dark standout type=date"
            ).classes("flex-1 min-w-0")
            known = sorted(set(token_usage.list_models()) | set(_backend_models()))
            model_opts: dict[str, str] = {_MODEL_ALL: tr("token_tracker.all_models")}
            model_opts.update({m: m for m in known})
            model_sel = ui.select(
                options=model_opts,
                label=tr("token_tracker.model"),
                value=_MODEL_ALL,
            ).props("dense dark standout").classes("flex-1 min-w-0")

        model_sel.on_value_change(lambda _: refresh_data())

        with ui.row().classes("w-full gap-2 shrink-0 flex-wrap items-center"):
            ui.button(tr("token_tracker.refresh"), icon="refresh", on_click=refresh_data).props(
                "flat dense color=primary"
            )
            ui.button(tr("token_tracker.today"), icon="today", on_click=set_today).props("flat dense")
            ui.button(tr("token_tracker.last_7"), icon="date_range", on_click=lambda: set_range(7)).props(
                "flat dense"
            )
            ui.button(tr("token_tracker.last_30"), icon="calendar_month", on_click=lambda: set_range(30)).props(
                "flat dense"
            )
            ui.button(tr("token_tracker.all_time_range"), icon="history", on_click=set_all_time_range).props(
                "flat dense"
            )

        with ui.tabs().classes("w-full text-[11px] shrink-0 mt-1") as tabs:
            tab_table = ui.tab(tr("token_tracker.tables"))
            tab_charts = ui.tab(tr("token_tracker.charts"))

        with ui.tab_panels(tabs, value=tab_table).classes("w-full flex-1 min-h-0 bg-transparent p-0"):
            with ui.tab_panel(tab_table).classes("p-0 h-full min-h-0"):
                with ui.column().classes("w-full h-full min-h-0 gap-1"):
                    with ui.column().classes("w-full flex-1 min-h-0 flex flex-col overflow-hidden"):
                        model_table()
                    with ui.column().classes("w-full flex-1 min-h-0 flex flex-col overflow-hidden"):
                        day_table()
            with ui.tab_panel(tab_charts).classes("p-0 h-full min-h-0"):
                with ui.column().classes(_PANEL):
                    period_sel = ui.select(
                        options={
                            "day": tr("token_tracker.group_day"),
                            "month": tr("token_tracker.group_month"),
                            "year": tr("token_tracker.group_year"),
                            "model": tr("token_tracker.group_model"),
                        },
                        label=tr("token_tracker.group_by"),
                        value="month",
                    ).props("dense dark standout").classes("w-full shrink-0")
                    period_sel.on_value_change(lambda _: _render_chart(period_sel.value or "month"))
                    chart_host["element"] = ui.column().classes("w-full flex-1 min-h-0")

        refresh_data()


class TokenTrackerExtension(BaseExtension):
    extension_id = "token_tracker"
    label = "Token Usage"

    def metadata(self) -> dict:
        base = super().metadata()
        base["description"] = (
            "Track LLM token usage per model, totals, and date ranges across LOMA sessions."
        )
        base["category"] = "utility"
        return base

    def mount(self, container) -> None:
        with container:
            build_token_tracker_panel()
