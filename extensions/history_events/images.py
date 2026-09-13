# -*- coding: utf-8 -*-
"""Bundled encounter illustration paths."""
from __future__ import annotations

import os

from extensions.history_events.encounters import HistoricalEncounter

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
BUNDLED_IMAGE_DIR = os.path.join(_PKG_DIR, "assets", "encounters")
DATA_IMAGE_DIR = os.path.join("data", "history_events", "encounters")


def bundled_image_path(encounter_id: str) -> str:
    return os.path.join(BUNDLED_IMAGE_DIR, f"{encounter_id}.png")


def data_image_path(encounter_id: str) -> str:
    return os.path.join(DATA_IMAGE_DIR, f"{encounter_id}.png")


def get_encounter_image_path(encounter_id: str) -> str:
    """Return saved PNG for an encounter (bundled assets first, then data cache)."""
    for path in (bundled_image_path(encounter_id), data_image_path(encounter_id)):
        if path and os.path.isfile(path):
            return os.path.abspath(path)
    return ""


def resolve_encounter_image(enc: HistoricalEncounter) -> str:
    return get_encounter_image_path(enc.id)
