# -*- coding: utf-8 -*-
"""Append-only local audit log of model-proposed actions and the gate's decisions.

One JSON object per line in <user data folder>/logs/security_audit.jsonl. Never records secrets or file
contents: only the action kind, a short redacted summary of its arguments, the decision and the reason.
Rotates once at ~2 MB (keeps one previous file). Never raises: auditing must not break a feature.
"""
from __future__ import annotations

import json
import os
import threading
import time

_lock = threading.Lock()
_MAX_BYTES = 2 * 1024 * 1024


def audit_path() -> str:
    from services.platform_paths import writable_root

    return os.path.join(writable_root(), "logs", "security_audit.jsonl")


def _short(value: object, limit: int = 160) -> str:
    text = str(value).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


def record(kind: str, decision: str, reason: str = "", **args: object) -> None:
    try:
        path = audit_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "kind": kind,
            "decision": decision,
            "reason": _short(reason),
            "args": {k: _short(v) for k, v in args.items()},
        }
        with _lock:
            if os.path.exists(path) and os.path.getsize(path) > _MAX_BYTES:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass
