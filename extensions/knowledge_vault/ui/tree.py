# -*- coding: utf-8 -*-
"""Folder tree renderer."""
from __future__ import annotations

import time
from typing import Any, Callable

from nicegui import ui

from pipeline.i18n import t as tr

_INDENT_PX = 22
_CHEVRON_W = 18
_ROW = "w-full flex flex-row items-center justify-start gap-0 min-h-[22px] no-wrap"
_LABEL = "justify-start text-left no-wrap min-w-0"
# A file click triggers a full tree rebuild (on_rebuild() clears and re-renders
# every visible row) before the handler returns — since NiceGUI processes events
# for one client sequentially, the second click of a double-click only gets
# timestamped AFTER that rebuild finishes. On a small tree this is negligible; on
# a large catalogue (dozens of files across nested folders) the rebuild itself can
# eat a meaningful chunk of a 0.4s window, so two clicks that were genuinely fast
# from the user's perspective can still measure as more than 0.4s apart server-side.
# Widened to give real double-clicks headroom against that server-side cost.
_DBLCLICK_WINDOW_S = 1.0


def path_str(parts: list[str]) -> str:
    return "/".join(parts)


def render_tree_node(
    container,
    tree: dict,
    *,
    path_prefix: list[str],
    indent_px: int,
    state: dict[str, Any],
    on_rebuild: Callable[[], None],
    branch_lbl_ref: dict[str, Any],
    on_open_file: Callable[[str], None] | None = None,
    on_delete_file: Callable[[str], None] | None = None,
    on_toggle_exclude: Callable[[str, bool], None] | None = None,
) -> None:
    folders = sorted(
        k for k in tree if k not in ("__files__", "__skipped__", "__file_flags__")
    )
    files = sorted(tree.get("__files__") or [])
    skipped: dict[str, str] = tree.get("__skipped__") or {}
    file_flags: dict[str, bool] = tree.get("__file_flags__") or {}

    for folder in folders:
        fpath = path_str(path_prefix + [folder])
        is_open = fpath in state.get("expanded", set())
        is_sel = state.get("selected_branch") == fpath
        chevron = "▼" if is_open else "▶"

        def _toggle(*, p=fpath) -> None:
            exp: set[str] = state.setdefault("expanded", set())
            if p in exp:
                exp.discard(p)
            else:
                exp.add(p)
            on_rebuild()

        def _select(*, p=fpath, n=folder) -> None:
            state["selected_branch"] = p
            lbl = branch_lbl_ref.get("el")
            if lbl is not None:
                lbl.text = p or n
            on_rebuild()

        with container:
            with ui.element("div").classes(_ROW).style(f"padding-left: {indent_px}px"):
                ui.button(chevron, on_click=_toggle).props("dense flat size=sm").classes(
                    f"min-w-[{_CHEVRON_W}px] w-[{_CHEVRON_W}px] text-[10px] text-gray-500 shrink-0"
                )
                ui.button(
                    f"📁 {folder}",
                    on_click=_select,
                ).props("dense flat no-caps align=left").classes(
                    f"{_LABEL} text-[11px] font-medium truncate "
                    + ("text-emerald-300 bg-emerald-600/20" if is_sel else "text-gray-300")
                )

        if is_open:
            render_tree_node(
                container,
                tree[folder],
                path_prefix=path_prefix + [folder],
                indent_px=indent_px + _INDENT_PX,
                state=state,
                on_rebuild=on_rebuild,
                branch_lbl_ref=branch_lbl_ref,
                on_open_file=on_open_file,
                on_delete_file=on_delete_file,
                on_toggle_exclude=on_toggle_exclude,
            )

    for fname in files:
        fpath = path_str(path_prefix + [fname])
        is_sel = state.get("selected_branch") == fpath
        is_low_content = bool(file_flags.get(fname))

        def _select_file(*, p=fpath, n=fname) -> None:
            # A native browser dblclick event turned out to be unreliable here (a
            # live test showed the server never received it at all — nothing
            # printed, confirmed not just a timing/latency issue) — back to
            # detecting the double-click server-side, by timing consecutive
            # 'click' events on the same file path. This is the version already
            # confirmed working; do not "fix" it again without live proof.
            now = time.time()
            last_path, last_time = state.get("_last_file_click") or (None, 0.0)
            is_double = p == last_path and (now - last_time) < _DBLCLICK_WINDOW_S
            state["_last_file_click"] = (None, 0.0) if is_double else (p, now)
            if is_double and on_open_file is not None:
                on_open_file(p)
                return
            state["selected_branch"] = p
            lbl = branch_lbl_ref.get("el")
            if lbl is not None:
                lbl.text = p or n
            on_rebuild()

        with container:
            with ui.element("div").classes(_ROW).style(f"padding-left: {indent_px}px"):
                ui.element("div").classes(f"w-[{_CHEVRON_W}px] shrink-0")
                icon = "🚫" if is_low_content else "📄"
                file_btn = ui.button(
                    f"{icon} {fname}",
                    on_click=_select_file,
                ).props("dense flat no-caps align=left").classes(
                    f"{_LABEL} text-[11px] truncate "
                    + (
                        "text-emerald-300 bg-emerald-600/20"
                        if is_sel
                        else "text-gray-500 italic"
                        if is_low_content
                        else "text-blue-200/80"
                    )
                )
                tooltip = tr("knowledge_vault.tree_low_content_tooltip") if is_low_content else (
                    tr("knowledge_vault.tree_file_tooltip") if on_open_file is not None else ""
                )
                if tooltip:
                    file_btn.tooltip(tooltip)

                if on_delete_file is not None or on_toggle_exclude is not None:
                    # A right-click ui.context_menu() listens for the browser's native
                    # contextmenu event, never click/dblclick — unlike a second
                    # ui.button() + ui.menu() sharing the row, it structurally cannot
                    # interfere with file_btn's own click/double-click handling, which
                    # a prior version of this menu (a separate kebab button) broke.
                    with file_btn:
                        with ui.context_menu() as menu:
                            if on_open_file is not None:
                                ui.menu_item(
                                    tr("knowledge_vault.file_open"),
                                    on_click=lambda *, p=fpath: (
                                        menu.close(),
                                        on_open_file(p),
                                    ),
                                )
                            if on_toggle_exclude is not None:
                                label = (
                                    tr("knowledge_vault.file_include")
                                    if is_low_content
                                    else tr("knowledge_vault.file_exclude")
                                )
                                ui.menu_item(
                                    label,
                                    on_click=lambda *, p=fpath, ex=not is_low_content: (
                                        menu.close(),
                                        on_toggle_exclude(p, ex),
                                    ),
                                )
                            if on_delete_file is not None:
                                ui.menu_item(
                                    tr("knowledge_vault.file_delete"),
                                    on_click=lambda *, p=fpath: (
                                        menu.close(),
                                        on_delete_file(p),
                                    ),
                                ).classes("text-red-400")

    for fname in sorted(skipped):
        with container:
            with ui.element("div").classes(_ROW).style(f"padding-left: {indent_px}px"):
                ui.element("div").classes(f"w-[{_CHEVRON_W}px] shrink-0")
                ui.label(f"⚠ {fname}").classes(
                    f"{_LABEL} text-[11px] truncate text-gray-500 italic"
                ).tooltip(skipped[fname])


