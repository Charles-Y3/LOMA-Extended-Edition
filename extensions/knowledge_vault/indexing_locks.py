# -*- coding: utf-8 -*-
"""Track files locked during indexing and prompt user to retry."""
from __future__ import annotations

import os
import threading
from typing import Callable

from pipeline.i18n import t as tr

_locked: list[str] = []
_lock = threading.Lock()


def note_locked_file(path: str) -> None:
    abs_p = os.path.abspath(path)
    with _lock:
        if abs_p not in _locked:
            _locked.append(abs_p)


def pop_locked_files() -> list[str]:
    with _lock:
        out = list(_locked)
        _locked.clear()
        return out


def file_appears_locked(path: str) -> bool:
    """Best-effort probe — Word/PDF may still block content extraction."""
    try:
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        os.close(fd)
        return False
    except OSError:
        return True


def prompt_close_locked_files(
    paths: list[str],
    *,
    on_continue: Callable[[], None],
) -> None:
    if not paths:
        return
    names = [os.path.basename(p) for p in paths]

    def _show() -> None:
        from nicegui import ui

        with ui.dialog() as dlg, ui.card().classes("gap-3 p-4 min-w-[320px]"):
            ui.label(tr("knowledge_vault.locks_title")).classes(
                "text-sm font-medium"
            )
            ui.label(tr("knowledge_vault.locks_body")).classes("text-[11px] text-gray-400")
            for name in names:
                ui.label(f"• {name}").classes("text-[11px] text-amber-300")
            with ui.row().classes("w-full justify-end gap-2"):
                ui.button(tr("common.cancel"), on_click=dlg.close).props("dense flat")

                def _go() -> None:
                    dlg.close()
                    on_continue()

                ui.button(tr("knowledge_vault.locks_continue"), on_click=_go).props("dense flat color=primary")
        dlg.open()

    try:
        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(_show)
    except Exception:
        pass
