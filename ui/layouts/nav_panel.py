# -*- coding: utf-8 -*-
"""Collapsible left nav: branding, Sources hub, output format, extension dropdown/library,
Home + Tips/Settings."""
from nicegui import ui

from config.app_version import APP_VERSION
from pipeline.i18n import section_label_class, t as tr, topbar_tagline_class
from pipeline.output_format import DEFAULT_OUTPUT_FORMAT, output_format_select_options
from pipeline.registry.catalog import EXTENSION_SELECT_NONE
from services.session import settings as session_settings
from services.session import state
from ui.branding import LOMA_ICON_URL
from ui.components.attachments_hub import mount_sources_uploader, render_sources_hub
from ui.components.loma_facts import bind_loma_icon_spark
from ui.themes import registry


_EDITION_COLUMNS = ("core", "extended", "complete")
_EDITION_ROW_KEYS = ("input", "output", "ui", "connectivity", "mode", "extensions")


def _edition_rows_by_column() -> dict[str, list[tuple[str, str]]]:
    """Look up the edition comparison table's row labels/values through i18n
    keys (about.row.<row>.label / about.row.<row>.<column>) so the popup
    follows the active UI language instead of the raw English EDITIONS.md text."""
    import os

    from services.platform_paths import resource_root

    try:
        path = os.path.join(resource_root(), "EDITIONS.md")
        if not os.path.isfile(path):
            return {col: [] for col in _EDITION_COLUMNS}
    except Exception:
        return {col: [] for col in _EDITION_COLUMNS}
    rows: dict[str, list[tuple[str, str]]] = {col: [] for col in _EDITION_COLUMNS}
    for row_key in _EDITION_ROW_KEYS:
        label = tr(f"about.row.{row_key}.label")
        for col in _EDITION_COLUMNS:
            rows[col].append((label, tr(f"about.row.{row_key}.{col}")))
    return rows


def _show_edition_capabilities() -> None:
    rows_by_column = _edition_rows_by_column()
    has_table_data = any(rows_by_column.values())

    def _render_rows(rows: list[tuple[str, str]]) -> None:
        for label, value in rows:
            # flex-nowrap: nicegui's ui.row() wraps by default, so once `value` gets long
            # enough (e.g. Complete's Extensions cell) the icon and label column split
            # onto separate lines instead of staying side by side — see reported screenshot.
            with ui.row().classes("items-start gap-2 mb-2 w-full flex-nowrap"):
                ui.icon("check_circle", size="xs").classes("text-blue-400/80 mt-0.5 shrink-0")
                with ui.column().classes("gap-0 min-w-0 flex-1"):
                    ui.label(label).classes("text-xs font-semibold")
                    ui.label(value).classes("text-xs leading-snug opacity-90")

    with ui.dialog() as dlg, ui.card().classes("w-[460px] p-5 rounded-2xl"):
        with ui.row().classes("items-baseline gap-1.5 mb-3"):
            ui.label(tr("about.title")).classes("text-lg font-bold tracking-[0.35em]")
            ui.label(tr("about.edition_tag")).classes("text-[10px] font-semibold tracking-wide px-1.5 py-0.5 rounded border")
        ui.label(tr("about.cap.heading")).classes("text-[10px] tracking-widest mb-2 opacity-70")

        if has_table_data:
            with ui.tabs().classes("w-full") as tabs:
                tab_core = ui.tab("core", label=tr("about.tab.core"))
                tab_ext = ui.tab("extended", label=tr("about.tab.extended"))
                tab_full = ui.tab("complete", label=tr("about.tab.complete"))
            with ui.tab_panels(tabs, value="extended").classes("w-full p-0 mt-2"):
                with ui.tab_panel("core").classes("p-0"):
                    _render_rows(rows_by_column["core"])
                with ui.tab_panel("extended").classes("p-0"):
                    _render_rows(rows_by_column["extended"])
                with ui.tab_panel("complete").classes("p-0"):
                    _render_rows(rows_by_column["complete"])
        else:
            for cap_key in (
                "about.cap.chat",
                "about.cap.deliverables",
                "about.cap.multimodal",
                "about.cap.modes",
                "about.cap.extensions",
                "about.cap.local",
            ):
                with ui.row().classes("items-start gap-2 mb-2 w-full"):
                    ui.icon("check_circle", size="xs").classes("text-blue-400/80 mt-0.5 shrink-0")
                    ui.label(tr(cap_key)).classes("text-xs leading-snug")
        ui.button(tr("library.close"), on_click=dlg.close).props("flat").classes("mt-2 self-end")
    dlg.open()


def _extension_options() -> dict:
    from pipeline.registry.extension_registry import extension_registry

    opts: dict[str, str] = {}
    for row in extension_registry.dropdown_options():
        val = row.get("value") or ""
        label = row.get("label") or val
        opts[val] = label
    return opts


def _on_extension_change(e) -> None:
    if registry.suppress_extension_change:
        return
    from pipeline.registry.extension_registry import normalize_extension_id
    from ui.layouts.extension_panel import show_extension

    ext_id = normalize_extension_id(getattr(e, "value", None))
    show_extension(ext_id)


