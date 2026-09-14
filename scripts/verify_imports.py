#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Smoke-import and registry alignment check after service/pipeline upgrades."""
from __future__ import annotations

import os
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
        ("spine boundaries", lambda: _check_spine_boundaries()),
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


def _check_spine_boundaries() -> None:
    """Enforce the shared spine (docs/PIPELINE_REFACTOR.md §0.5): no module may import
    an LLM backend directly — all inference goes through services.llm_bridge, so the
    Context Governor and provider-capability handling apply to every call. Only the
    provider adapters themselves and a few backend-management helpers are exempt.

    A name heuristic (module-level `import ollama` / `import openai`), deliberately
    conservative: it catches a new surface reaching past the spine without flagging
    legitimate provider code. Add a genuinely new backend-management module to
    _SPINE_EXEMPT only with a clear reason.
    """
    import re

    exempt = {
        os.path.normpath(p)
        for p in (
            "services/providers/ollama_provider.py",
            "services/providers/lmstudio_provider.py",
            "services/providers/base.py",
            "services/providers/registry.py",
            "services/inference/ollama_chat.py",
            "config/__init__.py",
            # Backend-management (model listing/warmup/capability) — Ollama-specific by
            # nature, not inference calls; tracked as consolidation debt (ledger §8).
            "services/model_router.py",
            "services/model_assignments.py",
            "services/startup_warmup.py",
        )
    }
    skip_dirs = {"venv", "dist", "build", "__pycache__", ".git", "node_modules", ".pytest_cache"}
    pattern = re.compile(r"^\s*(?:import\s+(?:ollama|openai)\b|from\s+(?:ollama|openai)\b)")
    offenders: list[str] = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in skip_dirs]
        for fn in files:
            if not fn.endswith(".py"):
                continue
            full = Path(base) / fn
            rel = os.path.normpath(str(full.relative_to(ROOT)))
            if rel in exempt or rel.startswith(os.path.normpath("scripts/")):
                continue
            try:
                text = full.read_text(encoding="utf-8")
            except Exception:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if pattern.match(line):
                    offenders.append(f"{rel}:{i}: {line.strip()}")
    if offenders:
        raise RuntimeError(
            "Direct LLM-backend import bypasses the spine (use services.llm_bridge):\n  "
            + "\n  ".join(offenders)
        )


if __name__ == "__main__":
    sys.exit(main())
