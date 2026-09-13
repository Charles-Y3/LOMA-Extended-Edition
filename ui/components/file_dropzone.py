# -*- coding: utf-8 -*-
from nicegui import ui

from pipeline.i18n import is_cjk_locale, t as tr
from ui.themes import registry, tokens

_EMPTY_DROPZONE = (
    "w-full relative border border-dashed transition-colors rounded-lg "
    "px-3 py-2.5 min-h-[48px] overflow-hidden cursor-pointer"
)


def render_empty_dropzone(refresh_callback) -> None:
    """Visual drop hint only — uploads go through the persistent sources uploader overlay."""
    t = tokens.get_theme()

    def pick_files() -> None:
        if registry.sources_uploader:
            registry.sources_uploader.run_method("pickFiles")

    with ui.element("div").classes(f"{_EMPTY_DROPZONE} {t['dropzone']}").on("click", pick_files):
        with ui.row().classes("w-full items-center justify-center gap-2 text-center"):
            ui.icon("cloud_upload", size="18px").classes(t["dropzone_icon"])
            ui.label(tr("sources.drop_hint")).classes(
                f"{'text-xs' if is_cjk_locale() else 'text-[10px]'} leading-snug {t['dropzone_text']}"
            )
