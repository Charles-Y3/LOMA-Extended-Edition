# -*- coding: utf-8 -*-
"""Local component-version manifest (app + services + extensions)."""
from __future__ import annotations

from typing import Any

from config.app_version import APP_VERSION


def local_manifest() -> dict[str, Any]:
    from pipeline.registry.service_registry import service_registry
    from services.plugins.catalog import load_extension_catalog

    services = {s.id: s.version for s in service_registry.list_all()}
    extensions = {
        row["id"]: row.get("version") or "0.1.0"
        for row in load_extension_catalog()
        if row.get("id")
    }
    return {"app_version": APP_VERSION, "services": services, "extensions": extensions}
