# -*- coding: utf-8 -*-
"""Local file drop zone — same look as Sources panel, custom upload handler."""
from __future__ import annotations

from collections.abc import Callable

from nicegui import ui

from pipeline.i18n import is_cjk_locale, t as tr
from ui.themes import tokens

_EMPTY_DROPZONE = (
    "w-full relative border border-dashed transition-colors rounded-lg "
    "px-3 py-2.5 min-h-[48px] overflow-hidden cursor-pointer"
)

_DRAG_ACTIVE_CLASS = "loma-dropzone-active"


def wire_native_drop(zone: ui.element, upload: ui.upload) -> None:
    """Make a decorative dropzone `<div>` actually accept an HTML5 file drop.

    `ui.upload` renders a real `<input type=file>` internally, but every dropzone
    in this app draws its own decorative div on top (for the icon/label/click-to-pick
    look) and hides the upload widget with CSS — so a browser drop landed on the
    visible div, which had no drag handlers at all, and could never reach the
    hidden input. This wires real dragenter/dragover/drop listeners onto the visible
    div: dragenter/dragleave toggle a highlight class, and drop populates the
    hidden `<input type=file>` via the DataTransfer API and fires a `change` event,
    which is exactly what the file picker does — Quasar's uploader then picks it up
    as normal. Must be called after both `zone` and `upload` are mounted (their DOM
    elements need to exist)."""
    ui.run_javascript(
        f"""
        (function() {{
            const zone = document.getElementById("c{zone.id}");
            const root = document.getElementById("c{upload.id}");
            if (!zone || !root) return;
            const input = root.querySelector('input[type=file]');
            if (!input) return;
            let depth = 0;
            zone.addEventListener('dragenter', (e) => {{
                e.preventDefault();
                depth++;
                zone.classList.add('{_DRAG_ACTIVE_CLASS}');
            }});
            zone.addEventListener('dragover', (e) => {{ e.preventDefault(); }});
            zone.addEventListener('dragleave', (e) => {{
                e.preventDefault();
                depth = Math.max(0, depth - 1);
                if (depth === 0) zone.classList.remove('{_DRAG_ACTIVE_CLASS}');
            }});
            zone.addEventListener('drop', (e) => {{
                e.preventDefault();
                depth = 0;
                zone.classList.remove('{_DRAG_ACTIVE_CLASS}');
                const files = e.dataTransfer && e.dataTransfer.files;
                if (!files || !files.length) return;
                const dt = new DataTransfer();
                for (const f of files) dt.items.add(f);
                input.files = dt.files;
                input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            }});
        }})();
        """
    )


_SOURCE_CHIP = (
    "w-full items-center justify-between gap-2 flex-nowrap group "
    "rounded-lg px-3 py-2 min-h-[36px]"
)


def render_local_dropzone(
    *,
    on_upload: Callable,
    accept: str = ".pdf,.docx,.txt,.md,.pptx,.xlsx,.csv,.wav,.mp3,.m4a",
    multiple: bool = True,
    hint: str | None = None,
) -> ui.upload:
    theme = tokens.get_theme()
    upload = ui.upload(
        on_upload=on_upload,
        auto_upload=True,
        multiple=multiple,
    ).props(f"accept={accept}").classes("hidden")

    def pick_files() -> None:
        upload.run_method("pickFiles")

    label = hint or tr("sources.drop_hint")
    with ui.element("div").classes(f"{_EMPTY_DROPZONE} {theme['dropzone']}").on("click", pick_files) as zone:
        with ui.row().classes("w-full items-center justify-center gap-2 text-center"):
            ui.icon("cloud_upload", size="18px").classes(theme["dropzone_icon"])
            ui.label(label).classes(
                f"{'text-xs' if is_cjk_locale() else 'text-[10px]'} leading-snug {theme['dropzone_text']}"
            )
    wire_native_drop(zone, upload)
    return upload


def render_upload_chip(filename: str, *, on_remove: Callable[[], None]) -> None:
    """Show a selected file like the Sources panel chip."""
    theme = tokens.get_theme()
    with ui.row().classes(f"{_SOURCE_CHIP} {theme['file_card']} w-full"):
        with ui.row().classes("items-center gap-2 flex-1 min-w-0 flex-nowrap"):
            ui.icon("audio_file", size="18px").classes("text-emerald-400 shrink-0")
            ui.label(filename).classes(
                f"text-[12px] font-semibold truncate {theme['file_card_text']}"
            )
        ui.button(icon="close", on_click=on_remove).props("flat round dense size=sm").classes(
            f"{theme['muted']} opacity-70 group-hover:opacity-100 hover:text-red-400 shrink-0"
        )


def render_local_upload_slot(
    *,
    filename: str,
    on_upload: Callable,
    on_clear: Callable[[], None],
    accept: str = ".wav,.mp3,.m4a",
    hint: str | None = None,
) -> ui.upload | None:
    """Drop zone when empty; file chip when a file is selected."""
    if (filename or "").strip():
        render_upload_chip(filename, on_remove=on_clear)
        return None
    return render_local_dropzone(on_upload=on_upload, accept=accept, multiple=False, hint=hint)
