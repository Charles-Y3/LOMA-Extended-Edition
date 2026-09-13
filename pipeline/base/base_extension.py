# -*- coding: utf-8 -*-
"""Template for interactive LOMA extensions (UI tools in /extensions)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseExtension(ABC):
    """Extension modules provide user-facing workspace tools."""

    extension_id: str = ""
    label: str = ""
    show_in_dropdown: bool = True

    @abstractmethod
    def mount(self, container: Any) -> None:
        """Render extension UI into the supplied NiceGUI container."""

    def metadata(self) -> dict[str, Any]:
        """Merge class fields with extension_catalog.json when present."""
        ext_id = self.extension_id or ""
        meta: dict[str, Any] = {
            "id": ext_id,
            "label": self.label or ext_id,
            "show_in_dropdown": self.show_in_dropdown,
        }
        try:
            from services.plugins.catalog import catalog_entry, json_catalog_row

            json_row = json_catalog_row(ext_id) or {}
            row = catalog_entry(ext_id) or {}
            from pipeline.extension_i18n import extension_description, extension_title

            fb_title = json_row.get("title") or row.get("title") or self.label or ext_id
            fb_desc = json_row.get("description") or row.get("description") or ""
            meta["title"] = extension_title(ext_id, fb_title)
            meta["label"] = meta["title"]
            meta["description"] = extension_description(ext_id, fb_desc)
            category = json_row.get("category") or row.get("category")
            if category:
                meta["category"] = category
            meta["version"] = row.get("version") or "0.1.0"
            if "bundled" in row:
                meta["bundled"] = row["bundled"]
            if row.get("show_in_dropdown") is False:
                meta["show_in_dropdown"] = False
        except Exception:
            pass
        return meta
