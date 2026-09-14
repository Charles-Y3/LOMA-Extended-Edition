# -*- coding: utf-8 -*-
"""Open local paths from Knowledge Vault UI."""
from __future__ import annotations

import os
import sys

from pipeline.i18n import t as tr


def open_local_path(path: str) -> None:
    from nicegui import ui

    abspath = os.path.abspath((path or "").strip())
    if not abspath or not os.path.exists(abspath):
        ui.notify(tr("knowledge_vault.open_path_not_found", path=path), color="warning")
        return
    try:
        if os.path.isfile(abspath):
            if sys.platform == "win32":
                os.startfile(abspath)
            elif sys.platform == "darwin":
                import subprocess

                subprocess.run(["open", abspath], check=False)
            else:
                import subprocess

                subprocess.run(["xdg-open", abspath], check=False)
        else:
            from services.platform_paths import open_path_in_os

            open_path_in_os(abspath)
    except Exception as exc:
        ui.notify(tr("knowledge_vault.open_path_failed", error=exc), color="negative")
