# -*- coding: utf-8 -*-
"""Sandbox paths outside the project tree (avoids dev-server reload loops)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


def sandbox_root() -> Path:
    override = (os.environ.get("LOMA_SANDBOX_ROOT") or os.environ.get("LOMA_SANDBOX_ROOT") or "").strip()
    if override:
        root = Path(override)
    else:
        root = Path(tempfile.gettempdir()) / "loma_sandbox"
    root.mkdir(parents=True, exist_ok=True)
    return root


def active_script_dir() -> Path:
    path = sandbox_root() / "active"
    path.mkdir(parents=True, exist_ok=True)
    return path
