# -*- coding: utf-8 -*-
"""Extension Library — browse and install extensions."""
from __future__ import annotations

import shutil
from pathlib import Path

from nicegui import ui

from pipeline.i18n import t as tr
from ui.themes import tokens
from pipeline.extension_i18n import extension_description, extension_title
from pipeline.registry.extension_registry import extension_registry
from services.plugins.catalog import load_extension_catalog
from services.plugins.extension_categories import (
    LIBRARY_CATEGORIES,
    category_chip_classes,
    category_chip_style,
    category_display,
    category_icon,
    category_icon_class,
    category_pill_class,
    normalize_library_category,
)
from services.plugins.extension_prefs import is_extension_enabled, set_extension_enabled
from services.plugins.paths import ensure_user_extensions_root
from services.session import settings as session_settings
from services.session import state


def refresh_extension_select_options() -> None:
    # ui/layouts/input_panel.py was renamed to ui/layouts/nav_panel.py; this used to
    # import a `refresh_extension_picker` from the old module path, which no longer
    # exists — every call here raised ModuleNotFoundError before ever reaching the
    # set_options()/set_value() repair below, so a stale "Active Extension" selection
    # (e.g. after enabling/disabling an extension) never got reset back to "None" and
    # could render blank instead.
    from ui.themes import registry

    if registry.extension_select is not None and hasattr(registry.extension_select, "set_options"):
        from ui.layouts.nav_panel import _extension_options
        from pipeline.registry.extension_registry import EXTENSION_SELECT_NONE

        opts = _extension_options()
        registry.extension_select.set_options(opts)
        if registry.extension_select.value not in opts:
            registry.extension_select.set_value(
                EXTENSION_SELECT_NONE if EXTENSION_SELECT_NONE in opts else None
            )


