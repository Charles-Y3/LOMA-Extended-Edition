# -*- coding: utf-8 -*-
"""Knowledge Vault extension."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import t as tr
from extensions.knowledge_vault.tabs.about import render_about_tab
from extensions.knowledge_vault.tabs.library import render_library_tab
from extensions.knowledge_vault.tabs.settings import render_settings_tab
from extensions.knowledge_vault.tabs.translation_vault import render_translation_vault_tab
from extensions.knowledge_vault.tabs.workspace import render_workspace_tab
from pipeline.base.base_extension import BaseExtension


class KnowledgeVaultExtension(BaseExtension):
    extension_id = "knowledge_vault"
    label = "Knowledge Vault"

    def mount(self, container) -> None:
        # The password-prompt poller for background indexing is app-level now (see
        # ui/layouts/main_layout.py) — it must keep running even when this panel isn't
        # open, so it's no longer started here.
        with container:
            with ui.tabs().classes("w-full text-[11px] min-h-[32px] shrink-0") as tabs:
                tab_about = ui.tab(tr("knowledge_vault.tab.about"))
                tab_workspace = ui.tab(tr("knowledge_vault.tab.workspace"))
                tab_library = ui.tab(tr("knowledge_vault.tab.library"))
                tab_translation_vault = ui.tab(tr("knowledge_vault.tab.translation_vault"))
                tab_settings = ui.tab(tr("knowledge_vault.tab.settings"))

            with ui.tab_panels(tabs, value=tab_workspace).classes(
                "w-full flex-1 min-h-0 bg-transparent p-0"
            ):
                with ui.tab_panel(tab_about).classes("p-0 h-full min-h-0 overflow-y-auto"):
                    render_about_tab()

                with ui.tab_panel(tab_workspace).classes("p-0 h-full min-h-0 overflow-hidden"):
                    render_workspace_tab()

                with ui.tab_panel(tab_library).classes("p-0 h-full min-h-0 overflow-hidden"):
                    render_library_tab()

                with ui.tab_panel(tab_translation_vault).classes(
                    "p-0 h-full min-h-0 overflow-hidden"
                ):
                    render_translation_vault_tab()

                with ui.tab_panel(tab_settings).classes("p-0 h-full min-h-0 overflow-y-auto"):
                    render_settings_tab()
