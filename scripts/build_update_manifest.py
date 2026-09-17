#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build update_manifest.json for the self-update feature (services/update/*).

Run this before pushing a release once LOMA is published on GitHub (see
config/update_source.py). Writes update_manifest.json to the repo root.

File-list scope, by design:
- services / extensions: file lists are derived from the service registry's
  `module` and the extensions/<id>/ package directory, so they only ever
  cover that component's own code.
- app: a fixed allowlist of top-level source directories (see APP_DIRS /
  APP_FILES below) — never data/, venv/, tests/, docs/, scripts/, packaging/,
  or any bundled model weights. This allowlist is what guarantees an update
  never touches user data; extend it deliberately, not by scanning.

Only tracked (git ls-files) files are included, so .gitignore'd local state
(logs, caches, venv, data/) can never leak into the manifest.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Top-level directories that are pure app-core source (never user data).
APP_DIRS = ("pipeline", "ui", "config")
# Individual root-level app-core files.
APP_FILES = ("main.py",)


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return [line.strip() for line in out.splitlines() if line.strip()]


def _service_files(tracked: list[str], module: str) -> list[str]:
    rel = module.replace(".", "/")
    single = f"{rel}.py"
    if single in tracked:
        return [single]
    prefix = f"{rel}/"
    return sorted(f for f in tracked if f.startswith(prefix))


def _extension_files(tracked: list[str], extension_id: str) -> list[str]:
    prefix = f"extensions/{extension_id}/"
    return sorted(f for f in tracked if f.startswith(prefix))


def _app_files(tracked: list[str]) -> list[str]:
    files: list[str] = [f for f in tracked if f in APP_FILES]
    for d in APP_DIRS:
        prefix = f"{d}/"
        files.extend(f for f in tracked if f.startswith(prefix))
    return sorted(set(files))


def build_manifest() -> dict:
    from config.app_version import APP_VERSION
    from pipeline.registry.service_registry import service_registry
    from services.plugins.catalog import load_extension_catalog

    tracked = _tracked_files()

    services = {s.id: s.version for s in service_registry.list_all()}
    extensions = {
        row["id"]: row.get("version") or "0.1.0"
        for row in load_extension_catalog()
        if row.get("id")
    }

    service_files = {
        s.id: _service_files(tracked, s.module) for s in service_registry.list_all()
    }
    extension_files = {
        row["id"]: _extension_files(tracked, row["id"])
        for row in load_extension_catalog()
        if row.get("id")
    }

    return {
        "app_version": APP_VERSION,
        "services": services,
        "extensions": extensions,
        "files": {
            "app": _app_files(tracked),
            "services": service_files,
            "extensions": extension_files,
        },
    }


def main() -> int:
    manifest = build_manifest()
    out_path = ROOT / "update_manifest.json"
    out_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {out_path} (app_version={manifest['app_version']}, "
          f"{len(manifest['services'])} services, {len(manifest['extensions'])} extensions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
