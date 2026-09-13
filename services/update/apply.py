# -*- coding: utf-8 -*-
"""Apply a single component update by re-downloading only its changed files.

Modular by design: the remote manifest's "files" map lists exactly which
repo-relative files belong to each service/extension/app-core component, so
applying an update only touches those files — never a full-app redownload.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path
from typing import Any

from config.update_source import manifest_url, raw_file_url

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _fetch_json(url: str, timeout: float = 5.0) -> dict[str, Any] | None:
    if not url:
        return None
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _component_files(component_type: str, component_id: str) -> list[str]:
    """Repo-relative file list for a component, from the remote manifest's file map."""
    remote = _fetch_json(manifest_url())
    if not remote:
        return []
    kind_key = {"service": "services", "extension": "extensions", "app": "app"}.get(component_type)
    if not kind_key:
        return []
    files_map = remote.get("files") or {}
    if kind_key == "app":
        return list(files_map.get("app") or [])
    return list((files_map.get(kind_key) or {}).get(component_id) or [])


def apply_update(component_type: str, component_id: str) -> tuple[bool, str]:
    """Download every file listed for this component, then write them in place.

    All downloads must succeed before anything is written, so a failed apply
    never leaves a component half-updated.
    """
    files = _component_files(component_type, component_id)
    if not files:
        return False, f"No file list found for {component_type} '{component_id}'."

    downloaded: dict[str, bytes] = {}
    for rel_path in files:
        url = raw_file_url(rel_path)
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                downloaded[rel_path] = resp.read()
        except Exception as exc:
            return False, f"Download failed for {rel_path}: {exc}"

    for rel_path, data in downloaded.items():
        dest = _PROJECT_ROOT / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    return True, f"Updated {component_type} '{component_id}' ({len(downloaded)} file(s))."
