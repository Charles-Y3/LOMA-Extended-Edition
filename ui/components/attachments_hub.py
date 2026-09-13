# -*- coding: utf-8 -*-
"""Sources hub — upload, add-link, and clear-all controls plus the chip list.

Lives in its own module (not workspace_panel.py) because several other modules
(toolbar, reset, chat_archive_manager, viewer_selection) need to refresh it, and
workspace_panel.py already imports toolbar.py, so putting it there would create
a circular import.

Mounted in the left nav panel (nav_panel.py) — uploading and adding a link both
happen through this module's own icon row, not a "+" button in the chat pill.
"""
from nicegui import ui

from pipeline.i18n import is_cjk_locale, t as tr
from services.session import state
from ui.components.file_dropzone import render_empty_dropzone
from ui.components.source_card import render_file_card, render_link_card
from ui.themes import registry, tokens
from ui.themes.assets import handle_add_link_pipeline, handle_file_uploaded_pipeline

def mount_sources_uploader(t: dict) -> None:
    """Persistent uploader overlay — survives render_sources_hub.refresh() for multi-file drag
    (5-file cap enforced in handle_file_uploaded_pipeline)."""
    registry.sources_uploader = ui.upload(
        multiple=True,
        auto_upload=True,
        on_upload=lambda e: handle_file_uploaded_pipeline(e, render_sources_hub),
    ).props(t["upload_props"]).classes(
        "absolute inset-0 w-full h-full opacity-0 hidden-uploader z-[5] "
        "loma-sources-uploader-overlay"
    )


@ui.refreshable
def render_sources_hub() -> None:
    t = tokens.get_theme()
    file_count = len(state.active_context_files)
    link_count = len(state.active_web_links)
    total_count = file_count + link_count

    with ui.column().classes("w-full shrink-0 gap-0"):
        with ui.row().classes(
            "w-full items-center justify-between mb-2 px-0.5 shrink-0 relative z-30"
        ):
            ui.label(tr("panel.sources_count", count=total_count)).classes(
                "text-[10px] font-bold tracking-widest uppercase text-blue-400"
            )
            with ui.row().classes("items-center gap-1"):
                def _pick_files() -> None:
                    if registry.sources_uploader:
                        registry.sources_uploader.run_method("pickFiles")

                ui.button(icon="upload_file").props("flat round dense").classes(
                    f"{t['muted']} hover:text-blue-400"
                ).on("click", _pick_files).tooltip(tr("sources.upload_tooltip"))

                with ui.button(icon="add_link").props("flat round dense").classes(
                    f"{t['muted']} hover:text-cyan-500"
                ).tooltip(tr("sources.link_tooltip")):
                    menu_props = "dark" if "dark" in t["select_props"] else ""
                    with ui.menu().props(menu_props).classes(t["menu_bg"]) as link_menu:
                        ui.label(tr("sources.paste_url")).classes(
                            f"{'text-[10px]' if is_cjk_locale() else 'text-[9px]'} font-bold mb-2 block tracking-wider uppercase {t['menu_label']}"
                        )
                        web_input = ui.input(placeholder="https://...").props(
                            f"{t['select_props']} autofocus"
                        ).classes("w-[220px] text-xs mb-2")
                        ui.button(
                            tr("sources.add_source"),
                            icon="add",
                            on_click=lambda: handle_add_link_pipeline(
                                web_input, link_menu, render_sources_hub
                            ),
                        ).props("flat dense").classes(
                            "w-full text-blue-400 bg-blue-500/10 text-xs rounded-lg py-1"
                        )

                def _confirm_clear_all_sources() -> None:
                    if not state.active_context_files and not state.active_web_links:
                        ui.notify(tr("sources.clear_none"), color="info")
                        return
                    with ui.dialog() as dlg, ui.card().classes("p-4"):
                        ui.label(tr("sources.clear_confirm")).classes("text-sm mb-3")
                        with ui.row().classes("justify-end gap-2"):
                            ui.button(tr("sources.clear_cancel"), on_click=dlg.close).props("flat dense")

                            def _do_clear() -> None:
                                from services.session.upload_cleanup import clear_all_sources

                                deleted = clear_all_sources(delete_disk_files=True)
                                dlg.close()
                                render_sources_hub.refresh()
                                ui.notify(tr("sources.cleared_all", deleted=deleted), color="info")

                            ui.button(tr("sources.clear_all"), on_click=_do_clear).props(
                                "flat dense color=negative"
                            )
                    dlg.open()

                ui.button(icon="delete_sweep").props("flat round dense").classes(
                    f"{t['muted']} hover:text-red-400"
                ).on("click", _confirm_clear_all_sources).tooltip(tr("sources.clear_all_tooltip"))

        if total_count == 0:
            render_empty_dropzone(render_sources_hub)
            return

        with ui.column().classes("w-full shrink-0 gap-1.5 mb-1"):
            for file_item in list(state.active_context_files):
                render_file_card(file_item, render_sources_hub.refresh)
            for link_item in list(state.active_web_links):
                render_link_card(link_item, render_sources_hub.refresh)
