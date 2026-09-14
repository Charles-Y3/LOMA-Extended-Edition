# -*- coding: utf-8 -*-
"""Central schema-versioning + migration registry for persisted state.

Every persisted store (a JSON settings file, a profile, a Knowledge Vault index
sidecar, a glossary, a chat archive) carries an integer schema version under
VERSION_KEY. Code registers ordered migration steps per store; `migrate()` brings a
loaded blob from whatever version it was written at up to the current version, then
stamps it. This replaces the ad-hoc inline migrations scattered across the codebase
(settings.py's extension-id remap + highlight_di_* fallback, model_assignments'
migrate_discontinued_models, knowledge_vault.settings' _migrate_*_root) with one
mechanism, so the recurring "rename broke on-disk data" class of bug has a single
home and is impossible to forget.

Design:
- Migrations are pure functions `dict -> dict` (they may mutate-and-return the same
  dict; callers pass a copy via migrate()).
- Step N produces version N from version N-1. Unversioned/legacy data is treated as
  version 0, so registering step 1 upgrades every existing install exactly once.
- `migrate()` is idempotent: data already at the current version is returned
  unchanged (aside from an added stamp if missing).
- No I/O here. Loaders call migrate() after reading; savers let stamp() set the
  version on fresh data. Keeps this testable without touching disk.
"""
from __future__ import annotations

from typing import Callable, Dict

VERSION_KEY = "_schema_version"

Migration = Callable[[dict], dict]

# store name -> {target_version: migration_fn}
_REGISTRY: Dict[str, Dict[int, Migration]] = {}


def register(store: str, to_version: int, func: Migration) -> None:
    """Register a migration that produces `to_version` from `to_version - 1`.

    to_version must be >= 1 and unique per store."""
    if to_version < 1:
        raise ValueError(f"migration to_version must be >= 1, got {to_version}")
    steps = _REGISTRY.setdefault(store, {})
    if to_version in steps:
        raise ValueError(f"duplicate migration for store '{store}' version {to_version}")
    steps[to_version] = func


def migration(store: str, to_version: int) -> Callable[[Migration], Migration]:
    """Decorator form of register()."""

    def _wrap(func: Migration) -> Migration:
        register(store, to_version, func)
        return func

    return _wrap


def current_version(store: str) -> int:
    """Highest registered version for a store (0 if none registered)."""
    steps = _REGISTRY.get(store)
    if not steps:
        return 0
    return max(steps)


def _data_version(data: dict) -> int:
    try:
        return max(0, int(data.get(VERSION_KEY, 0)))
    except (TypeError, ValueError):
        return 0


def stamp(store: str, data: dict) -> dict:
    """Mark `data` as being at the store's current version (for freshly created data)."""
    data[VERSION_KEY] = current_version(store)
    return data


def migrate(store: str, data: dict) -> dict:
    """Upgrade `data` from its recorded version to the store's current version.

    Unregistered stores / already-current data are returned with only the version
    stamp ensured. Missing intermediate steps raise, so a gap in the sequence is a
    loud programming error, not silent data loss."""
    if not isinstance(data, dict):
        return data
    target = current_version(store)
    if target == 0:
        return data  # store not using the framework yet
    have = _data_version(data)
    steps = _REGISTRY.get(store, {})
    for v in range(have + 1, target + 1):
        func = steps.get(v)
        if func is None:
            raise KeyError(f"store '{store}' is missing migration to version {v}")
        data = func(data)
        if not isinstance(data, dict):
            raise TypeError(f"migration '{store}' v{v} must return a dict")
        data[VERSION_KEY] = v
    data[VERSION_KEY] = target
    return data


def reset_for_tests() -> None:
    """Clear the registry — test isolation only."""
    _REGISTRY.clear()