def render_root_tree(
    container,
    tree: dict,
    *,
    state: dict[str, Any],
    on_rebuild: Callable[[], None],
    branch_lbl_ref: dict[str, Any],
    empty_msg: str = "",
    on_open_file: Callable[[str], None] | None = None,
    on_delete_file: Callable[[str], None] | None = None,
    on_toggle_exclude: Callable[[str, bool], None] | None = None,
) -> None:
    if not empty_msg:
        empty_msg = tr("knowledge_vault.tree_empty")
    has_items = bool(tree.get("__files__") or any(k != "__files__" for k in tree))
    expanded = state.get("expanded")
    if expanded is None and has_items:
        state["expanded"] = set()
        from extensions.knowledge_vault.ui.fs_tree import collect_folder_paths

        state["expanded"].add("")
        for p in collect_folder_paths(tree, prefix=[]):
            state["expanded"].add(p)

    exp = state.get("expanded") or set()
    root_open = "" in exp
    root_sel = not state.get("selected_branch")

    def _toggle_root() -> None:
        e: set[str] = state.get("expanded") or set()
        if "" in e:
            e.discard("")
        else:
            e.add("")
        state["expanded"] = e
        on_rebuild()

    def _select_root() -> None:
        state["selected_branch"] = ""
        lbl = branch_lbl_ref.get("el")
        if lbl is not None:
            lbl.text = tr("knowledge_vault.all_documents")
        on_rebuild()

    with container:
        with ui.element("div").classes(_ROW).style("padding-left: 4px"):
            ui.button("▼" if root_open else "▶", on_click=_toggle_root).props(
                "dense flat size=sm"
            ).classes("min-w-[20px] w-[20px] text-[10px] text-gray-500 shrink-0")
            ui.button(tr("knowledge_vault.tree_root"), on_click=_select_root).props(
                "dense flat no-caps align=left"
            ).classes(
                f"{_LABEL} text-[11px] font-semibold "
                + ("text-emerald-300 bg-emerald-600/20" if root_sel else "text-gray-300")
            )

        if not has_items:
            ui.label(empty_msg).classes("text-[11px] text-gray-500 italic px-2 py-2")
        elif root_open:
            render_tree_node(
                container,
                tree,
                path_prefix=[],
                indent_px=_INDENT_PX,
                state=state,
                on_rebuild=on_rebuild,
                branch_lbl_ref=branch_lbl_ref,
                on_open_file=on_open_file,
                on_delete_file=on_delete_file,
                on_toggle_exclude=on_toggle_exclude,
            )
