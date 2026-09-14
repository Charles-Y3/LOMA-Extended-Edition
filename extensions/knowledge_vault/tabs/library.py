# -*- coding: utf-8 -*-
"""Library tab — manage catalogues within the library."""
from __future__ import annotations

import asyncio
import logging
import os

from nicegui import ui

from extensions.knowledge_vault.corpus.library import (
    add_folder_to_library,
    create_library,
    delete_library,
    get_library,
    list_libraries,
    load_manifest,
    resume_pending_semantic_builds,
)
from extensions.knowledge_vault.corpus.types import LibraryManifest
from extensions.knowledge_vault.ui.browse import browse_folder
from extensions.knowledge_vault.ui.corpus_panel import render_corpus_panel
from pipeline.i18n import t as tr


_logger = logging.getLogger(__name__)

# Pseudo library_id for the "All catalogues" dropdown entry — never a real
# manifest id (uuid4 hex), so it can't collide with one.
_ALL_CATALOGUES = "__all__"


def _console_log(msg: str) -> None:
    """Surface per-file indexing warnings (locked/encrypted/conversion-failed files,
    etc.) that would otherwise be silently dropped — build_lexical_index only calls
    log_fn if one is given. Goes through `logging` (not print) since a packaged
    --windowed .exe has no console — print() output vanishes into devnull there, leaving
    a stuck build with no way to see what's happening. See main.py's log file setup."""
    _logger.info(msg)


def _index_chips(manifest: LibraryManifest, *, indexing: bool = False) -> str:
    parts: list[str] = []
    if manifest.lexical_ready:
        parts.append(tr("knowledge_vault.lib_lexical_ok"))
    elif indexing:
        parts.append(tr("knowledge_vault.lib_lexical_busy"))
    if manifest.semantic_ready:
        parts.append(tr("knowledge_vault.lib_semantic_ok"))
    elif manifest.semantic_building or manifest.semantic_enabled:
        parts.append(tr("knowledge_vault.lib_semantic_busy"))
    return " · ".join(parts) or "—"


def _lib_option_label(manifest: LibraryManifest, *, indexing: bool = False) -> str:
    chips = _index_chips(manifest, indexing=indexing)
    return f"{manifest.name} — {chips}"


