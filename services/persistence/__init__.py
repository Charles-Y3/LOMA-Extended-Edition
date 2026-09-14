# -*- coding: utf-8 -*-
"""Persistence spine: central schema-versioning + migration for on-disk state.

Part of the shared architecture (see docs/PIPELINE_REFACTOR.md §0.5). One place
every persisted store (settings, profiles, Knowledge Vault index sidecars,
glossaries, archives) stamps a version and runs ordered migrations, so a rename or
moved module never again silently breaks on-disk data.
"""
from services.persistence.migrations import (  # noqa: F401
    VERSION_KEY,
    current_version,
    migrate,
    migration,
    register,
    stamp,
)
