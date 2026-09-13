# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CATALOG_PATH = _PROJECT_ROOT / "config" / "extension_catalog.json"
_EXTENSIONS_ROOT = _PROJECT_ROOT / "extensions"
_cache: list[dict[str, Any]] | None = None


def _json_catalog_by_id() -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in _load_json_catalog():
        ext_id = str(row.get("id") or "").strip()
        if ext_id:
            by_id[ext_id] = dict(row)
    return by_id


def json_catalog_row(extension_id: str) -> dict[str, Any] | None:
    """Single extension row from extension_catalog.json (no merge/cache)."""
    return _json_catalog_by_id().get(str(extension_id or "").strip())


def _load_json_catalog() -> list[dict[str, Any]]:
    if not _CATALOG_PATH.is_file():
        return []
    try:
        data = json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))
        return list(data.get("extensions") or [])
    except Exception:
        return []


def _scan_extensions_dir() -> list[dict[str, Any]]:
    """List every bundled extension package under LOMA/extensions."""
    rows: list[dict[str, Any]] = []
    if not _EXTENSIONS_ROOT.is_dir():
        return rows
    for pkg in sorted(_EXTENSIONS_ROOT.iterdir()):
        if not pkg.is_dir() or pkg.name.startswith("_"):
            continue
        if not (pkg / "extension.py").is_file():
            continue
        ext_id = pkg.name
        meta: dict[str, Any] = {}
        try:
            from pipeline.registry.extension_registry import extension_registry

            cls = extension_registry._extensions.get(ext_id)
            if cls is None:
                for _cid, _cls in extension_registry._extensions.items():
                    if getattr(_cls, "__module__", "").startswith(f"extensions.{pkg.name}"):
                        cls = _cls
                        break
            if cls is not None:
                inst = cls()
                meta = inst.metadata() or {}
                ext_id = (inst.extension_id or ext_id).strip()
        except Exception:
            pass
        rows.append(
            {
                "id": ext_id,
                "title": meta.get("label") or meta.get("title") or ext_id.replace("_", " ").title(),
                "description": meta.get("description", ""),
                "category": meta.get("category", "tools"),
                "version": meta.get("version") or "0.1.0",
                "bundled": True,
                "show_in_dropdown": meta.get("show_in_dropdown", True),
            }
        )
    return rows


def load_extension_catalog(*, force: bool = False) -> list[dict[str, Any]]:
    global _cache
    if force:
        _cache = None
    if _cache is not None:
        return _cache

    by_id: dict[str, dict[str, Any]] = {}
    for row in _scan_extensions_dir():
        ext_id = str(row.get("id") or "").strip()
        if ext_id:
            by_id[ext_id] = dict(row)

    for row in _load_json_catalog():
        ext_id = str(row.get("id") or "").strip()
        if not ext_id:
            continue
        merged = dict(by_id.get(ext_id) or {})
        merged.update({k: v for k, v in row.items() if v is not None and v != ""})
        merged.setdefault("id", ext_id)
        merged.setdefault("bundled", bool(merged.get("bundled", ext_id in by_id)))
        by_id[ext_id] = merged

    try:
        from pipeline.registry.extension_registry import extension_registry

        for ext_id, cls in extension_registry._extensions.items():
            if ext_id in by_id:
                meta = cls().metadata() or {}
                entry = by_id[ext_id]
                if meta.get("label"):
                    entry["title"] = meta["label"]
                if meta.get("description"):
                    entry["description"] = meta["description"]
                if meta.get("version"):
                    entry["version"] = meta["version"]
                if "show_in_dropdown" in meta:
                    entry["show_in_dropdown"] = meta["show_in_dropdown"]
                continue
            meta = cls().metadata() or {}
            by_id[ext_id] = {
                "id": ext_id,
                "title": meta.get("label") or ext_id,
                "description": meta.get("description", ""),
                "category": meta.get("category", "tools"),
                "version": meta.get("version") or "0.1.0",
                "bundled": True,
                "show_in_dropdown": meta.get("show_in_dropdown", True),
            }
    except Exception:
        pass

    for ext_id, json_row in _json_catalog_by_id().items():
        if ext_id not in by_id:
            continue
        entry = by_id[ext_id]
        if json_row.get("category"):
            entry["category"] = json_row["category"]
        for key in ("show_in_library", "show_in_dropdown", "requires", "bundled"):
            if key in json_row and json_row[key] is not None:
                entry[key] = json_row[key]

    _cache = sorted(by_id.values(), key=lambda r: (r.get("title") or r.get("id") or "").lower())
    return _cache


def catalog_entry(extension_id: str) -> dict[str, Any] | None:
    for row in load_extension_catalog():
        if row.get("id") == extension_id:
            return row
    return None
