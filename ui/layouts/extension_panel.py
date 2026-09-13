# -*- coding: utf-8 -*-
from nicegui import ui

from pipeline.i18n import t as tr
from pipeline.registry.extension_registry import extension_registry
from ui.components.extension_chrome import mount_extension_header
from ui.themes import registry
from ui.themes.panel_nav import close_extension_panel, open_extension_panel


@ui.refreshable
def render_extension_content() -> None:
    if not registry.extension_content:
        return
    registry.extension_content.clear()
    ext_id = registry.active_extension or ""

    with registry.extension_content:
        if not ext_id:
            ui.label(tr("extension.select_hint")).classes(
                "text-[11px] text-gray-500 leading-relaxed whitespace-pre-line p-2"
            )
            return

        mount_extension_header(ext_id)

        with ui.column().classes(
            "w-full flex-1 min-h-0 flex flex-col gap-0 overflow-hidden"
        ) as ext_body:
            if ext_id in extension_registry._mount_fns or ext_id in extension_registry._extensions:
                extension_registry.mount(ext_id, ext_body)
            else:
                ui.label(tr("extension.unknown", id=ext_id)).classes("text-[11px] text-gray-500 p-2")


def build_extension_panel(t: dict) -> None:
    registry.extension_panel = ui.column().classes(
        f'shrink-0 h-full min-w-0 bg-[{t["sidebar"]}] border border-[{t["border"]}] '
        f"rounded-2xl p-4 flex flex-col backdrop-blur-md transition-all relative no-scrollbar"
    ).style("width: 300px; min-width: 220px; max-width: 45vw;").props(
        "id=loma-extension-panel"
    )
    registry.extension_panel.set_visibility(False)

    with registry.extension_panel:
        ui.button(on_click=close_extension_panel).props(
            "flat round dense icon=close"
        ).classes("absolute top-2 right-2 text-gray-500 z-10")

        registry.extension_content = ui.column().classes(
            "w-full flex-1 min-h-0 overflow-hidden flex flex-col"
        )
        render_extension_content()

    # TEMP (verification only): auto-open an extension when LOMA_AUTO_EXT is set.
    import os as _os
    _auto = _os.environ.get("LOMA_AUTO_EXT")
    if _auto:
        ui.timer(1.5, lambda: show_extension(_auto), once=True)


def show_extension(ext_id: str) -> None:
    import time

    from pipeline.debug_session import debug_log

    from extensions.viewer_runtime.controls import VIEWER_EXTENSION_IDS, on_viewer_closed, on_viewer_opened

    previous = registry.active_extension or ""
    if previous in VIEWER_EXTENSION_IDS and ext_id != previous:
        on_viewer_closed(previous)

    _t0 = time.perf_counter()
    registry.active_extension = ext_id or ""
    if ext_id:
        open_extension_panel()
        if ext_id in VIEWER_EXTENSION_IDS:
            on_viewer_opened(ext_id)
    else:
        close_extension_panel()
    render_extension_content.refresh()
    debug_log(
        "extension_panel:show_extension",
        "show_extension complete",
        {"ext_id": ext_id, "ms": round((time.perf_counter() - _t0) * 1000, 1)},
        "B",
    )
