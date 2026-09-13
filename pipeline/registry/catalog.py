# -*- coding: utf-8 -*-
"""Canonical ids for registry validation and routing."""
from __future__ import annotations

# Bundled extensions discovered from extensions/*/extension.py
BUILTIN_EXTENSION_IDS = frozenset(
    {
        "document_editor",
        "chat_archive_manager",
        "document_intelligence",
        "research",
        "token_tracker",
        "web_viewer",
        "history_events",
        "news_brief",
    }
)

# NiceGUI map-options: use value=None in ui.select; this id is the "None" row value on change.
EXTENSION_SELECT_NONE = "none"

# Route keys with role/contract wiring for direct pipeline
ROLE_CONTRACT_WIRED_ROUTE_KEYS = frozenset({"direct_pipeline"})
