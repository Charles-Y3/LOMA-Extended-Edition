# -*- coding: utf-8 -*-
"""Cross-registry validation after discover() — extensions, services, roles, contracts."""
from __future__ import annotations

from pathlib import Path

from pipeline.registry.catalog import (
    BUILTIN_EXTENSION_IDS,
    EXTENSION_SELECT_NONE,
    ROLE_CONTRACT_WIRED_ROUTE_KEYS,
)
from pipeline.registry.extension_registry import ExtensionRegistry, normalize_extension_id
from pipeline.registry.service_registry import service_registry

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EXTENSIONS_ROOT = _PROJECT_ROOT / "extensions"
_EXTENSION_PACKAGE_DIRS: dict[str, str] = {}


def _extension_package_path(ext_id: str) -> Path:
    return _EXTENSIONS_ROOT / _EXTENSION_PACKAGE_DIRS.get(ext_id, ext_id)


def validate_roles_and_contracts() -> list[str]:
    from pipeline.contracts.registry import get_contract_for_role
    from pipeline.roles.registry import classify_capability_roles, get_role

    errors: list[str] = []
    probes = {
        "direct_pipeline": ("summarize then translate", {}),
    }
    for route_key in sorted(ROLE_CONTRACT_WIRED_ROUTE_KEYS):
        if route_key not in probes:
            errors.append(f"{route_key}: add a classify probe in validate_roles_and_contracts()")
            continue
        query, kwargs = probes[route_key]
        try:
            role_ids = classify_capability_roles(query, route_key, **kwargs)
            if not role_ids:
                errors.append(f"{route_key}: classify_capability_roles returned empty")
                continue
            for rid in role_ids:
                get_role(rid, route_key)
                get_contract_for_role(rid, route_key)
        except Exception as exc:
            errors.append(f"{route_key}: roles/contracts wiring failed: {exc}")
    return errors


def validate_extension_packages(registry: ExtensionRegistry) -> list[str]:
    errors: list[str] = []
    discovered = set(registry._extensions.keys())

    missing = BUILTIN_EXTENSION_IDS - discovered
    if missing:
        errors.append(f"Extensions not discovered (missing extension.py?): {sorted(missing)}")

    for ext_id in sorted(BUILTIN_EXTENSION_IDS):
        pkg = _extension_package_path(ext_id)
        if not (pkg / "extension.py").is_file():
            errors.append(f"{ext_id}: missing extension.py")
        if not (pkg / "__init__.py").is_file():
            errors.append(f"{ext_id}: missing __init__.py (required by template)")

    try:
        opts = registry.dropdown_options()
        if not opts:
            errors.append("extension dropdown_options() returned empty")
        elif opts[0].get("value") in ("", None):
            errors.append(
                "extension dropdown 'None' must use EXTENSION_SELECT_NONE sentinel, not empty string"
            )
        elif opts[0].get("value") != EXTENSION_SELECT_NONE:
            errors.append(
                f"extension dropdown first value must be {EXTENSION_SELECT_NONE!r}"
            )
    except Exception as exc:
        errors.append(f"extension dropdown_options failed: {exc}")

    try:
        from services.plugins.catalog import load_extension_catalog

        # force=True: this runs after discover() has populated the registry, but the
        # catalog module caches its merged view — an earlier caller that read the catalog
        # before discovery (e.g. during settings load) would otherwise leave a stale/
        # incorrectly-aliased entry cached for the rest of the process's life.
        catalog = load_extension_catalog(force=True)
        for row in catalog:
            if not row.get("bundled"):
                continue
            eid = row.get("id") or ""
            if eid and eid not in discovered:
                errors.append(f"Catalog bundled extension not registered: {eid}")
    except Exception as exc:
        errors.append(f"extension catalog check failed: {exc}")

    if normalize_extension_id(EXTENSION_SELECT_NONE) != "":
        errors.append("normalize_extension_id(EXTENSION_SELECT_NONE) must return ''")
    return errors


def validate_all_registries(
    extension_registry: ExtensionRegistry | None = None,
) -> list[str]:
    errors = validate_roles_and_contracts()
    if extension_registry is not None:
        errors += validate_extension_packages(extension_registry)
    return errors
