# -*- coding: utf-8 -*-
"""Small persistent log file for troubleshooting (Settings > About > Open log file)."""
from __future__ import annotations

import os
import traceback

LOG_DIR = os.path.join("data", "logs")
LOG_FILE = os.path.join(LOG_DIR, "loma.log")


def log(message: str) -> None:
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(message.rstrip() + "\n")
    except Exception:
        pass


def log_exception(prefix: str) -> None:
    log(prefix + "\n" + traceback.format_exc())


def open_log_file() -> None:
    """Opens the log file in the system's default app, or its containing
    folder if the log doesn't exist yet — reuses platform_paths' already
    cross-platform-tested open helpers (Explorer-focus on Windows, `open` on
    macOS, `xdg-open` on Linux) rather than duplicating that logic here."""
    from services.platform_paths import open_file_in_os, open_path_in_os

    if os.path.isfile(LOG_FILE):
        open_file_in_os(LOG_FILE)
    else:
        open_path_in_os(LOG_DIR)
