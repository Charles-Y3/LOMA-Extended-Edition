#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke-import and registry alignment check after service/pipeline upgrades."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    errors: list[str] = []
    checks = [
        ("main", lambda: __import__("main")),
        ("pipeline.workflow", lambda: __import__("pipeline.workflow")),
        ("pipeline.direct.entry", lambda: __import__(
            "pipeline.direct.entry", fromlist=["run_direct"]
        )),
        ("artifact_build", lambda: __import__(
            "services.artifact_build", fromlist=["generate_output"]
        )),
        ("image_generation service", lambda: __import__(
            "services.image_generation", fromlist=["generate_image"]
        )),
        ("extensions registry", lambda: _check_extensions()),
        ("service registry", lambda: _check_services()),
        ("roles and contracts registry", lambda: _check_roles_contracts()),
        ("registry integrity", lambda: _check_integrity()),
    ]
    for name, fn in checks:
        try:
            fn()
            print(f"OK  {name}")
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print(f"FAIL {name}: {exc}")

    if errors:
        return 1
    print("All import checks passed.")
    return 0


def _check_extensions() -> None:
    from pipeline.registry.catalog import BUILTIN_EXTENSION_IDS
    from pipeline.registry.extension_registry import extension_registry

    extension_registry.discover()
    ids = set(extension_registry._extensions.keys())
    if ids != BUILTIN_EXTENSION_IDS:
        raise RuntimeError(
            f"Extension mismatch.\n  expected: {sorted(BUILTIN_EXTENSION_IDS)}\n  got: {sorted(ids)}"
        )


def _check_services() -> None:
    from pipeline.registry.service_registry import service_registry

    ids = set(service_registry.ids())
    required = {
        "llm_bridge",
        "model_router",
        "resource_governor",
        "file_io",
        "artifact_store",
        "context_selector",
        "media_transcription",
        "renderer",
        "image_generation",
        "sandbox_runner",
    }
    if not required.issubset(ids):
        raise RuntimeError(f"Missing services: {required - ids}")


def _check_roles_contracts() -> None:
    from pipeline.registry.integrity import validate_roles_and_contracts

    errors = validate_roles_and_contracts()
    if errors:
        raise RuntimeError("\n".join(errors))


def _check_integrity() -> None:
    from pipeline.registry.extension_registry import extension_registry
    from pipeline.registry.integrity import validate_all_registries

    extension_registry.discover()
    errors = validate_all_registries(extension_registry)
    if errors:
        raise RuntimeError("\n".join(errors))


if __name__ == "__main__":
    sys.exit(main())