def render_library_tab() -> None:
    state: dict = {"open_id": "", "rebuild": lambda: None}
    lib_sel_ref: dict = {"el": None}
    semantic_btn_ref: dict = {"el": None}

    with ui.column().classes("w-full h-full min-h-0 gap-2 px-2 pt-2 pb-0"):
        with ui.row().classes("w-full gap-2 shrink-0 items-center"):
            lib_sel = ui.select(
                {},
                label=tr("knowledge_vault.lib_catalogue"),
                value=None,
                on_change=lambda e: _on_lib_change(e.value),
            ).props("dense outlined dark options-dense").classes("flex-1 min-w-0 text-xs")
            lib_sel_ref["el"] = lib_sel

            def _open_create_dialog() -> None:
                with ui.dialog() as dlg, ui.card().classes("gap-3 p-4 min-w-[280px]"):
                    ui.label(tr("knowledge_vault.lib_new_catalogue")).classes("text-sm font-medium")
                    name_in = ui.input(tr("knowledge_vault.lib_catalogue_name")).props("dense outlined dark").classes(
                        "w-full text-xs"
                    )

                    async def _confirm() -> None:
                        folder = await asyncio.to_thread(
                            browse_folder, title=tr("knowledge_vault.lib_select_folder")
                        )
                        if not folder or not os.path.isdir(folder):
                            ui.notify(tr("knowledge_vault.lib_folder_required"), color="warning")
                            return
                        lib = create_library(
                            (name_in.value or "").strip() or os.path.basename(folder),
                            roots=[folder],
                        )
                        state["open_id"] = lib.library_id
                        corp = get_library(lib.library_id)
                        corp.build_lexical_index(
                            log_fn=_console_log,
                            on_done=lambda: (
                                _refresh_libs(),
                                _show_detail(),
                                state["rebuild"](),
                            )
                        )
                        dlg.close()
                        _refresh_libs()
                        _show_detail()
                        ui.notify(tr("knowledge_vault.lib_created", name=lib.name), color="positive")

                    with ui.row().classes("w-full justify-end gap-2"):
                        ui.button(tr("common.cancel"), on_click=dlg.close).props("dense flat")
                        ui.button(tr("knowledge_vault.lib_create"), on_click=_confirm).props(
                            "dense flat color=primary"
                        )
                dlg.open()

            ui.button(icon="add", on_click=_open_create_dialog).props(
                "dense outline"
            ).classes("shrink-0 self-stretch").tooltip(tr("knowledge_vault.lib_new_tooltip"))

        detail_host = ui.column().classes("w-full flex-1 min-h-0 gap-1 overflow-hidden")

    def _on_lib_change(library_id: str | None) -> None:
        state["open_id"] = (library_id or "").strip()
        _show_detail()

    def _refresh_libs() -> None:
        libs = list_libraries()
        corp = get_library(state["open_id"]) if state.get("open_id") and state["open_id"] != _ALL_CATALOGUES else None
        options = {
            lib.library_id: _lib_option_label(
                lib,
                indexing=bool(corp and corp.library_id == lib.library_id and corp.indexing),
            )
            for lib in libs
        }
        if libs:
            options = {_ALL_CATALOGUES: tr("knowledge_vault.lib_all_catalogues"), **options}
        sel = lib_sel_ref["el"]
        if sel is None:
            return
        sel.options = options
        if state.get("open_id") not in options:
            state["open_id"] = next(iter(options), "")
        if sel.value != (state["open_id"] or None):
            sel.value = state["open_id"] or None
            sel.update()
        elif options != getattr(sel, "_doc_intel_opts", {}):
            setattr(sel, "_doc_intel_opts", dict(options))
            sel.update()

    def _confirm_delete(library_id: str, name: str) -> None:
        with ui.dialog() as dlg, ui.card().classes("gap-3 p-4 min-w-[280px]"):
            ui.label(tr("knowledge_vault.lib_delete_title", name=name)).classes("text-sm font-medium")
            ui.label(tr("knowledge_vault.lib_delete_body")).classes("text-[11px] text-gray-400")

            def _do_delete() -> None:
                delete_library(library_id)
                if state.get("open_id") == library_id:
                    state["open_id"] = ""
                dlg.close()
                _refresh_libs()
                _show_detail()
                from services.session.workflow_control import schedule_on_ui

                schedule_on_ui(
                    lambda: ui.notify(tr("knowledge_vault.lib_deleted"), color="positive")
                )

            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(tr("common.cancel"), on_click=dlg.close).props("dense flat")
                ui.button(tr("common.delete"), on_click=_do_delete).props("dense").classes(
                    "bg-red-800 text-xs"
                )
        dlg.open()

    def _all_catalogues_stat() -> str:
        ready = [lib for lib in list_libraries() if lib.lexical_ready]
        chunks = sum(m.chunk_count for m in ready)
        files = sum(m.file_count for m in ready)
        pairs = sum(len(get_library(m.library_id).translations.pairs) for m in ready)
        return tr(
            "knowledge_vault.lib_stat_all",
            catalogues=len(ready), chunks=chunks, files=files, pairs=pairs,
        )

    def _show_all_detail() -> None:
        from extensions.knowledge_vault.corpus.backend_resolver import resolve_kv_backend

        with ui.row().classes("w-full items-center gap-2 shrink-0"):
            with ui.column().classes("gap-0 min-w-0 shrink"):
                ui.label(tr("knowledge_vault.lib_all_catalogues")).classes(
                    "text-sm font-medium text-gray-200"
                )
                status = ui.label(_all_catalogues_stat()).classes("text-[10px] text-gray-500")

        def _tick_all() -> None:
            status.set_text(_all_catalogues_stat())
            _refresh_libs()

        ui.timer(0.4, _tick_all)

        with ui.column().classes("w-full flex-1 min-h-0 overflow-hidden"):
            render_corpus_panel(
                get_backend=lambda: resolve_kv_backend("all", None),
                get_tree=lambda: {},
                library_id=None,
                semantic_ready=lambda: False,
                show_tree=False,
            )

    def _show_detail() -> None:
        detail_host.clear()
        lid = state.get("open_id") or ""
        with detail_host:
            if not lid:
                ui.label(tr("knowledge_vault.lib_select_or_create")).classes(
                    "text-[11px] text-gray-500 p-1 shrink-0"
                )
                return

            if lid == _ALL_CATALOGUES:
                _show_all_detail()
                return

            corp = get_library(lid)
            manifest = load_manifest(lid)

            with ui.row().classes("w-full items-center gap-2 shrink-0"):
                with ui.column().classes("gap-0 min-w-0 shrink"):
                    ui.label(manifest.name).classes("text-sm font-medium text-gray-200")
                    chips_lbl = ui.label(
                        _index_chips(manifest, indexing=corp.indexing)
                    ).classes("text-[10px] text-gray-500")
                    status = ui.label("").classes("text-[10px] text-gray-500")
                    with ui.row().classes("w-full items-center gap-2") as prog_row:
                        prog_row.set_visibility(False)
                        prog = ui.linear_progress(value=0, show_value=False).props(
                            "instant-feedback"
                        ).classes("flex-1 min-w-[80px]")
                        prog_label = ui.label("").classes(
                            "text-[10px] text-gray-400 shrink-0"
                        )

                with ui.row().classes("ml-auto gap-2 shrink-0 items-center"):

                    async def _add_folder() -> None:
                        folder = await asyncio.to_thread(
                            browse_folder, title=tr("knowledge_vault.lib_add_folder_title")
                        )
                        if not folder or not os.path.isdir(folder):
                            return
                        add_folder_to_library(lid, folder)
                        corp.manifest = load_manifest(lid)
                        corp.build_lexical_index(
                            log_fn=_console_log,
                            on_done=lambda: (
                                _refresh_libs(),
                                _show_detail(),
                                state["rebuild"](),
                            )
                        )
                        _refresh_libs()
                        ui.notify(tr("knowledge_vault.lib_folder_merged"), color="info")

                    ui.button(tr("knowledge_vault.lib_add_folder"), on_click=_add_folder).props("dense").classes(
                        "text-xs"
                    )
                    if not (
                        manifest.semantic_ready
                        or manifest.semantic_building
                        or manifest.semantic_enabled
                    ):
                        def _on_semantic_click(c=corp) -> None:
                            if not c.manifest.lexical_ready:
                                ui.notify(tr("knowledge_vault.lib_semantic_needs_lexical"), type="warning")
                                return

                            def _on_semantic_done(error: str = "") -> None:
                                # build_semantic_index() runs this from its background
                                # worker thread — NiceGUI element/notify calls need the
                                # UI event loop, not a bare thread (previously this ran
                                # unguarded; adding the ui.notify call below made the
                                # existing gap worth closing at the same time).
                                from services.session.workflow_control import schedule_on_ui

                                def _apply() -> None:
                                    if error:
                                        ui.notify(tr("knowledge_vault.lib_semantic_failed", error=error[:200]), type="negative")
                                    _refresh_libs()
                                    _show_detail()
                                    state["rebuild"]()

                                schedule_on_ui(_apply)

                            c.build_semantic_index(log_fn=_console_log, on_done=_on_semantic_done)

                        semantic_btn = ui.button(
                            tr("knowledge_vault.lib_semantic"),
                            on_click=_on_semantic_click,
                        ).props("dense flat").classes("text-[11px]")
                        semantic_btn_ref["el"] = semantic_btn
                    ui.button(
                        tr("common.delete"),
                        on_click=lambda: _confirm_delete(lid, manifest.name),
                    ).props("dense flat").classes("text-[11px] text-red-400")

            def _stat() -> str:
                m = corp.manifest
                pairs = len(corp.translations.pairs)
                if corp.indexing:
                    p = corp.index_progress
                    segs = int(p.get("segments") or 0)
                    pair_n = int(p.get("pairs") or 0)
                    cur = int(p.get("current") or 0)
                    total = int(p.get("total") or 0)
                    parts = [
                        tr(
                            "knowledge_vault.lib_stat_indexing",
                            segments=segs,
                            pairs=pair_n,
                            cur=cur,
                            total=total,
                        )
                        if total
                        else f"{segs} · {pair_n} · {tr('knowledge_vault.lib_stat_indexing_scan')}",
                    ]
                else:
                    excluded = corp.excluded_file_count()
                    files_disp = (
                        tr("knowledge_vault.lib_files_excl", files=m.file_count, excluded=excluded)
                        if excluded
                        else m.file_count
                    )
                    parts = [
                        tr(
                            "knowledge_vault.lib_stat_ready",
                            chunks=m.chunk_count,
                            files=files_disp,
                            pairs=pairs,
                        ),
                    ]
                if corp.semantic_building:
                    parts.append(tr("knowledge_vault.lib_semantic_build"))
                return " · ".join(parts)

            panel_box = ui.column().classes("w-full flex-1 min-h-0 overflow-hidden")

            def _mount() -> None:
                from extensions.knowledge_vault.corpus.backend_resolver import resolve_kv_backend

                panel_box.clear()
                with panel_box:
                    render_corpus_panel(
                        get_backend=lambda: resolve_kv_backend("selected", lid),
                        get_tree=lambda: corp.indexed_tree() or {},
                        library_id=lid,
                        semantic_ready=lambda: corp.manifest.semantic_ready,
                        on_refresh_tree=lambda fn: state.update(rebuild=fn),
                    )

            shown_error = {"text": ""}

            def _tick() -> None:
                from extensions.knowledge_vault.passwords import poll_password_dialog

                poll_password_dialog()
                corp.manifest = load_manifest(lid)
                status.set_text(_stat())
                chips_lbl.set_text(_index_chips(corp.manifest, indexing=corp.indexing))
                if not corp.indexing:
                    err = str(corp.index_progress.get("error") or "")
                    if err and err != shown_error["text"]:
                        shown_error["text"] = err
                        ui.notify(tr("knowledge_vault.lib_index_error", error=err), type="negative")
                    elif not err:
                        shown_error["text"] = ""
                if corp.indexing:
                    p = corp.index_progress
                    total = int(p.get("total") or 0)
                    cur = int(p.get("current") or 0)
                    name = str(p.get("filename") or "")
                    prog_row.set_visibility(True)
                    if total > 0:
                        prog.set_value(min(1.0, cur / total))
                        prog_label.set_text(f"{cur}/{total} — {name}")
                    else:
                        prog.set_value(0)
                        prog_label.set_text(name or tr("knowledge_vault.lib_scanning"))
                else:
                    prog_row.set_visibility(False)
                    prog.set_value(0)
                    prog_label.set_text("")
                btn = semantic_btn_ref.get("el")
                if btn is not None and (
                    corp.manifest.semantic_ready
                    or corp.manifest.semantic_building
                    or corp.manifest.semantic_enabled
                ):
                    btn.set_visibility(False)
                _refresh_libs()

            _mount()
            state["rebuild"] = _mount
            ui.timer(0.4, _tick, once=False)

    _refresh_libs()
    _show_detail()
    resume_pending_semantic_builds()
