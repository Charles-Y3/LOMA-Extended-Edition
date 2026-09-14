# -*- coding: utf-8 -*-

"""Workspace tab."""

from __future__ import annotations



import asyncio

import os



from nicegui import ui



from extensions.knowledge_vault.corpus.backend_resolver import resolve_kv_backend

from extensions.knowledge_vault.corpus.workspace import get_workspace, reset_workspace

from extensions.knowledge_vault.ui.browse import browse_folder

from extensions.knowledge_vault.ui.corpus_panel import render_corpus_panel

from pipeline.i18n import t as tr





def render_workspace_tab() -> None:

    rebuild_ref: dict = {"fn": lambda: None}



    with ui.column().classes("w-full h-full min-h-0 gap-2 px-2 pt-2 pb-0"):

        ui.label(tr("knowledge_vault.ws_subtitle")).classes(
            "text-[10px] text-gray-500 italic shrink-0"
        )

        with ui.row().classes("w-full items-center justify-between gap-2 shrink-0 flex-wrap"):

            with ui.column().classes("gap-0 flex-1 min-w-0"):

                status_lbl = ui.label(tr("knowledge_vault.ws_add_folder_begin")).classes(

                    "text-[11px] text-gray-500"

                )

                chips_lbl = ui.label("").classes(

                    "text-[10px] text-gray-500"

                )

                with ui.row().classes("w-full items-center gap-2") as prog_row:

                    prog_row.set_visibility(False)

                    prog = ui.linear_progress(value=0, show_value=False).props(

                        "instant-feedback"

                    ).classes("flex-1 min-w-[80px]")

                    prog_label = ui.label("").classes(

                        "text-[10px] text-gray-400 shrink-0"

                    )



            async def _add_folder() -> None:

                path = await asyncio.to_thread(

                    browse_folder, title=tr("knowledge_vault.ws_select_folder")

                )

                if not path or not os.path.isdir(path):

                    return

                w = get_workspace()

                roots = list(w.roots)

                abs_p = os.path.abspath(path)

                if abs_p not in roots:

                    roots.append(abs_p)

                w.set_roots(roots)

                w.build_index(on_done=lambda: rebuild_ref["fn"]())

                rebuild_ref["fn"]()

                ui.notify(tr("knowledge_vault.ws_indexing_started"), color="info")



            def _reset() -> None:

                reset_workspace()

                _mount_panel()

                rebuild_ref["fn"]()



            ui.button(tr("knowledge_vault.ws_new_workspace"), on_click=_reset).props("dense flat").classes(

                "text-[11px] text-gray-400"

            )

            ui.button(tr("knowledge_vault.ws_add_folder"), on_click=_add_folder).props("dense").classes(

                "text-xs bg-emerald-700"

            )



        panel_host = ui.column().classes("w-full flex-1 min-h-0")



        def _status() -> str:

            ws = get_workspace()

            if ws.indexing:

                p = ws.index_progress
                segs = int(p.get("segments") or 0)
                pairs = int(p.get("pairs") or 0)
                cur = int(p.get("current") or 0)
                total = int(p.get("total") or 0)
                if total:
                    return tr(
                        "knowledge_vault.lib_stat_indexing",
                        segments=segs,
                        pairs=pairs,
                        cur=cur,
                        total=total,
                    )
                return f"{segs} · {pairs} · {tr('knowledge_vault.lib_stat_indexing_scan')}"

            if ws.index_error:

                return tr("knowledge_vault.ws_error", error=ws.index_error)

            if ws.ready:

                return tr(

                    "knowledge_vault.ws_ready",

                    segments=len(ws.chunks),

                    folders=len(ws.roots),

                )

            if ws.roots:

                return tr("knowledge_vault.ws_waiting")

            return tr("knowledge_vault.ws_add_folder_begin")



        def _chips() -> str:

            ws = get_workspace()

            if not ws.chunks and not ws.roots:

                return ""

            excluded = ws.excluded_file_count()

            files_disp = (

                tr("knowledge_vault.lib_files_excl", files=ws.file_count, excluded=excluded)

                if excluded

                else ws.file_count

            )

            return tr(

                "knowledge_vault.lib_stat_ready",

                chunks=len(ws.chunks),

                files=files_disp,

                pairs=len(ws.translations.pairs),

            )



        def _mount_panel() -> None:

            panel_host.clear()

            with panel_host:

                render_corpus_panel(

                    get_backend=lambda: resolve_kv_backend("workspace", None),

                    get_tree=lambda: get_workspace().indexed_tree(),

                    on_refresh_tree=lambda fn: rebuild_ref.update(fn=fn),

                )



        def _tick() -> None:

            from extensions.knowledge_vault.passwords import poll_password_dialog



            poll_password_dialog()

            ws = get_workspace()

            status_lbl.set_text(_status())

            chips_lbl.set_text(_chips())

            if ws.indexing:

                p = ws.index_progress

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



        _mount_panel()

        ui.timer(0.4, _tick)

