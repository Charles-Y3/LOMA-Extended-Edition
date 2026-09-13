# -*- coding: utf-8 -*-
"""Panel visibility: extension open collapses output to share width with workspace."""
from nicegui import ui

from ui.themes import registry


def open_extension_panel() -> None:
    registry.extension_panel_open = True
    if registry.output_panel:
        registry.output_panel.set_visibility(False)
    if registry.panel_splitter:
        registry.panel_splitter.set_visibility(False)
    if registry.ext_ws_splitter:
        registry.ext_ws_splitter.set_visibility(True)
    if registry.extension_panel:
        registry.extension_panel.set_visibility(True)
    try:
        from ui.themes.assets import inject_splitter_resizable_script

        inject_splitter_resizable_script()
        ui.run_javascript(
            "if (window.lomaSyncExtensionDefaultWidth) { window.lomaSyncExtensionDefaultWidth(); }"
        )
    except Exception:
        pass


def close_extension_panel() -> None:
    ext = (registry.active_extension or "").strip()
    if ext:
        from extensions.viewer_runtime.controls import VIEWER_EXTENSION_IDS, on_viewer_closed

        if ext in VIEWER_EXTENSION_IDS:
            on_viewer_closed(ext)
    registry.extension_panel_open = False
    if registry.extension_panel:
        registry.extension_panel.set_visibility(False)
    if registry.ext_ws_splitter:
        registry.ext_ws_splitter.set_visibility(False)
    if registry.output_panel:
        registry.output_panel.set_visibility(True)
    if registry.panel_splitter:
        registry.panel_splitter.set_visibility(True)
    if registry.extension_select and registry.extension_select.value:
        # Must be the catalog's real "none" option value, not "" — the select's
        # options are {"none": "None", "chat_archive": "...", ...} (see
        # ui/layouts/nav_panel.py::_extension_options), so a value of "" matches
        # none of them and NiceGUI renders the dropdown blank instead of "None"
        # (ChoiceElement._render_markdown falls into its except branch and
        # displays the raw, label-less value). This was the same class of bug
        # already fixed once for the dropdown's own refresh path
        # (ui/components/extension_library.py::refresh_extension_select_options)
        # — this call site just never got the same fix.
        from pipeline.registry.extension_registry import EXTENSION_SELECT_NONE

        registry.extension_select.set_value(EXTENSION_SELECT_NONE)
    registry.active_extension = ""
    try:
        from ui.layouts.extension_panel import render_extension_content

        render_extension_content.refresh()
    except Exception:
        pass
