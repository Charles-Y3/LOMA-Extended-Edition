# -*- coding: utf-8 -*-
"""Shared Source / Viewer mode helpers for Preview and viewer extensions."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from pipeline.i18n import t as tr


def normalize_source_viewer_mode(mode: str | None) -> str:
    """viewer | source (accepts legacy markup / markdown)."""
    m = (mode or "viewer").strip().lower()
    if m in ("markdown", "source"):
        return "source"
    if m in ("markup", "viewer"):
        return "viewer"
    return "viewer"


def is_viewer_mode(mode: str | None) -> bool:
    return normalize_source_viewer_mode(mode) == "viewer"


def supports_viewer_mode(output_type: str | None) -> bool:
    """HTML Viewer is document-only; slides use Source markdown for now."""
    return (output_type or "").strip().lower() == "document"


def mode_hint(mode: str | None, *, editable: bool = False, output_type: str = "document") -> str:
    if is_viewer_mode(mode):
        return tr("preview.hint_viewer")
    if (output_type or "").strip().lower() == "presentation":
        return tr("preview.hint_presentation_source")
    if editable:
        return tr("preview.hint_source")
    return tr("viewer.hint_source")


def build_source_viewer_pills(
    container,
    *,
    mode: str,
    on_change: Callable[[str], None],
    show_viewer: bool = True,
) -> None:
    use_viewer = is_viewer_mode(mode)
    with container:
        with ui.row().classes(
            "items-center gap-0 shrink-0 rounded border border-white/10 overflow-hidden"
        ):
            ui.button(
                tr("preview.toggle_to_source"),
                on_click=lambda: on_change("source"),
            ).props("flat dense no-caps").classes(
                "text-[9px] px-2 min-h-0 "
                + ("bg-amber-500/20 text-amber-300" if not use_viewer or not show_viewer else "text-gray-500")
            )
            if show_viewer:
                ui.button(
                    tr("preview.toggle_to_viewer"),
                    on_click=lambda: on_change("viewer"),
                ).props("flat dense no-caps").classes(
                    "text-[9px] px-2 min-h-0 "
                    + ("bg-amber-500/20 text-amber-300" if use_viewer else "text-gray-500")
                )
