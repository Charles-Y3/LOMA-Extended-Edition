# -*- coding: utf-8 -*-
"""Tests for search formatting."""
from __future__ import annotations

import unittest

from extensions.document_intelligence.corpus.types import ChunkRecord
from extensions.document_intelligence.index.lexical import LexicalIndex
from extensions.document_intelligence.retrieval.search_format import (
    highlight_terms,
    phrase_match_coverage,
)


def _build_index(*texts: str) -> LexicalIndex:
    idx = LexicalIndex()
    idx.build(
        [
            ChunkRecord(chunk_id=str(i), text=t, source=f"s{i}", vault_path=f"s{i}", file_path=f"s{i}")
            for i, t in enumerate(texts)
        ]
    )
    return idx


class SearchFormatTests(unittest.TestCase):
    def test_longest_phrase_coverage(self) -> None:
        text = "前言 學習之目的在充實自己 結尾"
        cov = phrase_match_coverage("學習之目的在充實自己", text)
        self.assertEqual(cov, 1.0)

    def test_highlight_prefers_longest_phrase(self) -> None:
        text = "學習之目的在充實自己與自己"
        out = highlight_terms(text, "學習之目的在充實自己")
        self.assertEqual(out.count("<mark"), 1)
        self.assertIn("學習之目的在充實自己", out)
        self.assertNotIn(">自己<", out)

    def test_english_highlight_matches_whole_words_not_letter_fragments(self) -> None:
        # Regression: raw character-substring matching used to light up stray
        # single letters ("s", "t", "T") anywhere a 2-char query fragment like
        # "is"/"at"/"ao" happened to appear, instead of the actual phrase.
        query = "what is tao cultivation"
        text = "The spirit of Tao cultivation of both Saint Yan Hui lives on."
        out = highlight_terms(text, query)
        self.assertIn('<mark class="loma-search-hit">Tao cultivation</mark>', out)
        self.assertNotIn(">s<", out)
        self.assertNotIn(">t<", out)
        self.assertNotIn(">T<", out)

    def test_english_word_boundary_not_substring(self) -> None:
        # "is" must match only the standalone word, not the "is" inside "this".
        cov = phrase_match_coverage("is", "please visit this location soon.")
        self.assertEqual(cov, 0.0)
        out = highlight_terms("please visit this location soon.", "is")
        self.assertNotIn("<mark", out)

    def test_standalone_common_words_not_highlighted(self) -> None:
        # A lone "and"/"its" hit is not a meaningful signal — only content
        # words should light up, even though they're technically whole query
        # words too. Significance now comes from the corpus's own BM25 IDF
        # (no hardcoded English stopword list): "and"/"its"/"the" appear in
        # nearly every chunk of this small corpus, so they score low IDF the
        # same way any language's common words would in a real corpus.
        corpus_texts = [
            "In the process of cultivating Tao, one must deeply understand "
            "its true meaning. Infinite wealth and status are within everyone's "
            "true nature.",
            "The teacher explained that patience and diligence are needed, "
            "and its rewards come slowly to those who wait for them.",
            "A good life requires balance, and its foundation is honesty, "
            "and the courage to face the truth.",
        ]
        idx = _build_index(*corpus_texts)
        query = "tao cultivation and its importance"
        out = highlight_terms(corpus_texts[0], query, significant=idx.term_is_significant)
        self.assertIn('<mark class="loma-search-hit">Tao</mark>', out)
        self.assertNotIn('<mark class="loma-search-hit">its</mark>', out)
        self.assertNotIn('<mark class="loma-search-hit">and</mark>', out)

    def test_significance_is_corpus_driven_not_a_word_list(self) -> None:
        # Same mechanism, different language — "и" ("and" in Russian) is
        # common in this tiny Russian corpus and should be suppressed
        # standalone, while the distinctive word remains highlighted. Proves
        # the fix isn't an English-only stopword list.
        corpus_texts = [
            "Кот и собака жили вместе и дружили много лет.",
            "Солнце и небо были ясными, и птицы пели с утра.",
            "Она читала книгу и пила чай, и было очень тихо.",
        ]
        idx = _build_index(*corpus_texts)
        out = highlight_terms(corpus_texts[0], "кот и собака", significant=idx.term_is_significant)
        self.assertIn("<mark", out)
        self.assertNotIn('<mark class="loma-search-hit">и</mark>', out)


if __name__ == "__main__":
    unittest.main()
