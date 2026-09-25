# -*- coding: utf-8 -*-
"""Which local paths may be opened in the OS from a chat link or button.

Model text can contain anything, including a forged ``loma-open:`` link. The browser sends the
path back to ``/loma/open-path``; this module decides in plain code whether that path may be
opened: never executables/scripts/UNC paths, and only files the APP itself linked
(``register_openable``) or that live under the app's data folder or a Knowledge Vault root.
"""
from __future__ import annotations

import os
import threading

# Launching any of these would run code rather than show a document.
DANGEROUS_EXTENSIONS = frozenset({
    ".exe", ".com", ".bat", ".cmd", ".scr", ".msi", ".msp", ".dll", ".cpl", ".ps1", ".psm1", ".vbs", ".vbe",
    ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".url", ".reg", ".jar", ".app", ".command", ".sh",
    ".pkg", ".dmg", ".workflow", ".action", ".scpt", ".terminal", ".desktop", ".appimage", ".pif",
})

_registered: set[str] = set()
_lock = threading.Lock()


def _canon(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def is_dangerous_file(path: str) -> bool:
    ext = os.path.splitext(path or "")[1].lower()
    # macOS .app bundles are directories: treat them as dangerous too.
    return ext in DANGEROUS_EXTENSIONS


def register_openable(path: str) -> None:
    """Called by code that builds a link/button for a path the APP chose to show."""
    if not path:
        return
    with _lock:
        _registered.add(_canon(path))


def _is_under(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath([child, parent]) == parent
    except ValueError:  # different drives
        return False


def _allowed_roots() -> list[str]:
    roots: list[str] = []
    try:
        from services.platform_paths import writable_root

        roots.append(_canon(writable_root()))
    except Exception:
        pass
    try:
        from extensions.knowledge_vault.corpus.library import list_libraries

        for lib in list_libraries():
            roots.extend(_canon(r) for r in (lib.roots or []) if r)
    except Exception:
        pass
    return roots


def check_openable(path: str) -> tuple[bool, str]:
    """(allowed, reason). Reason is empty when allowed."""
    raw = (path or "").strip()
    if not raw:
        return False, "Missing path"
    if raw.startswith("\\\\") or raw.startswith("//"):
        return False, "Network (UNC) paths cannot be opened from a link"
    real = _canon(raw)
    if is_dangerous_file(real) or is_dangerous_file(raw):
        return False, "Programs and scripts cannot be opened from a link"
    with _lock:
        if real in _registered:
            return True, ""
    if any(_is_under(real, root) for root in _allowed_roots()):
        return True, ""
    return False, "This path was not offered by LOMA"
