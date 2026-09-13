# -*- coding: utf-8 -*-
"""Native folder picker."""
from __future__ import annotations


def browse_folder(*, title: str = "Select folder") -> str:
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
