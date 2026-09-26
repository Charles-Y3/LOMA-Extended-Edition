# -*- coding: utf-8 -*-
"""Strings that used to be hardcoded English (installer dialogs, small UI notices) must exist in
every supported locale with the same placeholders, and the plain_text() helper must strip markdown."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pipeline.workflow")

from pipeline import i18n  # noqa: E402
from pipeline.i18n_installer import INSTALLER_STRINGS  # noqa: E402
from pipeline.i18n_ui_misc import UI_MISC_STRINGS  # noqa: E402

_PLACEHOLDER = re.compile(r"\{(\w+)\}")
_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("table", [INSTALLER_STRINGS, UI_MISC_STRINGS], ids=["installer", "ui_misc"])
def test_every_locale_has_every_key_with_same_placeholders(table):
    assert set(table) == set(i18n.SUPPORTED_LOCALES)
    base = table["en"]
    for locale in i18n.SUPPORTED_LOCALES:
        assert set(table[locale]) == set(base), f"{locale} keys differ"
        for key, text in table[locale].items():
            assert set(_PLACEHOLDER.findall(text)) == set(_PLACEHOLDER.findall(base[key])), (locale, key)
            assert text.strip(), (locale, key)


@pytest.mark.parametrize("locale", ["en", "zh_tw", "zh_cn", "es", "de"])
def test_merged_into_runtime_translations(locale):
    for table in (INSTALLER_STRINGS, UI_MISC_STRINGS):
        for key in table["en"]:
            assert key in i18n.TRANSLATIONS[locale], (locale, key)


def test_keys_used_in_code_exist():
    used: set[str] = set()
    for rel in ("ui/components/capability_installer.py", "ui/components/chat_message.py",
                "ui/components/chat_summarize.py", "ui/components/source_card.py",
                "ui/components/viewer_query.py", "ui/themes/assets.py"):
        text = (_ROOT / rel).read_text(encoding="utf-8")
        used |= set(re.findall(r'tr\("((?:installer|ui)\.[a-z_]+)"', text))
    assert used, "no keys found — pattern broke"
    missing = used - set(INSTALLER_STRINGS["en"]) - set(UI_MISC_STRINGS["en"])
    assert not missing, missing


def test_installer_has_no_hardcoded_english_left():
    text = (_ROOT / "ui/components/capability_installer.py").read_text(encoding="utf-8")
    leftovers = re.findall(r'(?:ui\.(?:label|button|markdown)|set_text)\(\s*f?"[A-Z]', text)
    assert not leftovers, leftovers


def test_placeholder_formatting_works():
    assert "8" in i18n.t("installer.vision_body", ram=8)
    assert "GTX" in i18n.t("installer.gpu_body", gpu="GTX", vram=", 6 GB VRAM")


def test_plain_text_strips_markdown():
    assert i18n.plain_text("Click **New encounter** to begin.") == "Click New encounter to begin."
    assert i18n.plain_text("Could not install `{key}`.") == "Could not install {key}."


def test_image_result_message_has_no_hardcoded_english_labels():
    text = (_ROOT / "pipeline/direct/step_executor.py").read_text(encoding="utf-8")
    for literal in ("**Image ready**", "**Image prompt:**", "**Composite:**", "**Edit:**"):
        assert literal not in text, literal


def test_image_result_labels_translated_everywhere():
    for locale in i18n.SUPPORTED_LOCALES:
        for key in ("chat.image_prompt_label", "chat.image_ready", "chat.image_composite_label", "chat.image_edit_label"):
            assert i18n.TRANSLATIONS[locale][key].strip(), (locale, key)
    assert i18n.TRANSLATIONS["zh_tw"]["chat.image_ready"] != i18n.TRANSLATIONS["en"]["chat.image_ready"]
