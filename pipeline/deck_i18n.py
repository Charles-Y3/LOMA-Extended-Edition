# -*- coding: utf-8 -*-
"""Language-aware helpers for deck slide titles.

The deck code used to recognise the standard slides only by their English titles ("Agenda",
"Conclusion"...) and to insert English defaults. In any other language a correct agenda slide
("議程") was judged missing and a second English "Agenda" was inserted. Everything that recognises
or produces a standard title goes through here instead.
"""
from __future__ import annotations

import re

from pipeline.i18n import SUPPORTED_LOCALES, TRANSLATIONS, t as tr
from pipeline.query_intent_i18n import CONCEPTS

_CJK = re.compile(r"[㐀-鿿]")


def _phrases(concept: str) -> list[str]:
    table = CONCEPTS[concept]
    out = [p.lower() for loc in SUPPORTED_LOCALES for p in table.get(loc, ())]
    return sorted(set(out), key=len, reverse=True)


def _starts_with(title: str, concept: str) -> bool:
    lower = (title or "").strip().lower()
    if not lower:
        return False
    for phrase in _phrases(concept):
        if lower.startswith(phrase):
            rest = lower[len(phrase):]
            if not rest or _CJK.search(phrase) or not (rest[0].isalnum() or rest[0] == "_"):
                return True
    return False


def _equals(title: str, concept: str) -> bool:
    lower = re.sub(r"[\s:：.。]+$", "", (title or "").strip().lower())
    return bool(lower) and lower in _phrases(concept)


def is_agenda_title(title: str) -> bool:
    """'Agenda' / 'Outline' / '議程' / 'Gliederung: …' ... in any supported language."""
    return _starts_with(title, "deck_agenda_title")


def is_bare_agenda_title(title: str) -> bool:
    """Just the word ('Agenda', '議程'), with no descriptive suffix."""
    return _equals(title, "deck_agenda_title")


def is_bare_closing_title(title: str) -> bool:
    """The whole title is 'Summary' / 'Conclusion' / 'Key Takeaways' / '結論' / 'Fazit' ..."""
    return _equals(title, "deck_closing_title")


def is_closing_title(title: str) -> bool:
    """A closing slide: a bare closing title, or one that mentions takeaways / next steps."""
    lower = (title or "").strip().lower()
    if not lower:
        return False
    return is_bare_closing_title(title) or any(p in lower for p in _phrases("deck_closing_contains"))


def _template_regex(key: str) -> re.Pattern[str]:
    parts = []
    for loc in SUPPORTED_LOCALES:
        template = TRANSLATIONS.get(loc, {}).get(key)
        if template:
            parts.append(re.escape(template).replace(re.escape("{n}"), r"\d+"))
    return re.compile(r"^(?:" + "|".join(sorted(set(parts))) + r")$", re.IGNORECASE)


def is_filler_section_title(title: str) -> bool:
    """Our own "Section 3" / "Key topic 2" fallback titles, in every language."""
    t = " ".join((title or "").split())
    return bool(_template_regex("deck.section").match(t) or _template_regex("deck.key_topic").match(t))


_STANDARD_KEYS = {
    "agenda": "deck.agenda",
    "outline": "deck.outline",
    "conclusion": "deck.conclusion",
    "summary": "deck.summary",
    "key takeaways": "deck.key_takeaways",
    "takeaways": "deck.key_takeaways",
}


def localize_standard_title(title: str) -> str:
    """Show the standard English titles the model (or our fallbacks) may have produced in the
    user's language. Titles that are already localized, or are real content titles, are untouched."""
    raw = (title or "").strip()
    key = _STANDARD_KEYS.get(re.sub(r"[\s:：.。]+$", "", raw.lower()))
    return tr(key) if key else title
