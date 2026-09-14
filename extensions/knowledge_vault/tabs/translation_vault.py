# -*- coding: utf-8 -*-
"""Translation Vault tab — browse, edit, and delete translation pairs synced into
the Translation Vault store (extensions/knowledge_vault/translation_vault_store.py)
from every catalogue's re-index. The store is its own thing, decoupled from any
one catalogue: a pair can carry sources from several catalogues, and deleting a
catalogue only strips the sources it contributed (see remove_library_from_vault),
not the pair itself. Session/workspace documents never reach this store at all —
see the note rendered below the search box. Search-only listing: nothing loads
until the user types a query, matching the Formslator glossary tab's approach to
avoid rendering the whole vault at once."""
from __future__ import annotations

from typing import Any

from nicegui import ui

from pipeline.i18n import t as tr

_PAGE_SIZE = 60

_tv_ui: dict[str, Any] = {
    "search": "",
    "page": 0,
    "selected": set(),
    "flagged_only": False,
    "show_all": False,
}


def _matches(pair, query: str) -> bool:
    hay = f"{pair.source_text} {pair.target_text}".lower()
    return query in hay


def render_translation_vault_tab() -> None:
    from extensions.knowledge_vault.translation import (
        describe_languages,
        find_conflicting_pairs,
        needs_review,
    )
    from extensions.knowledge_vault.translation_vault_store import (
        best_source,
        delete_all_vault_pairs,
        delete_vault_pairs,
        library_display_name,
        list_pairs,
        mark_vault_pair_reviewed,
        update_vault_pair,
    )
    from extensions.knowledge_vault.ui.path_links import resolve_abs_path
    from services.platform_paths import open_file_in_os

    def _confirm_delete(ids: set[str], label: str) -> None:
        with ui.dialog() as dlg, ui.card().classes("gap-2 p-4 min-w-[300px]"):
            ui.label(label).classes("text-sm")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button(tr("config.cancel"), on_click=dlg.close).props("flat dense")

                def _do() -> None:
                    n = delete_vault_pairs(ids)
                    _tv_ui["selected"].clear()
                    dlg.close()
                    ui.notify(tr("knowledge_vault.tv_deleted", count=n), type="positive")
                    _render_list.refresh()

                ui.button(tr("knowledge_vault.tv_delete"), on_click=_do).props(
                    "color=negative dense"
                )
        dlg.open()

    def _confirm_delete_all(total: int) -> None:
        label = tr("knowledge_vault.tv_confirm_delete_all", count=total)
        with ui.dialog() as dlg, ui.card().classes("gap-2 p-4 min-w-[300px]"):
            ui.label(label).classes("text-sm")
            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button(tr("config.cancel"), on_click=dlg.close).props("flat dense")

                def _do() -> None:
                    n = delete_all_vault_pairs()
                    _tv_ui["selected"].clear()
                    dlg.close()
                    ui.notify(tr("knowledge_vault.tv_deleted", count=n), type="positive")
                    _render_list.refresh()

                ui.button(tr("knowledge_vault.tv_delete"), on_click=_do).props(
                    "color=negative dense"
                )
        dlg.open()

    def _save_pair(pid: str, source_text: str, target_text: str) -> bool:
        """Shared save path for the inline row's Save button and the view dialog's
        Save button — same update + score-drop-warning notify either way."""
        result = update_vault_pair(pid, source_text or "", target_text or "")
        if not result.success:
            ui.notify(tr("knowledge_vault.tv_save_failed"), type="negative")
            return False
        try:
            from services.formslator.vault_alignment import translation_pair_score

            old_score = translation_pair_score(result.old_source_text, result.old_target_text)
            new_score = translation_pair_score(source_text or "", target_text or "")
        except Exception:
            old_score = new_score = None
        if old_score is not None and new_score is not None and new_score < old_score:
            ui.notify(
                tr(
                    "knowledge_vault.tv_score_dropped",
                    old=f"{old_score:.2f}",
                    new=f"{new_score:.2f}",
                ),
                type="warning",
            )
        else:
            ui.notify(tr("knowledge_vault.tv_saved"), type="positive")
        return True

    def _open_view_dialog(pair) -> None:
        # Compact inline inputs truncate long source/translation text visually —
        # this shows both in full and editable, for pairs where that matters.
        with ui.dialog() as dlg, ui.card().classes("gap-3 p-4 min-w-[320px] max-w-2xl"):
            ui.label(tr("knowledge_vault.tv_col_source")).classes(
                "text-[11px] font-bold text-blue-400 uppercase"
            )
            src_ta = ui.textarea(value=pair.source_text).classes(
                "w-full text-sm"
            ).props("dense borderless autogrow input-class=q-pa-none")
            ui.label(tr("knowledge_vault.tv_col_translation")).classes(
                "text-[11px] font-bold text-blue-400 uppercase mt-2"
            )
            tgt_ta = ui.textarea(value=pair.target_text).classes(
                "w-full text-sm"
            ).props("dense borderless autogrow input-class=q-pa-none")

            def _save(pid=pair.pair_id, src=src_ta, tgt=tgt_ta) -> None:
                if _save_pair(pid, src.value, tgt.value):
                    dlg.close()
                    _render_list.refresh()

            with ui.row().classes("w-full justify-end gap-2 mt-2"):
                ui.button(tr("knowledge_vault.tv_close"), on_click=dlg.close).props("flat dense")
                ui.button(tr("knowledge_vault.tv_save"), on_click=_save).props(
                    "dense color=primary"
                )
        dlg.open()

    def _render_pair_row(pair, conflicting_ids: set[str]) -> None:
        with ui.card().classes("w-full p-2 gap-1").props("flat bordered"):
            with ui.row().classes("w-full items-center gap-2 flex-wrap"):
                cb = ui.checkbox(value=pair.pair_id in _tv_ui["selected"]).props("dense")

                def _on_check(e, pid=pair.pair_id) -> None:
                    if e.value:
                        _tv_ui["selected"].add(pid)
                    else:
                        _tv_ui["selected"].discard(pid)
                    _render_list.refresh()

                cb.on_value_change(_on_check)

                src_in = ui.input(
                    label=tr("knowledge_vault.tv_col_source"), value=pair.source_text
                ).classes("min-w-[160px] flex-1").props("dense standout")
                tgt_in = ui.input(
                    label=tr("knowledge_vault.tv_col_translation"), value=pair.target_text
                ).classes("min-w-[160px] flex-1").props("dense standout")

                ui.label(describe_languages(pair)).classes(
                    "text-[11px] px-2 py-1 rounded bg-white/10 shrink-0"
                )

                ui.button(icon="edit", on_click=lambda: _open_view_dialog(pair)).props(
                    "flat dense round size=sm"
                ).tooltip(tr("knowledge_vault.tv_view_row"))

                # A pair can carry sources from more than one catalogue — only the
                # single best-matched one (see best_source) is ever shown or opened.
                src = best_source(pair.sources)
                if src and src.file_path:
                    lib_name = library_display_name(src.library_id)

                    def _open_source_file(path=src.file_path) -> None:
                        abs_path = resolve_abs_path(path)
                        try:
                            open_file_in_os(abs_path)
                        except Exception as exc:
                            ui.notify(
                                tr("knowledge_vault.tv_open_failed", error=exc), type="negative"
                            )

                    ui.button(icon="description", on_click=_open_source_file).props(
                        "flat dense round size=sm"
                    ).tooltip(f"{lib_name} — {src.file_path}")

                def _save(pid=pair.pair_id, src=src_in, tgt=tgt_in) -> None:
                    _save_pair(pid, src.value, tgt.value)

                ui.button(icon="save", on_click=_save).props(
                    "flat dense round size=sm color=primary"
                ).tooltip(tr("knowledge_vault.tv_save_row"))

                is_flagged = needs_review(pair) or pair.pair_id in conflicting_ids
                if is_flagged and not getattr(pair, "reviewed", False):
                    def _mark_reviewed(pid=pair.pair_id) -> None:
                        if mark_vault_pair_reviewed(pid):
                            ui.notify(tr("knowledge_vault.tv_marked_reviewed"), type="positive")
                            _render_list.refresh()

                    ui.button(icon="fact_check", on_click=_mark_reviewed).props(
                        "flat dense round size=sm color=warning"
                    ).tooltip(tr("knowledge_vault.tv_mark_reviewed"))

                def _delete_one(pid=pair.pair_id) -> None:
                    _confirm_delete(
                        {pid}, tr("knowledge_vault.tv_confirm_delete_selected", count=1)
                    )

                ui.button(icon="delete", on_click=_delete_one).props(
                    "flat dense round size=sm color=negative"
                ).tooltip(tr("knowledge_vault.tv_delete_row"))

    @ui.refreshable
    def _render_list() -> None:
        search = (_tv_ui.get("search") or "").strip().lower()
        flagged_only = bool(_tv_ui.get("flagged_only"))
        show_all = bool(_tv_ui.get("show_all"))
        if not search and not flagged_only and not show_all:
            ui.label(tr("knowledge_vault.tv_search_prompt")).classes(
                "text-xs opacity-70 py-4 text-center w-full"
            )
            return

        matched = list_pairs()
        # Computed once per render (not per row) — find_conflicting_pairs is a
        # whole-scope pass, same cost class as needs_review()'s own per-render scan.
        conflicting_ids = find_conflicting_pairs(matched)
        if flagged_only:
            matched = [
                p
                for p in matched
                if (needs_review(p) or p.pair_id in conflicting_ids)
                and not getattr(p, "reviewed", False)
            ]
        if search:
            matched = [p for p in matched if _matches(p, search)]
        total = len(matched)
        if not total:
            ui.label(tr("knowledge_vault.tv_no_results")).classes(
                "text-xs opacity-70 py-4 text-center w-full"
            )
            return

        max_page = max(0, (total - 1) // _PAGE_SIZE)
        page = min(int(_tv_ui.get("page") or 0), max_page)
        _tv_ui["page"] = page
        rows = matched[page * _PAGE_SIZE : page * _PAGE_SIZE + _PAGE_SIZE]

        with ui.row().classes("w-full items-center justify-between flex-wrap gap-2"):
            ui.label(
                tr(
                    "knowledge_vault.tv_showing",
                    shown=len(rows), total=total, page=page + 1, pages=max_page + 1,
                )
            ).classes("text-[11px] opacity-50")
            with ui.row().classes("gap-2 items-center"):
                selected_ids = set(_tv_ui["selected"])
                if selected_ids:
                    ui.button(
                        tr("knowledge_vault.tv_delete_selected", count=len(selected_ids)),
                        icon="delete",
                        on_click=lambda: _confirm_delete(
                            selected_ids,
                            tr(
                                "knowledge_vault.tv_confirm_delete_selected",
                                count=len(selected_ids),
                            ),
                        ),
                    ).props("flat dense size=sm color=negative")
                ui.button(
                    tr("knowledge_vault.tv_delete_all"),
                    icon="delete_forever",
                    on_click=lambda: _confirm_delete_all(total),
                ).props("flat dense size=sm color=negative")

        with ui.row().classes("w-full gap-2 mb-1"):
            if page > 0:
                ui.button(
                    tr("knowledge_vault.tv_previous"), on_click=lambda: _set_page(page - 1)
                ).props("flat dense size=sm")
            if page < max_page:
                ui.button(
                    tr("knowledge_vault.tv_next"), on_click=lambda: _set_page(page + 1)
                ).props("flat dense size=sm")

        with ui.column().classes("w-full gap-1"):
            for pair in rows:
                _render_pair_row(pair, conflicting_ids)

    def _set_page(p: int) -> None:
        _tv_ui["page"] = max(0, p)
        _render_list.refresh()

    with ui.column().classes("w-full h-full min-h-0 gap-2 p-2 overflow-y-auto"):
        with ui.row().classes("w-full items-center gap-2"):
            search_input = ui.input(
                tr("knowledge_vault.tv_search_placeholder"), value=_tv_ui["search"]
            ).classes("flex-1 min-w-0").props("dense outlined dark")

            def _on_search(e) -> None:
                _tv_ui["search"] = (e.value or "").strip()
                _tv_ui["page"] = 0
                _render_list.refresh()

            search_input.on_value_change(_on_search)

            show_all_cb = ui.checkbox(
                tr("knowledge_vault.tv_show_all"), value=_tv_ui.get("show_all", False)
            ).props("dense")

            def _on_show_all(e) -> None:
                _tv_ui["show_all"] = bool(e.value)
                _tv_ui["page"] = 0
                _render_list.refresh()

            show_all_cb.on_value_change(_on_show_all)

            flagged_cb = ui.checkbox(
                tr("knowledge_vault.tv_flagged_only"), value=_tv_ui.get("flagged_only", False)
            ).props("dense")

            def _on_flagged_only(e) -> None:
                _tv_ui["flagged_only"] = bool(e.value)
                _tv_ui["page"] = 0
                _render_list.refresh()

            flagged_cb.on_value_change(_on_flagged_only)

        ui.label(tr("knowledge_vault.tv_session_note")).classes(
            "text-[10px] opacity-50 -mt-1"
        )
        _render_list()
