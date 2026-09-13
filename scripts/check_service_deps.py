#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail if top-level services/*.py import other top-level services/*.py."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVICES = ROOT / "services"

# Infrastructure modules allowed to reference session (not atomic in strict sense)
ALLOWED_CROSS = {
    ("logger.py", "session_store.py"),
    ("logger.py", "session"),
    ("image_generation.py", "resource_governor.py"),
    ("image_generation.py", "model_router.py"),
    ("renderer.py", "file_io.py"),
    ("resource_governor.py", "image_generation.py"),
}


def _top_level_service_modules() -> set[str]:
    return {p.stem for p in SERVICES.glob("*.py") if p.name != "__init__.py"}


def _imports_in_file(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            mod = node.module
            if mod.startswith("services.") and not mod.startswith("services."):
                pass
            parts = mod.split(".")
            if parts[0] == "services" and len(parts) >= 2:
                if parts[1] in _top_level_service_modules():
                    found.append(parts[1])
            elif parts[0] == "services" and len(parts) == 1:
                pass
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("services."):
                    sub = alias.name.split(".", 2)
                    if len(sub) >= 2 and sub[1] in _top_level_service_modules():
                        found.append(sub[1])
    return found


def main() -> int:
    modules = _top_level_service_modules()
    violations: list[str] = []
    for py in sorted(SERVICES.glob("*.py")):
        if py.name == "__init__.py":
            continue
        for imp in _imports_in_file(py):
            if imp == py.stem:
                continue
            pair = (py.name, f"{imp}.py")
            if pair in ALLOWED_CROSS:
                continue
            if imp in modules:
                violations.append(f"{py.name} imports services.{imp}")
    if violations:
        print("Service dependency violations (atomic rule):")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("OK: no disallowed cross-imports among top-level services.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
