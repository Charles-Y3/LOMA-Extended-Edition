# -*- coding: utf-8 -*-
"""About tab."""
from __future__ import annotations

from nicegui import ui

from pipeline.extension_i18n import extension_title
from pipeline.i18n import t as tr


def render_about_tab() -> None:
    with ui.column().classes("w-full gap-3 p-2 overflow-y-auto loma-scroll"):
        ui.add_css(
            """
            .loma-document-intelligence-about, .loma-document-intelligence-about * {
                font-family: inherit !important;
                font-size: 0.75rem !important;
                line-height: 1.5 !important;
            }
            """
        )
        ui.label(extension_title("knowledge_vault", tr("knowledge_vault.about_title"))).classes(
            "text-lg font-bold"
        )
        ui.markdown(tr("knowledge_vault.about_body")).classes(
            "loma-document-intelligence-about text-xs opacity-90"
        )
