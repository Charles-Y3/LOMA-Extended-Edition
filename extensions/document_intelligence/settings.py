# -*- coding: utf-8 -*-
"""Persisted settings for Document Intelligence."""
from __future__ import annotations

import json
import os
import shutil

from services.plugins.paths import loma_app_data_root

# Was CWD-relative ("data/document_intelligence"), which drifted between launch
# methods (shortcut vs. double-click set different working directories) and predates
# the app's move to a fixed per-user AppData root — resolve against that instead so
# library data always lands in the same place regardless of how the .exe was started.
_ROOT = os.path.join(loma_app_data_root(), "document_intelligence")
_SETTINGS_PATH = os.path.join(_ROOT, "settings.json")
_LEGACY_ROOT = os.path.join("data", "document_intelligence")


def _migrate_legacy_root() -> None:
    """One-time move of libraries/settings already ingested under the old CWD-relative
    path, so switching to the AppData root doesn't orphan existing Document Intelligence
    data."""
    if os.path.isdir(_ROOT) or not os.path.isdir(_LEGACY_ROOT):
        return
    try:
        os.makedirs(os.path.dirname(_ROOT), exist_ok=True)
        shutil.move(_LEGACY_ROOT, _ROOT)
    except OSError:
        pass


_migrate_legacy_root()

DEFAULTS: dict = {
    "chunk_size_tokens": 320,
    "min_chunk_tokens": 60,
    "min_retrieval_tokens": 20,
    "chunks_per_document": 2,
    "max_sources": 8,
    "max_chunks_returned": 50,
    "score_cutoff": 0.5,
    "retrieval_depth": 40,
    "fast_score_gap_ratio": 1.5,
    "max_agent_iterations": 5,
    "agent_confidence_threshold": 0.75,
    "citation_required": True,
    "results_display_count": 20,
    "search_strategy": "Moderate",
    "document_passwords": "",
    "answer_model": "",
}


def data_root() -> str:
    os.makedirs(_ROOT, exist_ok=True)
    return _ROOT


def libraries_root() -> str:
    path = os.path.join(data_root(), "libraries")
    os.makedirs(path, exist_ok=True)
    return path


def load_settings() -> dict:
    data_root()
    if not os.path.isfile(_SETTINGS_PATH):
        return dict(DEFAULTS)
    try:
        with open(_SETTINGS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        out = dict(DEFAULTS)
        if isinstance(raw, dict):
            for key in DEFAULTS:
                if key in raw:
                    out[key] = raw[key]
        return out
    except Exception:
        return dict(DEFAULTS)


def save_settings(data: dict) -> None:
    data_root()
    merged = dict(DEFAULTS)
    merged.update(data or {})
    with open(_SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
