# -*- coding: utf-8 -*-
"""Localized era bucket labels for History Events."""
from __future__ import annotations

from extensions.history_events.encounters import ERA_ANY
from pipeline.i18n import t as tr

_ERA_KEYS = {
    ERA_ANY: "history_events.era_any",
    "ancient": "history_events.era_ancient",
    "medieval": "history_events.era_medieval",
    "early_modern": "history_events.era_early_modern",
    "nineteenth": "history_events.era_nineteenth",
    "twentieth": "history_events.era_twentieth",
    "twenty_first": "history_events.era_twenty_first",
}


def era_bucket_options() -> dict[str, str]:
    return {key: tr(i18n_key) for key, i18n_key in _ERA_KEYS.items()}
