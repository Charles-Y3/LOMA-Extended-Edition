# -*- coding: utf-8 -*-
"""Localized text for model-catalog entries (descriptions), falling back to the catalog's English."""
from __future__ import annotations

from pipeline.i18n import TRANSLATIONS, get_locale, t as tr
from pipeline.i18n_catalog import slug


def localized_desc(entry: dict) -> str:
    """The catalog description in the UI language; the English original if that model has no translation
    (e.g. a model added to the catalog later)."""
    english = (entry.get("desc") or "").strip()
    key = f"catalog.desc.{slug(entry.get('name', ''))}"
    if key not in TRANSLATIONS.get(get_locale(), {}) and key not in TRANSLATIONS["en"]:
        return english
    return tr(key) or english
