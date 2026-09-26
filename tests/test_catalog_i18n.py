# -*- coding: utf-8 -*-
"""Every model-catalog description shown to the user must exist in all supported languages."""
from __future__ import annotations

import pytest

pytest.importorskip("pipeline.workflow")

from config.model_catalog import MODEL_CATALOG, SUBSYSTEM_CATEGORIES  # noqa: E402
from pipeline import i18n  # noqa: E402
from pipeline.i18n_catalog import slug  # noqa: E402
from services import catalog_i18n  # noqa: E402
from services.session import state  # noqa: E402

ENTRIES = [e for entries in MODEL_CATALOG.values() for e in entries if (e.get("desc") or "").strip()]


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e["name"])
def test_every_catalog_description_is_translated_in_every_locale(entry):
    key = f"catalog.desc.{slug(entry['name'])}"
    assert i18n.TRANSLATIONS["en"][key] == entry["desc"].strip(), "English text must match the catalog"
    for loc in i18n.SUPPORTED_LOCALES:
        assert i18n.TRANSLATIONS[loc].get(key, "").strip(), (loc, entry["name"])
    for loc in ("zh_tw", "zh_cn", "es", "de"):
        assert i18n.TRANSLATIONS[loc][key] != entry["desc"], (loc, entry["name"], "still English")


def test_localized_desc_follows_the_language_setting(monkeypatch):
    entry = next(e for e in ENTRIES if e["name"] == "llava:7b")
    monkeypatch.setattr(state, "current_settings", {"language": "zh_tw"})
    assert catalog_i18n.localized_desc(entry) == "記憶體吃緊時使用的輕量版 LLaVA。"
    monkeypatch.setattr(state, "current_settings", {"language": "en"})
    assert catalog_i18n.localized_desc(entry) == entry["desc"]


def test_unknown_model_falls_back_to_its_english_description(monkeypatch):
    monkeypatch.setattr(state, "current_settings", {"language": "zh_tw"})
    entry = {"name": "brand-new/model:1b", "desc": "A model added later."}
    assert catalog_i18n.localized_desc(entry) == "A model added later."


@pytest.mark.parametrize("cat", SUBSYSTEM_CATEGORIES, ids=lambda c: c["key"])
def test_subsystem_rows_are_translated(cat):
    for loc in i18n.SUPPORTED_LOCALES:
        assert i18n.TRANSLATIONS[loc].get(f"subsystem.{cat['key']}", "").strip(), (loc, cat["key"])


@pytest.mark.parametrize("loc", i18n.SUPPORTED_LOCALES)
def test_subsystem_statuses_exist(loc):
    for key in ("status_ready", "status_missing", "status_planned", "coming_soon"):
        assert i18n.TRANSLATIONS[loc][f"subsystem.{key}"].strip()
