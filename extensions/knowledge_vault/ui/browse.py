# -*- coding: utf-8 -*-
"""Native folder picker."""
from __future__ import annotations

import os
import subprocess
import sys


def _browse_folder_macos(title: str) -> str:
    # Tk (Cocoa) aborts the whole process when an NSWindow is created off the main thread,
    # and this runs on a NiceGUI worker thread — so ask macOS for the dialog out-of-process.
    script = (
        "on run argv\n"
        "  set p to POSIX path of (choose folder with prompt (item 1 of argv))\n"
        "  return p\n"
        "end run"
    )
    try:
        out = subprocess.run(
            ["osascript", "-e", script, title],
            capture_output=True, text=True, timeout=600, check=False,
        )
    except Exception:
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def browse_folder(*, title: str = "Select folder") -> str:
    # CI-only: a headless runner can't click a native folder dialog, so tests name the folder.
    forced = os.environ.get("LOMA_E2E_PICK_FOLDER", "").strip()
    if forced:
        return forced
    if sys.platform == "darwin":
        return _browse_folder_macos(title)
    try:
        import tkinter as tk
        from tkinter import filedialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        path = filedialog.askdirectory(title=title)
        root.destroy()
        return (path or "").strip()
    except Exception:
        return ""
