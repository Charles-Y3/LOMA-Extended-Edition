# -*- coding: utf-8 -*-
"""Debug instrumentation (optional)."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-92d406.log"
_SESSION = "92d406"
# Off unless LOMA_DEBUG_LOG=1. In a packaged app _LOG_PATH is inside the app bundle, so
# writing it on every launch modified the signed .app (on macOS that invalidates its code
# signature) and could fail outright in a read-only install folder.
_ENABLED = os.environ.get("LOMA_DEBUG_LOG", "").strip().lower() in ("1", "true", "yes")


def debug_logging_enabled() -> bool:
    return _ENABLED


def debug_log(location: str, message: str, data: dict | None = None, hypothesis_id: str = "") -> None:
    if not _ENABLED:
        return
    try:
        payload = {
            "sessionId": _SESSION,
            "location": location,
            "message": message,
            "data": data or {},
            "hypothesisId": hypothesis_id,
            "timestamp": int(time.time() * 1000),
        }
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except Exception:
        pass
