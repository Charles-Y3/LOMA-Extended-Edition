# -*- coding: utf-8 -*-
from nicegui import ui

from services.session import state
from ui.themes import tokens

_SOURCE_CHIP = (
    "items-center gap-1.5 flex-nowrap group shrink-0 w-full "
    "rounded-lg pl-2.5 pr-1 py-1.5"
)

# Matches ParsedSource.kind / to_context_dict()'s "source_kind" (services/source_parser).
_SOURCE_KIND_ICON = {
    "document": "description",
    "presentation": "slideshow",
    "spreadsheet": "table_chart",
    "image": "image",
    "audio": "audiotrack",
    "video": "movie",
    "text": "article",
    "web": "link",
    "chat": "chat",
}


def render_file_card(file_item: dict, on_remove) -> None:
    t = tokens.get_theme()
    filename = file_item.get("filename", "Unknown File")
    icon_name = _SOURCE_KIND_ICON.get(file_item.get("source_kind"), "description")
    with ui.row().classes(f"{_SOURCE_CHIP} {t['file_card']}").tooltip(filename):
        ui.icon(icon_name, size="16px").classes("text-blue-400 shrink-0")
        ui.label(filename).classes(f"text-[11px] font-semibold truncate flex-1 min-w-0 {t['file_card_text']}")

        def remove() -> None:
            if file_item in state.active_context_files:
                state.active_context_files.remove(file_item)
                on_remove()
                ui.notify("Document context removed.", color="warning")

        ui.button(icon="close", on_click=remove).props("flat round dense size=xs").classes(
            f"{t['muted']} opacity-70 group-hover:opacity-100 hover:text-red-400 shrink-0"
        )


def render_link_card(link_item: str, on_remove) -> None:
    t = tokens.get_theme()
    display_url = link_item.replace("https://", "").replace("http://", "").replace("www.", "")
    with ui.row().classes(f"{_SOURCE_CHIP} {t['link_card']}").tooltip(display_url):
        ui.icon("link", size="16px").classes("text-cyan-400 shrink-0")
        ui.label(display_url).classes(f"text-[11px] font-semibold truncate flex-1 min-w-0 {t['file_card_text']}")

        def remove() -> None:
            if link_item in state.active_web_links:
                state.active_web_links.remove(link_item)
                from services.web_context_cache import drop_cached

                drop_cached(link_item)
                on_remove()
                ui.notify("Link reference removed.", color="warning")

        ui.button(icon="close", on_click=remove).props("flat round dense size=xs").classes(
            f"{t['muted']} opacity-70 group-hover:opacity-100 hover:text-red-400 shrink-0"
        )
