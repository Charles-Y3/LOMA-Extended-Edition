# -*- coding: utf-8 -*-
"""Password cache and UI prompts for encrypted documents."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field

_cache: dict[str, str] = {}
_skipped: set[str] = set()
_lock = threading.Lock()
_dialog_lock = threading.Lock()
_active_dialog = False


@dataclass
class _PasswordRequest:
    path: str
    basename: str
    event: threading.Event = field(default_factory=threading.Event)
    result: str | None = None
    shown: bool = False


_pending: _PasswordRequest | None = None


def remember_password(file_path: str, password: str) -> None:
    """Call only once a password is CONFIRMED correct (a successful decrypt) —
    caches it for this run and persists it to Knowledge Vault settings so
    future documents (this run and later ones) are tried against it automatically."""
    pwd = (password or "").strip()
    if not pwd:
        return
    with _lock:
        _cache[os.path.abspath(file_path)] = pwd
    try:
        from extensions.knowledge_vault.settings import (
            load_settings,
            save_settings,
        )

        cfg = load_settings()
        existing = [p.strip() for p in str(cfg.get("document_passwords") or "").split(";") if p.strip()]
        if pwd not in existing:
            existing.append(pwd)
            cfg["document_passwords"] = ";".join(existing)
            save_settings(cfg)
    except Exception:
        pass


def cached_password(file_path: str) -> str | None:
    with _lock:
        return _cache.get(os.path.abspath(file_path))


def remember_skip(file_path: str) -> None:
    """The user chose to skip this file (no password / gave up). A single file can
    be processed by more than one extraction pass in the same indexing run (e.g.
    text-chunk extraction and translation-pair extraction both open it independently)
    — remembering the skip means the second pass doesn't re-prompt for a decision
    the user already made once."""
    with _lock:
        _skipped.add(os.path.abspath(file_path))


def was_skipped(file_path: str) -> bool:
    with _lock:
        return os.path.abspath(file_path) in _skipped


def passwords_for_file(file_path: str, settings_passwords: str = "") -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    cached = cached_password(file_path)
    if cached:
        seen.add(cached)
        out.append(cached)
    for part in (settings_passwords or "").split(";"):
        pwd = part.strip()
        if pwd and pwd not in seen:
            seen.add(pwd)
            out.append(pwd)
    return out


def password_attempts(file_path: str, settings_passwords: str = "") -> list[str]:
    """Empty password first (unencrypted), then cached and settings passwords."""
    seen: set[str] = {""}
    out = [""]
    for pwd in passwords_for_file(file_path, settings_passwords):
        if pwd not in seen:
            seen.add(pwd)
            out.append(pwd)
    return out


def pending_password_basename() -> str | None:
    with _lock:
        if _pending and not _pending.event.is_set():
            return _pending.basename
    return None


def get_pending_password_request() -> _PasswordRequest | None:
    with _lock:
        if _pending and not _pending.event.is_set():
            return _pending
    return None


def _schedule_on_main(callback) -> None:
    def _run() -> None:
        try:
            callback()
            return
        except RuntimeError as exc:
            if "slot stack" not in str(exc).lower():
                raise
        try:
            from nicegui import app

            clients = list(app.clients())
            if not clients:
                return
            with clients[0]:
                callback()
        except Exception:
            pass

    try:
        from nicegui import core

        if core.loop and core.loop.is_running():
            core.loop.call_soon_threadsafe(_run)
            return
    except Exception:
        pass
    try:
        from services.session.workflow_control import schedule_on_ui

        schedule_on_ui(callback)
    except Exception:
        _run()


def _open_password_dialog(req: _PasswordRequest) -> None:
    global _active_dialog

    def _show() -> None:
        global _active_dialog
        with _dialog_lock:
            if _active_dialog or req.event.is_set():
                return
            _active_dialog = True

        def _finish(pwd: str | None) -> None:
            global _active_dialog
            req.result = pwd
            req.event.set()
            with _dialog_lock:
                _active_dialog = False
            with _lock:
                global _pending
                if _pending is req:
                    _pending = None

        from nicegui import ui
        from pipeline.i18n import t as tr

        # Best-effort toast — a failure here (e.g. no valid slot for this call) must
        # not abort building the actual dialog below.
        try:
            ui.notify(
                tr("knowledge_vault.pwd_notify", name=req.basename),
                color="warning",
                timeout=0,
                close_button=True,
            )
        except Exception:
            pass

        try:
            with ui.dialog().props("persistent") as dlg, ui.card().classes(
                "gap-3 p-4 min-w-[300px]"
            ):
                ui.label(tr("knowledge_vault.pwd_title")).classes("text-sm font-medium")
                ui.label(req.basename).classes("text-[11px] text-gray-400 break-all")
                ui.label(tr("knowledge_vault.pwd_paused")).classes("text-[11px] text-amber-300/90")
                pwd_in = ui.input(tr("knowledge_vault.pwd_input")).props(
                    "dense outlined dark type=password password-toggle-button autofocus"
                ).classes("w-full text-xs")

                def _submit() -> None:
                    pwd = (pwd_in.value or "").strip() or None
                    dlg.close()
                    _finish(pwd)

                def _cancel() -> None:
                    dlg.close()
                    _finish(None)

                pwd_in.on("keydown.enter", _submit)
                with ui.row().classes("w-full justify-end gap-2"):
                    ui.button(tr("knowledge_vault.pwd_skip"), on_click=_cancel).props("dense flat")
                    ui.button(tr("common.ok"), on_click=_submit).props("dense flat color=primary")
            req.shown = True
            dlg.open()
        except Exception:
            with _dialog_lock:
                _active_dialog = False
            req.shown = False
            # Re-raise so _schedule_on_main's _run() sees a real failure and, for the
            # "no slot for this task" case, retries via the app.clients() fallback
            # instead of this silently doing nothing — the original bare `except
            # Exception: pass` around the whole dialog build swallowed that signal,
            # making the fallback unreachable and leaving nothing to ever show the
            # dialog if this call happened to run with no valid slot.
            raise

    _schedule_on_main(_show)


def poll_password_dialog() -> None:
    """Call from a UI timer while indexing — ensures the password modal appears."""
    req = get_pending_password_request()
    if req is None:
        return
    _open_password_dialog(req)


def request_password_dialog(file_path: str) -> str | None:
    """Block until user submits a password in the UI (indexing thread safe).

    Does NOT cache the result — a caller only knows the password is actually
    correct after decrypting with it, so remember_password() must be called by
    the caller on confirmed success. Caching here unconditionally meant a wrong
    entry got cached as if it were right, and every retry after that first wrong
    guess short-circuited straight back to that same wrong password forever,
    without the dialog ever reappearing (looked like an endless "Wrong password"
    loop with no way to enter a different one).
    """
    abs_path = os.path.abspath(file_path)

    req = _PasswordRequest(path=abs_path, basename=os.path.basename(abs_path))
    with _lock:
        global _pending
        _pending = req

    _open_password_dialog(req)
    if not req.event.wait(timeout=600):
        with _lock:
            if _pending is req:
                _pending = None
        return None

    return req.result
