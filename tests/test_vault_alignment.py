# -*- coding: utf-8 -*-
"""Tests for SOPA-style vault alignment."""
from __future__ import annotations

import unittest

from extensions.document_intelligence.translation import TranslationIndex, TranslationPair
from services.formslator.vault_alignment import (
    get_precise_pair,
    is_obviously_wrong,
    is_valid_vault_output,
    search_vault_translation,
)


class VaultAlignmentTests(unittest.TestCase):
    def _index(self, pairs: list[tuple[str, str]]) -> TranslationIndex:
        idx = TranslationIndex()
        idx.build(
            [
                TranslationPair(source_text=s, target_text=t)
                for s, t in pairs
            ]
        )
        return idx

    def test_literal_pair(self) -> None:
        idx = self._index([("你好", "Hello")])
        hit = search_vault_translation("你好", idx, allow_llm=False)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.translation_text, "Hello")
        self.assertGreaterEqual(hit.alignment_score, 0.9)

    def test_anchor_extract_proverb(self) -> None:
        zh = "俗語說：「有緣千里來相會，無緣對面不相識。」"
        en = (
            "A proverb says: “Those with affinity will come together from thousands of miles apart, "
            "while those without it will not get to know one another even when face to face.” "
            "Further commentary continues here."
        )
        _, winner = get_precise_pair(zh, en, zh)
        self.assertNotEqual(winner, "Not Found")
        self.assertTrue(is_valid_vault_output(zh, winner))
        self.assertIn("proverb", winner.lower())

    def test_anchor_extract_buddha(self) -> None:
        zh = "佛云：「天雨雖大，不潤無根之草，佛法雖廣，難渡無緣之人。」"
        en = (
            "The Buddha said, “Although the rain from heaven is abundant, it will not moisten "
            "grasses without roots; although Buddhist dharma is vast, it is difficult to save "
            "those without affinity.” Additional text follows."
        )
        idx = self._index([(zh + "\n更多段落。", en + "\nMore paragraphs.")])
        hit = search_vault_translation(zh, idx, allow_llm=False)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertTrue(is_valid_vault_output(zh, hit.translation_text))
        self.assertIn("Buddha", hit.translation_text)
        self.assertNotIn("Additional text", hit.translation_text)

    def test_invalid_chinese_echo_rejected(self) -> None:
        zh = "佛云：「天雨雖大」"
        self.assertFalse(is_valid_vault_output(zh, "天雨雖大"))

    def test_literal_long_paragraph_not_trimmed(self) -> None:
        zh = (
            "在這浩瀚的宇宙當中，人們能結為父子、夫婦、兄弟、朋友，這並非是巧合，"
            "也不是偶然相遇，這其中必有很深切的因緣。故大家能夠在這神聖莊嚴的佛堂相聚，"
            "非是過去一世兩世所結下的緣。"
        )
        en = (
            "In this vast universe, people can become fathers and sons, husbands and wives, "
            "brothers, and friends. This is neither coincidence nor chance encounter; "
            "there must be very deep causes and conditions at play. Therefore, everyone can "
            "gather in this sacred and dignified fotang due to the affinity with buddhas "
            "that was planted in past lives."
        )
        idx = self._index([(zh, en)])
        hit = search_vault_translation(zh, idx, allow_llm=False)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.method, "literal")
        self.assertIn("universe", hit.translation_text.lower())
        self.assertIn("fotang", hit.translation_text.lower())

    def test_overlong_bleed_flags_llm_path(self) -> None:
        zh = "今天後學要與各位前賢共同學習的題目是「修緣、修圓」，"
        zh_doc = (
            "佛云：「天雨雖大，不潤無根之草，佛法雖廣，難渡無緣之人。」\n"
            f"{zh}\n"
            "「緣」這個字，是 師尊 老大人以前慈悲開示的。"
        )
        en_doc = (
            "The Buddha said, “Although the rain from heaven is abundant, it will not moisten "
            "grasses without roots; although Buddhist dharma is vast, it cannot deliver those without affinity. ” "
            "Today, houxue will be learning together with all virtuous cultivators on the topic "
            "cultivating affinity, cultivating perfection. "
            "The word affinity was something that Shi Zun Lao Da Ren once kindly shared with us."
        )
        bloated = en_doc
        self.assertTrue(is_obviously_wrong(zh, bloated, zh_doc, en_doc))

        idx = self._index([(zh_doc, en_doc)])
        hit = search_vault_translation(zh, idx, allow_llm=False)
        self.assertIsNone(hit)


if __name__ == "__main__":
    unittest.main()
