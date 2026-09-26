# -*- coding: utf-8 -*-
"""Decks must follow the UI language: the standard slides are recognised in every language (no second,
English 'Agenda' inserted next to a correct '議程') and every fallback string is localized."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("pipeline.workflow")

from pipeline import deck_i18n as d  # noqa: E402
from pipeline import i18n  # noqa: E402
from services.session import state  # noqa: E402
from services import presentation_markdown as pm  # noqa: E402

LOCALES = ("en", "zh_tw", "zh_cn", "es", "de")
ENGLISH_STANDARD = re.compile(r"\b(Agenda|Key Takeaways|Conclusion|Summary|Thank you|Section \d|Key topic)\b")


@pytest.fixture
def use_locale(monkeypatch):
    def _set(loc):
        monkeypatch.setattr(state, "current_settings", {"language": loc})

    yield _set


@pytest.mark.parametrize("loc", LOCALES)
def test_own_agenda_and_closing_titles_are_recognised_in_every_language(use_locale, loc):
    use_locale(loc)
    assert d.is_agenda_title(i18n.t("deck.agenda"))
    assert d.is_bare_agenda_title(i18n.t("deck.agenda"))
    assert d.is_agenda_title(i18n.t("deck.outline"))
    for key in ("deck.conclusion", "deck.summary", "deck.key_takeaways"):
        assert d.is_bare_closing_title(i18n.t(key)), (loc, key)
    assert d.is_filler_section_title(i18n.t("deck.section", n=3))
    assert d.is_filler_section_title(i18n.t("deck.key_topic", n=2))


def test_content_titles_are_not_mistaken_for_standard_slides():
    for title in ("夜市的歷史淵源與發展", "重點產品介紹", "Summary of results", "Agendas of the world", "Inhaltsstoffe"):
        assert not d.is_agenda_title(title) or title.startswith("Inhalt") is False, title
    assert not d.is_closing_title("重點產品介紹")
    assert not d.is_bare_closing_title("Summary of results")
    assert d.is_closing_title("Practical takeaways")


@pytest.mark.parametrize("loc", ["zh_tw", "zh_cn", "es", "de"])
def test_english_standard_titles_are_localized_for_display(use_locale, loc):
    use_locale(loc)
    for english in ("Agenda", "Key Takeaways", "Conclusion", "Summary", "Outline"):
        shown = d.localize_standard_title(english)
        assert shown != english or loc in ("es", "de") and english == "Agenda", (loc, english, shown)
    assert d.localize_standard_title("夜市的歷史") == "夜市的歷史"  # real content titles untouched


def test_english_titles_unchanged_in_english(use_locale):
    use_locale("en")
    assert d.localize_standard_title("Agenda") == "Agenda"


def _zh_deck() -> list[str]:
    text = (
        "--- Slide 1 ---\n# 台灣夜市文化\n\n"
        "--- Slide 2 ---\n## 議程\n- 歷史淵源\n- 美食特色\n- 未來展望\n\n"
        "--- Slide 3 ---\n## 歷史淵源\n- 夜市起源於早期市集\n- 融合多元文化\n\n"
        "--- Slide 4 ---\n## 美食特色\n- 蚵仔煎\n- 珍珠奶茶\n\n"
        "--- Slide 5 ---\n## 結論\n- 夜市是台灣的文化名片\n- 值得持續保存\n"
    )
    return pm.split_presentation_slides(text)


def test_a_correct_chinese_agenda_makes_the_deck_well_structured():
    """Before: only 'agenda|outline' counted, so this deck was 'missing its agenda'."""
    assert pm.is_well_structured_deck(_zh_deck())


def test_a_correct_chinese_agenda_is_not_scrubbed_as_junk():
    kept = pm.scrub_junk_slides(_zh_deck())
    assert len(kept) == len(_zh_deck())


@pytest.mark.parametrize("loc", ["zh_tw", "zh_cn", "es", "de"])
def test_generated_fallback_structure_has_no_english_standard_titles(use_locale, loc):
    use_locale(loc)
    md = pm.ensure_deck_structure("", deck_title="")
    # "Agenda" is also the correct Spanish/German word, so it is only English-only for Chinese.
    english = ENGLISH_STANDARD if loc.startswith("zh") else re.compile(ENGLISH_STANDARD.pattern.replace("Agenda|", ""))
    assert not english.search(md), md
    md2 = pm.ensure_deck_structure("--- Slide 1 ---\n# X\n--- Slide 2 ---\n## Y\n- a\n- b\n- c", deck_title="X")
    if loc.startswith("zh"):
        assert not re.search(r"^## Agenda$", md2, re.M), md2


@pytest.mark.parametrize("loc", ["zh_tw", "zh_cn", "es", "de"])
def test_filler_bullets_are_localized(use_locale, loc):
    use_locale(loc)
    bullets = pm._synthetic_bullets("Topic", query="")
    assert bullets and not any(b.startswith(("Clarify", "Give one", "End with")) for b in bullets), bullets


def test_chat_messages_are_localized(use_locale):
    use_locale("zh_tw")
    planned = i18n.t("deck.planned", n=8)
    assert "8" in planned and "Compiling" not in planned and "Deck planned" not in planned
    assert "Processing" not in i18n.t("deck.processing_file")
    assert "ready" not in i18n.t("deck.output_ready", label="簡報", name="a.pptx")
    assert "{" not in planned


@pytest.mark.parametrize("loc", LOCALES)
def test_deck_and_message_tables_are_complete_and_consistent(loc):
    from pipeline.i18n_deck import DECK_STRINGS
    from pipeline.i18n_messages import MESSAGE_STRINGS

    ph = re.compile(r"\{(\w+)\}")
    for table in (DECK_STRINGS, MESSAGE_STRINGS):
        assert set(table[loc]) == set(table["en"]), (loc, set(table["en"]) ^ set(table[loc]))
        for key, text in table[loc].items():
            assert set(ph.findall(text)) == set(ph.findall(table["en"][key])), (loc, key)
            assert key in i18n.TRANSLATIONS[loc]


def test_no_hardcoded_english_left_at_the_audited_sites():
    """Regression guard for the strings found by the audit."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    banned = {
        "pipeline/deliverables/deck_pipeline.py": ['title="Agenda"', 'title="Key Takeaways"', '"Thank you."'],
        "pipeline/deliverables/presentation_deck.py": ['title = "Agenda"', 'f"Section {', 'f"Key topic {', '"Summary & Next Steps"'],
        "pipeline/direct/step_executor.py": ["Deck planned", "Processing your file", "Image generation failed:", "Poster generation failed:"],
        "services/session/handlers.py": ['ui.notify("Workspace rebooted.', "Upload process failed"],
        "services/artifact_build.py": ['add_heading("Contents"', '"Slide Section"'],
    }
    for rel, needles in banned.items():
        text = (root / rel).read_text(encoding="utf-8")
        # log lines (sink.log / state.add_log) may stay English: strip them before checking
        text = re.sub(r"(?m)^\s*(?:sink\.log|state\.add_log|_log|log_fn)\(.*$", "", text)
        for needle in needles:
            assert needle not in text, (rel, needle)
