# -*- coding: utf-8 -*-
"""Savepoint / rollback for Formslator files changed by LOMA updates."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_SAVEPOINT_ROOT = _PROJECT_ROOT / "data" / "formslator" / "savepoint"
_MARKER = _SAVEPOINT_ROOT / ".initialized"

_TRACKED: tuple[Path, ...] = (
    Path("services/formslator/translator.py"),
    Path("services/formslator/translate_engine.py"),
    Path("services/formslator/worker.py"),
    Path("services/formslator/resource_budget.py"),
    Path("services/formslator/savepoint.py"),
    Path("extensions/formslator/tabs/translate.py"),
    Path("extensions/formslator/extension.py"),
)


def create_savepoint(*, force: bool = False) -> bool:
    """Snapshot tracked Formslator files (once, unless force=True)."""
    if _MARKER.exists() and not force:
        return False
    for rel in _TRACKED:
        src = _PROJECT_ROOT / rel
        if not src.is_file():
            continue
        dest = _SAVEPOINT_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    _MARKER.write_text("ok", encoding="utf-8")
    return True


def rollback_savepoint() -> list[str]:
    """Restore tracked files from savepoint. Returns restored relative paths."""
    restored: list[str] = []
    for rel in _TRACKED:
        snap = _SAVEPOINT_ROOT / rel
        if not snap.is_file():
            continue
        dest = _PROJECT_ROOT / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(snap, dest)
        restored.append(str(rel).replace("\\", "/"))
    return restored


def savepoint_exists() -> bool:
    return _MARKER.exists()
