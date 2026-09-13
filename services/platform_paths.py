# -*- coding: utf-8 -*-
"""Cross-platform paths and OS shell helpers (Windows, macOS, Linux)."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def resource_root() -> str:
    """Bundled read-only assets (PyInstaller ``_MEIPASS`` or the repo root)."""
    if is_frozen():
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def writable_root() -> str:
    """Directory for ``data/`` (uploads, chats, generated). Dev = repo; packaged = app data."""
    if is_frozen():
        from services.plugins.paths import loma_app_data_root

        root = loma_app_data_root()
        os.makedirs(root, exist_ok=True)
        return root
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def project_root() -> str:
    """Back-compat alias: writable root (where relative ``data/`` should live)."""
    return writable_root()


def ensure_runtime_cwd() -> str:
    """When packaged, chdir to a writable app-data root so relative ``data/`` paths work
    (Finder/.app and Start Menu launches often start with cwd ``/`` or System32)."""
    root = writable_root()
    if is_frozen():
        try:
            os.chdir(root)
        except OSError:
            pass
    return root


def ensure_data_folder_shortcut() -> None:
    """Windows-only, packaged-only: drop an "Open Data Folder.lnk" next to the
    exe pointing at the AppData folder writable_root() actually resolves to.
    All the app's real data (RAG corpus, chats, settings) lives in AppData —
    not next to the exe — so this is how a user browsing the install folder
    finds it without knowing that convention exists. Idempotent/cheap; safe
    to call on every launch."""
    if os.name != "nt" or not is_frozen():
        return
    try:
        import win32com.client

        target = writable_root()
        exe_dir = os.path.dirname(sys.executable)
        shortcut_path = os.path.join(exe_dir, "Open Data Folder.lnk")
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(shortcut_path)
        shortcut.Targetpath = target
        shortcut.WorkingDirectory = target
        shortcut.save()
    except Exception:
        pass


def generated_dir() -> str:
    path = os.path.join(project_root(), "data", "generated")
    os.makedirs(path, exist_ok=True)
    return path


def venv_python() -> str:
    """Interpreter to recommend for pip installs / restarts."""
    if is_frozen():
        return sys.executable
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if sys.platform == "win32":
        return os.path.join(root, "venv", "Scripts", "python.exe")
    return os.path.join(root, "venv", "bin", "python")


def pip_install_cmd(*packages_or_flags: str) -> str:
    """Shell-ready ``python -m pip install …`` using the project/venv interpreter."""
    py = venv_python()
    quoted = f'"{py}"' if " " in py else py
    rest = " ".join(packages_or_flags)
    return f"{quoted} -m pip install {rest}".rstrip()


def _foreground_explorer_windows(folder: str) -> None:
    import ctypes
    from ctypes import wintypes

    folder_name = os.path.basename(os.path.normpath(folder))
    user32 = ctypes.windll.user32
    user32.AllowSetForegroundWindow(ctypes.c_ulong(-1))

    target: int | None = None
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _enum(hwnd: int, _: int) -> bool:
        nonlocal target
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value not in ("CabinetWClass", "ExploreWClass"):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        title = ""
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
        if folder_name and folder_name in title:
            target = hwnd
            return False
        if target is None:
            target = hwnd
        return True

    user32.EnumWindows(EnumProc(_enum), 0)
    if target:
        user32.ShowWindow(target, 9)
        user32.BringWindowToTop(target)
        user32.SetForegroundWindow(target)


def open_path_in_os(path: str) -> None:
    abspath = os.path.abspath(path)
    if not os.path.exists(abspath):
        os.makedirs(abspath, exist_ok=True)
    if sys.platform == "win32":
        subprocess.Popen(["explorer", abspath])

        def _focus() -> None:
            time.sleep(0.55)
            _foreground_explorer_windows(abspath)

        threading.Thread(target=_focus, daemon=True).start()
    elif sys.platform == "darwin":
        subprocess.run(["open", abspath], check=False)
    else:
        subprocess.run(["xdg-open", abspath], check=False)


def open_file_in_os(path: str) -> None:
    """Launch ``path`` in its default viewer/app — unlike :func:`open_path_in_os`,
    which reveals a *folder* (or selects a file within Explorer on Windows), this
    actually opens the file itself (e.g. the OS photo viewer for an image)."""
    abspath = os.path.abspath(path)
    if not os.path.isfile(abspath):
        raise FileNotFoundError(abspath)
    if sys.platform == "win32":
        os.startfile(abspath)  # noqa: S606 - local file the app itself generated
    elif sys.platform == "darwin":
        subprocess.run(["open", abspath], check=False)
    else:
        subprocess.run(["xdg-open", abspath], check=False)
