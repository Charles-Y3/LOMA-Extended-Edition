# -*- coding: utf-8 -*-
"""Document Intelligence extension."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import t as tr
from extensions.document_intelligence.tabs.about import render_about_tab
from extensions.document_intelligence.tabs.library import render_library_tab
from extensions.document_intelligence.tabs.settings import render_settings_tab
from extensions.document_intelligence.tabs.workspace import render_workspace_tab
from pipeline.base.base_extension import BaseExtension


class DocumentIntelligenceExtension(BaseExtension):
    extension_id = "document_intelligence"
    label = "Document Intelligence"

    def mount(self, container) -> None:
        # The password-prompt poller for background indexing is app-level now (see
        # ui/layouts/main_layout.py) — it must keep running even when this panel isn't
        # open, so it's no longer started here.
        with container:
            with ui.tabs().classes("w-full text-[11px] min-h-[32px] shrink-0") as tabs:
                tab_about = ui.tab(tr("di.tab.about"))
                tab_workspace = ui.tab(tr("di.tab.workspace"))
                tab_library = ui.tab(tr("di.tab.library"))
                tab_settings = ui.tab(tr("di.tab.settings"))

            with ui.tab_panels(tabs, value=tab_workspace).classes(
                "w-full flex-1 min-h-0 bg-transparent p-0"
            ):
                with ui.tab_panel(tab_about).classes("p-0 h-full min-h-0 overflow-y-auto"):
                    render_about_tab()

                with ui.tab_panel(tab_workspace).classes("p-0 h-full min-h-0 overflow-hidden"):
                    render_workspace_tab()

                with ui.tab_panel(tab_library).classes("p-0 h-full min-h-0 overflow-hidden"):
                    render_library_tab()

                with ui.tab_panel(tab_settings).classes("p-0 h-full min-h-0 overflow-y-auto"):
                    render_settings_tab()
