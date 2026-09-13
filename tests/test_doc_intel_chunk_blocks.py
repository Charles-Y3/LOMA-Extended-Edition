# -*- coding: utf-8 -*-
"""Tests for oversized-block splitting during chunking."""
from __future__ import annotations

import unittest

from extensions.document_intelligence.extract import _chunk_blocks, _encoding, _token_count

_ENC = _encoding()


class ChunkBlocksTests(unittest.TestCase):
    def test_oversized_cjk_block_no_tiny_fragments(self) -> None:
        # Regression: irregular inter-character spacing (common in some PDF text
        # extractions) meant whitespace-based word splitting produced near-single-
        # character "words", and dividing into exact thirds by word count left
        # remainder pieces as small as one leftover word/character.
        sentence = "這 是一句 測試句子，用來 模擬不規則間距的中文段落內容。"
        big_text = sentence * 40
        blocks = [{"text": big_text, "section": "General", "page_number": 5}]
        chunks = _chunk_blocks(blocks, _ENC, max_tokens=320, min_tokens=20)
        self.assertGreater(len(chunks), 1)
        for ch in chunks:
            self.assertGreaterEqual(_token_count(ch["text"], _ENC), 20)

    def test_oversized_block_splits_at_sentence_boundaries(self) -> None:
        sentences = [f"這是第{i}句話，內容各不相同。" for i in range(60)]
        blocks = [{"text": "".join(sentences), "section": "General", "page_number": 1}]
        chunks = _chunk_blocks(blocks, _ENC, max_tokens=50, min_tokens=5)
        self.assertGreater(len(chunks), 1)
        # No chunk should end mid-sentence (i.e. not on a sentence-ending punctuation
        # or the very end of the source text).
        for ch in chunks[:-1]:
            self.assertTrue(ch["text"].rstrip().endswith("。"))

    def test_single_oversized_sentence_falls_back_to_char_split(self) -> None:
        # No punctuation at all to break at.
        text = "字" * 2000
        blocks = [{"text": text, "section": "General", "page_number": 1}]
        chunks = _chunk_blocks(blocks, _ENC, max_tokens=320, min_tokens=20)
        self.assertGreater(len(chunks), 1)
        for ch in chunks:
            self.assertGreaterEqual(_token_count(ch["text"], _ENC), 20)

    def test_normal_sized_blocks_unaffected(self) -> None:
        blocks = [
            {"text": "First paragraph.", "section": "General", "page_number": 1},
            {"text": "Second paragraph.", "section": "General", "page_number": 1},
        ]
        chunks = _chunk_blocks(blocks, _ENC, max_tokens=320, min_tokens=1)
        self.assertEqual(len(chunks), 1)
        self.assertIn("First paragraph.", chunks[0]["text"])
        self.assertIn("Second paragraph.", chunks[0]["text"])


if __name__ == "__main__":
    unittest.main()
