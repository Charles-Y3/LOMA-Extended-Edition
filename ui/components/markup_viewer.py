# -*- coding: utf-8 -*-
"""Shared NiceGUI panes for markup and markdown source views."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

# Shared Source-mode layout (Preview panel + Document Editor extension).
MARKDOWN_SOURCE_INNER_CLASSES = "p-3"
MARKDOWN_SOURCE_LABEL_CLASSES = (
    "w-full select-text whitespace-pre-wrap leading-relaxed text-xs text-gray-300 "
    "font-mono loma-markdown-source"
)
MARKDOWN_SOURCE_EDITOR_CLASSES = (
    "w-full font-mono text-xs leading-relaxed break-words "
    "whitespace-pre-wrap [overflow-wrap:anywhere] text-gray-300 "
    "loma-markdown-source loma-preview-editor"
)


def build_markup_pane(container, html_content: str, *, font_px: int | None = None):
    """Render styled semantic HTML inside an isolated, scroll-safe markup viewport.

    font_px sets --loma-view-font-size, a CSS custom property the paper-sheet
    body text reads (see loma-paper-sheet in ui/themes/assets.py) — custom
    properties inherit through the injected HTML even though the paper sheet
    sets its own explicit font-size, so this is how view-mode font bump
    reaches content a plain inline font-size on the wrapper couldn't touch.
    """
    container.clear()
    body = html_content or "<p></p>"
    wrapped = (
        '<div class="loma-markup-root">'
        f'<div class="loma-markup-canvas">{body}</div>'
        "</div>"
    )
    with container:
        el = ui.html(wrapped).classes(
            "loma-markup-host w-full min-w-0 max-w-full select-text"
        )
        if font_px:
            el.style(f"--loma-view-font-size: {font_px}px;")
    return el


def build_markdown_source_pane(
    container,
    text: str,
    *,
    editable: bool = False,
    on_change: Callable | None = None,
    fill_height: bool = False,
    font_px: int = 12,
):
    """Render raw markdown source — read-only label or editable textarea."""
    container.clear()
    wrap_classes = "w-full flex-1 min-h-0 h-full gap-0"
    if fill_height:
        wrap_classes += " loma-doc-editor-wrap"
    with container:
        with ui.column().classes(wrap_classes):
            if editable:
                editor = (
                    ui.textarea(value=text or "")
                    .props(
                        'dark borderless autogrow=false '
                        'input-style="height:100%;min-height:100%;resize:none;"'
                    )
                    .classes(
                        "w-full flex-1 min-h-0 font-mono leading-relaxed break-words "
                        "whitespace-pre-wrap [overflow-wrap:anywhere] "
                        "bg-transparent loma-doc-editor-field"
                    )
                    .style(f"flex: 1 1 auto; min-height: 0; height: 100%; font-size: {font_px}px;")
                )
                if on_change:
                    editor.on("update:model-value", on_change)
                return editor
            label = ui.label(text or "").classes(
                MARKDOWN_SOURCE_LABEL_CLASSES + " flex-1 min-h-0"
            )
            if font_px:
                label.style(f"font-size: {font_px}px;")
            return label
