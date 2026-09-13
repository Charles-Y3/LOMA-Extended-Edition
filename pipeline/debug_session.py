# -*- coding: utf-8 -*-
"""Debug instrumentation (optional)."""
from __future__ import annotations

import json
import time
from pathlib import Path

_LOG_PATH = Path(__file__).resolve().parent.parent / "debug-92d406.log"
_SESSION = "92d406"


def debug_log(location: str, message: str, data: dict | None = None, hypothesis_id: str = "") -> None:
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
