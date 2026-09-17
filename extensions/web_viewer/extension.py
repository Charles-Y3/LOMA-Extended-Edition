# -*- coding: utf-8 -*-
"""Web viewer — scrape page text; highlight to query LOMA (direct pipeline)."""
from __future__ import annotations

import asyncio
import json

from nicegui import ui

from services.web_fetch import scrape_website_text
from services.web_fetch_policy import responsible_use_notice
from services.web_viewer_history import add_web_viewer_history, get_web_viewer_history
from pipeline.base.base_extension import BaseExtension
from pipeline.i18n import t as tr
from extensions.web_viewer.highlight_runner import start_web_highlight_workflow
from ui.components.viewer_selection import open_highlight_dialog, read_dom_selection
from ui.themes import tokens

_web_state = {"last_scraped_dict": None, "font_px": 13}
_FONT_MIN = 9
_FONT_MAX = 20


def _web_source_text(content: dict) -> str:
    return (content.get("simple_text") or content.get("content") or "").strip()


def build_web_viewer_panel() -> None:
    t = tokens.get_theme()
    placeholder_html = (
        '<div class="w-full h-full flex items-center justify-center {muted} text-[10px] '
        'text-center p-4 border border-dashed rounded-lg" '
        'style="border-color: {border}">Webview ready.</div>'
    ).format(muted=t["muted"], border=t["border"])
    web_content_text_holder: dict = {"column": None}
    web_content_area_holder: dict = {"area": None}
    iframe_holder: dict = {"el": None}
    web_url_holder: dict = {"input": None}
    history_list_holder: dict = {"column": None}
    actions_row_holder: dict = {"row": None}

    def render_captured_web_content() -> None:
        col = web_content_text_holder["column"]
        if col is None:
            return
        col.clear()
        content = _web_state["last_scraped_dict"]
        if not content:
            return
        with col:
            label = ui.label(_web_source_text(content)).classes(
                f"w-full select-text whitespace-pre-wrap leading-relaxed {t['text']} "
                "font-sans max-w-[70ch] mx-auto"
            )
            label.style(f"font-size: {_web_state.get('font_px', 13)}px")
            web_content_text_holder["label"] = label

    def _bump_font_size(delta: int) -> None:
        px = max(_FONT_MIN, min(_FONT_MAX, _web_state.get("font_px", 13) + delta))
        _web_state["font_px"] = px
        label = web_content_text_holder.get("label")
        if label is not None:
            label.style(f"font-size: {px}px")

    def _copy_page_text() -> None:
        content = _web_state["last_scraped_dict"]
        text = _web_source_text(content) if content else ""
        if not text:
            ui.notify(tr("web_viewer.copy_nothing"), color="warning")
            return
        payload = json.dumps(text)
        ui.run_javascript(f"navigator.clipboard.writeText({payload})")
        ui.notify(tr("web_viewer.copy_done"), color="positive", timeout=1500)

    def send_viewer_query(
        sel: str,
        instruction: str,
        use_kv: bool = False,
        kv_mode: str = "ask",
        kv_scope: str = "all",
        kv_library_id: str | None = None,
        *,
        display_text: str | None = None,
    ) -> None:
        url = (web_url_holder["input"].value or "") if web_url_holder["input"] else ""
        source_part = url.strip() or "the web page"
        query = (
            f"Regarding the following excerpt from {source_part}:\n\n"
            f'"{sel.strip()}"\n\n'
            f"{instruction.strip()}"
        )
        from services.session import state

        # display_text keeps the chat bubble short for a full-page excerpt (e.g. the
        # Summarize/Extract buttons, which can carry tens of thousands of characters) —
        # the full `query` (with the complete excerpt) still goes to the actual workflow
        # below, which takes it as an explicit argument rather than re-reading
        # state.messages.
        state.messages.append({"role": "user", "content": display_text or query})
        state.add_log(f"Selection query sent to workspace ({len(sel.strip())} chars).")
        ui_mod = state.get_ui_module()
        if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):
            ui_mod.render_chat.refresh()
        from ui.themes.assets import schedule_scroll_chat

        schedule_scroll_chat()
        start_web_highlight_workflow(
            query, instruction,
            use_kv=use_kv, kv_mode=kv_mode, kv_scope=kv_scope, kv_library_id=kv_library_id,
        )

    def _run_whole_page_instruction(instruction_key: str, nothing_key: str) -> None:
        content = _web_state["last_scraped_dict"]
        text = _web_source_text(content) if content else ""
        if not text:
            ui.notify(tr(nothing_key), color="warning")
            return
        instruction = tr(instruction_key)
        send_viewer_query(text, instruction, display_text=instruction)

    def summarize_page() -> None:
        _run_whole_page_instruction("web_viewer.summarize_instruction", "web_viewer.summarize_nothing")

    def extract_key_points() -> None:
        _run_whole_page_instruction("web_viewer.extract_instruction", "web_viewer.extract_nothing")

    async def on_content_mouseup(_=None) -> None:
        highlight = await read_dom_selection()
        if not highlight:
            return
        url = (web_url_holder["input"].value or "") if web_url_holder["input"] else ""

        open_highlight_dialog(
            tr("document_editor.query_highlighted"),
            highlight,
            on_query=send_viewer_query,
            source_label=f"web_{url or 'page'}",
        )

    with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2"):
        with ui.row().classes("w-full items-center gap-1 flex-nowrap shrink-0"):
            web_url = ui.input(placeholder="https://...").props("dark standout dense").classes(
                "flex-1 text-[11px]"
            )
            web_url_holder["input"] = web_url

            async def load_url() -> None:
                url = (web_url.value or "").strip()
                if not url:
                    return
                if not url.startswith("http"):
                    url = f"https://{url}"

                col = web_content_text_holder["column"]
                if col:
                    col.clear()
                    with col:
                        ui.label(tr("web_viewer.scraping")).classes(f"text-xs {t['muted']} italic")

                area = web_content_area_holder["area"]
                iframe = iframe_holder["el"]
                if area:
                    area.set_visibility(True)
                if iframe:
                    iframe.set_visibility(False)

                def _show_actions() -> None:
                    row = actions_row_holder.get("row")
                    if row is not None:
                        row.set_visibility(True)

                try:
                    content = await asyncio.to_thread(scrape_website_text, url)
                    if isinstance(content, dict) and "raw_styled" in content and "error" not in content:
                        _web_state["last_scraped_dict"] = content
                        render_captured_web_content()
                        add_web_viewer_history(url)
                        _refresh_history_menu()
                        _show_actions()
                    elif isinstance(content, dict) and content.get("error"):
                        _web_state["last_scraped_dict"] = None
                        msg = (content.get("content") or content.get("simple_text") or "Fetch blocked.")[:400]
                        ui.notify(msg, color="warning", multi_line=True)
                        if area:
                            area.set_visibility(False)
                        if iframe:
                            iframe.set_visibility(True)
                            iframe.set_content(
                                f'<iframe src="{url}" class="w-full h-full border-0 rounded-lg"></iframe>'
                            )
                    elif isinstance(content, str) and not str(content).startswith(("Error", "⚠️")):
                        _web_state["last_scraped_dict"] = {
                            "raw_styled": f"<text-block>{content}</text-block>",
                            "content": content,
                            "simple_text": content,
                        }
                        render_captured_web_content()
                        add_web_viewer_history(url)
                        _refresh_history_menu()
                        _show_actions()
                    else:
                        _web_state["last_scraped_dict"] = None
                        if area:
                            area.set_visibility(False)
                        if iframe:
                            iframe.set_visibility(True)
                            iframe.set_content(
                                f'<iframe src="{url}" class="w-full h-full border-0 rounded-lg"></iframe>'
                            )
                except Exception as ex:
                    print(f"Web viewer error: {ex}")
                    _web_state["last_scraped_dict"] = None
                    if area:
                        area.set_visibility(False)
                    if iframe:
                        iframe.set_visibility(True)
                        iframe.set_content(
                            f'<iframe src="{url}" class="w-full h-full border-0 rounded-lg"></iframe>'
                        )

            def _refresh_history_menu() -> None:
                col = history_list_holder.get("column")
                if col is None:
                    return
                col.clear()
                with col:
                    history = get_web_viewer_history()
                    if not history:
                        ui.label(tr("web_viewer.history_empty")).classes(
                            f"text-xs {t['muted']} p-2"
                        )
                        return
                    for hist_url in history:
                        async def _pick(u=hist_url) -> None:
                            web_url.value = u
                            history_menu.close()
                            await load_url()

                        ui.menu_item(hist_url, on_click=_pick).classes("text-xs")

            ui.button(icon="travel_explore", on_click=load_url).props("flat dense").classes(
                "text-blue-400 bg-blue-500/10 shrink-0 rounded p-1.5"
            ).tooltip(tr("web_viewer.scrape"))

            with ui.button(icon="history").props("flat dense").classes(
                "text-gray-300 bg-white/5 shrink-0 rounded p-1.5"
            ).tooltip(tr("web_viewer.history_tooltip")):
                with ui.menu() as history_menu:
                    history_col = ui.column().classes("gap-0 min-w-[240px] py-1")
                    history_list_holder["column"] = history_col
            history_menu.on("show", _refresh_history_menu)

            def clear_web_content() -> None:
                web_url.value = ""
                _web_state["last_scraped_dict"] = None
                col = web_content_text_holder["column"]
                if col:
                    col.clear()
                area = web_content_area_holder["area"]
                iframe = iframe_holder["el"]
                if area:
                    area.set_visibility(False)
                if iframe:
                    iframe.set_visibility(True)
                    iframe.set_content(placeholder_html)
                row = actions_row_holder.get("row")
                if row is not None:
                    row.set_visibility(False)

            ui.button(icon="cleaning_services", on_click=clear_web_content).props("flat dense").classes(
                "text-red-400 bg-red-500/10 shrink-0 rounded p-1.5"
            ).tooltip(tr("web_viewer.clear"))

        with ui.row().classes("w-full items-center gap-1 flex-wrap shrink-0") as actions_row:
            actions_row.set_visibility(False)
            actions_row_holder["row"] = actions_row

            ui.button(icon="summarize", on_click=summarize_page).props("flat dense").classes(
                "text-blue-400 bg-blue-500/10 shrink-0 rounded p-1.5"
            ).tooltip(tr("web_viewer.summarize_tooltip"))

            ui.button(icon="checklist", on_click=extract_key_points).props("flat dense").classes(
                "text-blue-400 bg-blue-500/10 shrink-0 rounded p-1.5"
            ).tooltip(tr("web_viewer.extract_key_points"))

            ui.button(icon="content_copy", on_click=_copy_page_text).props("flat dense").classes(
                "text-gray-300 bg-white/5 shrink-0 rounded p-1.5"
            ).tooltip(tr("web_viewer.copy_tooltip"))

            ui.button(icon="text_decrease", on_click=lambda: _bump_font_size(-1)).props(
                "flat round dense size=sm"
            ).classes(
                "text-gray-400 opacity-80 hover:opacity-100 shrink-0"
            ).tooltip(tr("web_viewer.font_decrease_tooltip"))

            ui.button(icon="text_increase", on_click=lambda: _bump_font_size(1)).props(
                "flat round dense size=sm"
            ).classes(
                "text-gray-400 opacity-80 hover:opacity-100 shrink-0"
            ).tooltip(tr("web_viewer.font_increase_tooltip"))

        ui.label(responsible_use_notice()).classes(f"text-[12px] {t['muted']} leading-relaxed px-0.5")

        with ui.column().classes(
            f"w-full flex-1 min-h-0 overflow-y-auto soma-scroll rounded-lg p-3 {t['settings_card']}"
        ) as web_content_area:
            web_content_area.set_visibility(False)
            web_content_area_holder["area"] = web_content_area
            web_content_area.on("mouseup", on_content_mouseup)
            web_content_text_holder["column"] = ui.column().classes("w-full gap-0 select-text")

        iframe_container = ui.html(placeholder_html).classes("w-full flex-1 min-h-0")
        iframe_holder["el"] = iframe_container

        ui.label(tr("web_viewer.hint")).classes(
            f"text-xs {t['muted']} text-center w-full shrink-0"
        )


class WebViewerExtension(BaseExtension):
    extension_id = "web_viewer"
    label = "Web Viewer"

    def metadata(self) -> dict:
        base = super().metadata()
        base["description"] = "Scrape web pages, highlight excerpts, and query LOMA."
        return base

    def mount(self, container) -> None:
        with container:
            build_web_viewer_panel()