def _dedupe_catalog(catalog: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for row in catalog:
        eid = str(row.get("id") or "").strip()
        if not eid or eid in seen:
            continue
        seen.add(eid)
        out.append(row)
    return out


def _filter_catalog(
    catalog: list[dict],
    q: str,
    *,
    category: str | None = None,
) -> list[dict]:
    catalog = _dedupe_catalog(catalog)
    catalog = [
        row
        for row in catalog
        if row.get("show_in_library", True) is not False
    ]
    if category and category in LIBRARY_CATEGORIES:
        catalog = [
            row
            for row in catalog
            if normalize_library_category(str(row.get("category") or "")) == category
        ]
    q = (q or "").strip().lower()
    if not q:
        return list(catalog)
    words = [w for w in q.split() if w]
    out = []
    for row in catalog:
        eid = str(row.get("id") or "").strip()
        hay = " ".join(
            [
                extension_title(eid, row.get("title") or ""),
                extension_description(eid, row.get("description") or ""),
                (row.get("category") or ""),
                category_display(str(row.get("category") or "")),
                eid,
            ]
        ).lower()
        if all(w in hay for w in words):
            out.append(row)
    return out


def open_extension_library() -> None:
    if not extension_registry._extensions:
        extension_registry.discover()

    catalog_cache = _dedupe_catalog(load_extension_catalog())
    active_category: dict[str, str | None] = {"value": None}

    with ui.dialog() as dialog, ui.card().classes(
        "loma-ext-library p-0 w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden"
    ):
        with ui.row().classes("w-full items-center justify-between px-4 py-3 shrink-0 border-b border-white/10"):
            ui.label(tr("library.title")).classes("text-base font-bold tracking-wide")
            ui.button(icon="close", on_click=dialog.close).props("flat round dense")

        with ui.column().classes("px-4 py-2 shrink-0 gap-2"):
            search = ui.input(placeholder=tr("library.search")).props(
                "dense outlined clearable"
            ).classes("w-full")
            chip_row = ui.row().classes("w-full gap-1 flex-wrap items-center")

        list_host = ui.column().classes("loma-scroll w-full flex-1 gap-1 px-4 pb-4 min-h-[200px]").style(
            "min-height: 360px; max-height: 55vh; overflow-y: auto"
        )

        def _search_query() -> str:
            val = search.value
            if val is None:
                return ""
            return str(val).strip()

        def _render_category_chips() -> None:
            chip_row.clear()
            cat = active_category["value"]

            def _chip(label: str, key: str | None, selected: bool) -> None:
                def _pick(k=key) -> None:
                    active_category["value"] = k
                    _render_category_chips()
                    extension_list.refresh()

                ui.button(label, on_click=_pick).props("flat dense size=sm no-caps").classes(
                    category_chip_classes(key, selected=selected)
                ).style(category_chip_style(key))

            with chip_row:
                ui.label(tr("library.category_label")).classes(
                    "text-[10px] opacity-60 mr-1 shrink-0"
                )
                _chip(tr("library.category_all"), None, cat is None)
                for key in LIBRARY_CATEGORIES:
                    _chip(tr(f"library.category_{key}"), key, cat == key)

        with list_host:
            @ui.refreshable
            def extension_list() -> None:
                q = _search_query()
                catalog = _filter_catalog(
                    catalog_cache,
                    q,
                    category=active_category["value"],
                )
                q_active = bool(q)
                cat_active = bool(active_category["value"])

                if not catalog:
                    if q_active or cat_active:
                        ui.label(tr("library.no_match")).classes("text-sm opacity-60 mt-4")
                    else:
                        ui.label(tr("library.empty")).classes("text-sm opacity-60 mt-4")
                    return

                if q_active or cat_active:
                    ui.label(tr("library.search_results", count=len(catalog))).classes(
                        "text-[10px] opacity-60 mb-2"
                    )
                for row in catalog:
                    _render_extension_card(row, extension_list.refresh)

            extension_list()

        search.on("update:model-value", lambda _: extension_list.refresh())
        search.on("clear", lambda: (search.set_value(""), extension_list.refresh()))
        _render_category_chips()
        dialog.open()


def _render_extension_card(row: dict, refresh_fn) -> None:
    ext_id = row.get("id") or ""
    bundled = bool(row.get("bundled", True))
    title = extension_title(ext_id, row.get("title") or ext_id)
    desc = extension_description(ext_id, row.get("description") or "")
    category = category_display(str(row.get("category") or ""))
    cat_key = normalize_library_category(str(row.get("category") or ""))
    pill_cls = category_pill_class(cat_key)
    enabled = is_extension_enabled(ext_id)
    is_in = _is_installed(ext_id, bundled)

    with ui.card().classes("w-full p-3 bg-white/5 border border-white/10 rounded-xl"):
        with ui.row().classes("w-full items-start gap-3 no-wrap"):
            ui.icon(category_icon(cat_key), size="28px").classes(
                f"{category_icon_class(cat_key)} shrink-0 mt-0.5"
            )
            with ui.column().classes("flex-1 min-w-0 gap-0.5"):
                with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                    ui.label(title).classes("text-sm font-semibold")
                    ui.label(category).classes(
                        f"text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded border {pill_cls}"
                    )
                    if enabled:
                        ui.badge(tr("library.enabled")).props("color=positive dense").classes(
                            "text-[9px]"
                        )
                    else:
                        ui.badge(tr("library.disabled")).props("color=grey dense").classes(
                            "text-[9px]"
                        )
                    warn_ram = row.get("warn_ram_gb")
                    if warn_ram is not None:
                        try:
                            import psutil

                            ram_gb = float(psutil.virtual_memory().total) / (1024**3)
                            if ram_gb < float(warn_ram):
                                ui.badge(tr("library.low_ram_badge")).props(
                                    "color=warning outline dense"
                                ).classes("text-[9px]").tooltip(
                                    tr("library.low_ram_tooltip", gb=int(warn_ram))
                                )
                        except Exception:
                            pass
                ui.label(desc).classes("text-xs opacity-75 break-words leading-snug")
            with ui.column().classes("shrink-0 items-end gap-1"):
                if bundled:
                    if enabled:

                        def _disable(eid=ext_id, r=row, title=title):
                            _disable_with_uninstall_prompt(eid, r, title, refresh_fn)

                        ui.button(tr("library.disable"), on_click=_disable).props(
                            "flat dense size=sm color=negative"
                        )
                    else:

                        def _enable(eid=ext_id, r=row, title=title):
                            _enable_with_requirements_check(eid, r, title, refresh_fn)

                        ui.button(tr("library.enable"), on_click=_enable).props(
                            "flat dense size=sm color=positive"
                        )
                elif is_in:
                    ui.button(
                        tr("library.remove"),
                        on_click=lambda eid=ext_id: _remove_plugin(eid, refresh_fn),
                    ).props("flat dense size=sm color=negative")
                else:

                    def _install(eid=ext_id, r=row):
                        _install_plugin(eid, r, refresh_fn)

                    ui.button(tr("library.install"), on_click=_install).props(
                        "flat dense size=sm color=primary"
                    )


def _enable_with_requirements_check(ext_id: str, row: dict, title: str, refresh_fn) -> None:
    """Enable only after every catalog `requires` key is satisfied (full stack)."""
    from pipeline.gap_handler import offer_requirement_installer, requirement_status
    from services.bootstrap.connectivity import check_connectivity

    def _finish_enable() -> None:
        set_extension_enabled(ext_id, True)
        refresh_extension_select_options()
        refresh_fn()

    missing = [k for k in (row.get("requires") or []) if not requirement_status(k)]
    if not missing:
        _finish_enable()
        return

    t = tokens.get_theme()
    with ui.dialog() as dialog, ui.card().classes("p-4 gap-3 max-w-md"):
        ui.label(tr("library.requires_download_title")).classes(
            "text-sm font-bold text-amber-500"
        )
        ui.markdown(
            tr("library.requires_download_body", title=title)
            + f"\n\n`{'`, `'.join(missing)}`"
        ).classes(f"text-xs {t['text']}")
        status = ui.label("").classes(f"text-xs {t['muted']}")
        status.set_visibility(False)

        def _install_next(keys: list[str]) -> None:
            if not keys:
                _finish_enable()
                return
            key = keys[0]
            rest = keys[1:]

            def _after() -> None:
                if requirement_status(key):
                    _install_next(rest)
                else:
                    status.set_visibility(True)
                    status.set_text(tr("library.requires_install_failed", key=key))
                    ui.notify(tr("library.requires_install_failed", key=key), type="negative")

            offer_requirement_installer(key, on_success=_after)

        def _download() -> None:
            conn = check_connectivity()
            if not conn.online:
                status.set_visibility(True)
                status.set_text(tr("library.offline_retry"))
                ui.notify(tr("setup.offline_no_download"), type="warning")
                return
            dialog.close()
            _install_next(list(missing))

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(tr("common.cancel"), on_click=dialog.close).props("flat dense")
            ui.button(tr("library.download_now"), on_click=_download).props(
                "dense color=primary"
            )
    dialog.open()


def _disable_with_uninstall_prompt(ext_id: str, row: dict, title: str, refresh_fn) -> None:
    """Disable; optionally uninstall orphaned add-on packages."""
    from pipeline.gap_handler import (
        offer_requirement_uninstaller,
        orphaned_requirements_for_extension,
    )

    def _just_disable() -> None:
        set_extension_enabled(ext_id, False)
        refresh_extension_select_options()
        refresh_fn()

    orphans = orphaned_requirements_for_extension(ext_id)
    if not orphans:
        _just_disable()
        return

    t = tokens.get_theme()
    with ui.dialog() as dialog, ui.card().classes("p-4 gap-3 max-w-md"):
        ui.label(tr("library.disable_uninstall_title")).classes("text-sm font-bold")
        ui.markdown(
            tr("library.disable_uninstall_body", title=title, keys=", ".join(orphans))
        ).classes(f"text-xs {t['text']}")

        def _keep() -> None:
            dialog.close()
            _just_disable()

        def _remove() -> None:
            dialog.close()
            for key in orphans:
                offer_requirement_uninstaller(key)
            _just_disable()
            ui.notify(tr("library.tools_removed"), type="positive")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(tr("common.cancel"), on_click=dialog.close).props("flat dense")
            ui.button(tr("library.keep_tools"), on_click=_keep).props("flat dense")
            ui.button(tr("library.remove_tools"), on_click=_remove).props(
                "dense color=negative"
            )
    dialog.open()


def _is_installed(ext_id: str, bundled: bool) -> bool:
    if bundled and ext_id in extension_registry._extensions:
        return True
    return ext_id in (state.current_settings.get("installed_extensions") or [])


def _install_plugin(ext_id: str, row: dict, refresh_fn) -> None:
    set_extension_enabled(ext_id, True)
    installed = list(state.current_settings.get("installed_extensions") or [])
    if ext_id not in installed:
        installed.append(ext_id)
    state.current_settings["installed_extensions"] = installed
    session_settings.save_settings(state.current_settings, quiet=True)
    ensure_user_extensions_root()
    ui.notify(tr("library.install_placeholder", id=ext_id), type="info")
    refresh_extension_select_options()
    refresh_fn()


def _remove_plugin(ext_id: str, refresh_fn) -> None:
    set_extension_enabled(ext_id, False)
    installed = [x for x in (state.current_settings.get("installed_extensions") or []) if x != ext_id]
    state.current_settings["installed_extensions"] = installed
    session_settings.save_settings(state.current_settings, quiet=True)
    root = Path(ensure_user_extensions_root()) / ext_id
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)
    extension_registry._extensions.pop(ext_id, None)
    refresh_extension_select_options()
    ui.notify(tr("library.removed", id=ext_id), type="positive")
    refresh_fn()