def build_nav_panel(t: dict, settings_dialog: ui.dialog, tips_dialog: ui.dialog) -> None:
    registry.input_panel = ui.column().classes(
        f'w-[264px] shrink-0 h-full min-h-0 bg-[{t["sidebar"]}] border border-[{t["border"]}] '
        f"rounded-2xl p-3 flex flex-col flex-nowrap backdrop-blur-md transition-all relative"
    ).props("id=loma-nav-panel").style(f"box-shadow: {t['panel_shadow']};")

    with registry.input_panel:
        with ui.column().classes(
            f"gap-0 w-full shrink-0 mb-3 rounded-xl p-2.5 {t['emboss_bg']}"
        ).style(f"box-shadow: {t['emboss_shadow']};"):
            with ui.row().classes("items-center justify-between w-full flex-nowrap gap-2"):
                with ui.row().classes("items-center gap-2 min-w-0 flex-nowrap"):
                    loma_icon = ui.image(LOMA_ICON_URL).classes("w-6 h-6 rounded shrink-0")
                    bind_loma_icon_spark(loma_icon)
                    with ui.row().classes("items-center gap-1.5 min-w-0 flex-nowrap"):
                        ui.label("LOMA").classes("text-sm font-bold tracking-[0.3em] shrink-0")
                        ui.label(tr("nav.edition_tag")).classes(
                            f"text-[9px] font-semibold tracking-wide px-1.5 py-0.5 rounded border border-[{t['border']}] {t['muted']} shrink-0 whitespace-nowrap cursor-pointer hover:opacity-80"
                        ).on("click", _show_edition_capabilities)

                ui.button(
                    on_click=lambda: (
                        registry.input_panel.set_visibility(False),
                        registry.input_open_btn.set_visibility(True),
                    )
                ).props("flat round dense icon=menu_open").classes(f"shrink-0 {t['icon_btn']}")

            ui.label(tr("topbar.tagline")).classes(
                topbar_tagline_class(t["muted"]) + " leading-snug pr-1"
            )
            ui.label(f"v{APP_VERSION}").classes(topbar_tagline_class(t["muted"]) + " leading-snug opacity-70")

        _BTN_PROPS = "flat dense no-caps align=left no-wrap padding=6px"
        _BTN_CLASSES = f"w-full justify-start text-sm whitespace-nowrap overflow-hidden pl-2 {t['muted']}"

        with ui.column().classes(
            "w-full flex-1 flex flex-col flex-nowrap min-h-0 gap-1 overflow-y-auto overflow-x-hidden loma-scroll"
        ):
            with ui.element("div").classes("relative w-full shrink-0"):
                mount_sources_uploader(t)
                render_sources_hub()

            with ui.column().classes("w-full shrink-0 flex-none gap-3 pt-3"):
                ui.separator().classes(f"{t['separator']}")
                ui.label(tr("panel.output_format")).classes(
                    section_label_class("text-cyan-400")
                )

                def update_output_format(e) -> None:
                    state.current_settings["default_output_format"] = e.value
                    session_settings.save_settings(state.current_settings, quiet=True)

                initial_format = state.current_settings.get(
                    "default_output_format", DEFAULT_OUTPUT_FORMAT
                )
                format_options = output_format_select_options()
                if initial_format not in format_options:
                    initial_format = DEFAULT_OUTPUT_FORMAT

                registry.output_format_select = ui.select(
                    options=format_options,
                    label=tr("output_format.label"),
                    value=initial_format,
                    on_change=update_output_format,
                ).props(t["select_props"]).classes("w-full")

                ui.separator().classes(f"{t['separator']}")
                ui.label(tr("panel.extension")).classes(section_label_class("text-amber-400"))

                with ui.row().classes("w-full items-center gap-2 no-wrap"):
                    ext_opts = _extension_options()
                    with ui.row().classes("flex-1 min-w-0 items-center gap-1 no-wrap"):
                        registry.extension_select = ui.select(
                            options=ext_opts,
                            label=tr("extension.active_label"),
                            value=EXTENSION_SELECT_NONE
                            if EXTENSION_SELECT_NONE in ext_opts
                            else None,
                            on_change=_on_extension_change,
                        ).props(t["select_props"]).classes("flex-1 min-w-0 loma-extension-select")

                    def _sync_extension_options() -> None:
                        from ui.components.extension_library import refresh_extension_select_options

                        refresh_extension_select_options()

                    ui.timer(0.25, _sync_extension_options, once=True)

                ui.button(
                    tr("library.add"),
                    icon="add",
                    on_click=lambda: __import__(
                        "ui.components.extension_library", fromlist=["open_extension_library"]
                    ).open_extension_library(),
                ).props("flat").classes(
                    "w-full text-amber-400 mt-1 bg-amber-500/10 rounded-lg text-xs"
                )

        ui.separator().classes(f"{t['separator']} my-1 shrink-0")

        with ui.column().classes("w-full shrink-0 gap-1"):
            ui.button(tr("tips.dialog.title"), icon="tips_and_updates", on_click=tips_dialog.open).props(
                _BTN_PROPS
            ).classes(f"{_BTN_CLASSES} hover:text-amber-400")
            ui.button(tr("panel.settings"), icon="tune", on_click=settings_dialog.open).props(
                _BTN_PROPS
            ).classes(f"{_BTN_CLASSES} hover:text-blue-400")
