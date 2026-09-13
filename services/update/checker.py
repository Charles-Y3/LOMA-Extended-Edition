# -*- coding: utf-8 -*-
"""Compare local component versions against the remote manifest hosted on GitHub.

Offline-safe by design: with no UPDATE_REPO configured (see config/update_source.py)
or no network reachable, check_for_updates() returns an empty list rather than
raising — update checks are opt-in and must never block normal use.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Any

from config.update_source import manifest_url
from services.update.manifest import local_manifest


@dataclass(frozen=True)
class ComponentUpdate:
    component_type: str  # "app" | "service" | "extension"
    component_id: str
    local_version: str
    remote_version: str


def _parse_version(value: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in (value or "0").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def _is_newer(remote: str, local: str) -> bool:
    return _parse_version(remote) > _parse_version(local)


def fetch_remote_manifest(timeout: float = 5.0) -> dict[str, Any] | None:
    url = manifest_url()
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def check_for_updates() -> list[ComponentUpdate]:
    """Return components with a newer remote version. Empty if unreachable/unconfigured."""
    remote = fetch_remote_manifest()
    if not remote:
        return []

    local = local_manifest()
    updates: list[ComponentUpdate] = []

    remote_app_version = str(remote.get("app_version") or "0")
    if _is_newer(remote_app_version, local["app_version"]):
        updates.append(
            ComponentUpdate("app", "loma", local["app_version"], remote_app_version)
        )

    for kind in ("services", "extensions"):
        remote_rows = remote.get(kind) or {}
        local_rows = local.get(kind) or {}
        component_type = kind[:-1]  # "services" -> "service", "extensions" -> "extension"
        for component_id, remote_version in remote_rows.items():
            local_version = str(local_rows.get(component_id, "0.0.0"))
            if _is_newer(str(remote_version), local_version):
                updates.append(
                    ComponentUpdate(component_type, component_id, local_version, str(remote_version))
                )
    return updates
