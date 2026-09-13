# -*- coding: utf-8 -*-
"""Create new blank documents for the document editor."""
from __future__ import annotations

import os
import sys

_word_installed: bool | None = None


def office_word_available() -> bool:
    """Fast registry check — does not launch Word."""
    global _word_installed
    if _word_installed is not None:
        return _word_installed
    if sys.platform != "win32":
        _word_installed = False
        return False
    try:
        import winreg

        winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Word.Application\CurVer")
        _word_installed = True
    except OSError:
        _word_installed = False
    return _word_installed


def create_blank_document(directory: str, basename: str, *, as_docx: bool) -> str:
    """Create an empty .txt or .docx under directory. Returns absolute path."""
    os.makedirs(directory, exist_ok=True)
    safe = "".join(c for c in basename if c not in '\\/:*?"<>|').strip() or "untitled"
    if as_docx:
        from docx import Document

        dest = os.path.join(directory, f"{safe}.docx")
        doc = Document()
        doc.add_paragraph("")
        doc.save(dest)
        return dest
    dest = os.path.join(directory, f"{safe}.txt")
    with open(dest, "w", encoding="utf-8") as f:
        f.write("")
    return dest
