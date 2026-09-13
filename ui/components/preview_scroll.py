# -*- coding: utf-8 -*-
"""Shared scroll pane for Preview Source and Viewer modes."""
from __future__ import annotations

from nicegui import ui

from ui.components.markup_viewer import (
    MARKDOWN_SOURCE_EDITOR_CLASSES,
    MARKDOWN_SOURCE_INNER_CLASSES,
    MARKDOWN_SOURCE_LABEL_CLASSES,
    build_markup_pane,
)
from ui.themes import registry

SOURCE_SCROLL_CLASSES = (
    "w-full flex-1 min-h-0 min-w-0 overflow-x-hidden loma-scroll loma-preview-scroll-wrap "
    "loma-preview-scroll-source border border-white/10 rounded-lg bg-black/30"
)
VIEWER_SCROLL_CLASSES = (
    "w-full flex-1 min-h-0 loma-scroll loma-preview-scroll-wrap loma-preview-scroll-viewer"
)


def build_preview_scroll_pane(
    *,
    use_viewer: bool,
    draft: str,
    viewer_html: str,
    on_editor_change,
    on_editor_mouseup,
    on_viewer_mouseup,
    output_type: str = "document",
) -> None:
    """Same-sized scroll frame; scrollbar on q-scrollarea edge, outside inner content."""
    registry.preview_editor = None
    registry.preview_markup_holder = None

    script_mode = (output_type or "").strip().lower() == "sound"
    if use_viewer:
        with ui.scroll_area().classes(VIEWER_SCROLL_CLASSES) as preview_scroll:
            with ui.element("div").classes(
                "loma-preview-pane-body w-full min-w-0 loma-preview-pane-body-viewer"
            ):
                holder = ui.column().classes("w-full min-w-0")
                registry.preview_markup_holder = holder
                build_markup_pane(holder, viewer_html)
                preview_scroll.on("mouseup", on_viewer_mouseup)
        return

    pane_classes = (
        f"{SOURCE_SCROLL_CLASSES} loma-preview-source-pane flex flex-col overflow-hidden"
    )
    with ui.element("div").classes(pane_classes):
        body_classes = (
            f"loma-preview-pane-body w-full min-h-0 flex-1 flex flex-col min-w-0 "
            f"{MARKDOWN_SOURCE_INNER_CLASSES}"
        )
        with ui.element("div").classes(body_classes):
            if script_mode:
                ui.label(draft or "").classes(
                    MARKDOWN_SOURCE_LABEL_CLASSES + " w-full block"
                )
            else:
                registry.preview_editor = (
                    ui.textarea(value=draft or "")
                    .props("dark borderless autogrow=false")
                    .classes(
                        f"{MARKDOWN_SOURCE_EDITOR_CLASSES} loma-preview-source-field "
                        "flex-1 min-h-0 min-w-0 max-w-full overflow-x-hidden"
                    )
                )
                registry.preview_editor.on("update:model-value", on_editor_change)
                registry.preview_editor.on("mouseup", on_editor_mouseup)
