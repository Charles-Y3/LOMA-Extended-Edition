# -*- coding: utf-8 -*-
"""Tests for document chunking, search, and context selection."""
from __future__ import annotations

import unittest

from pipeline.context_retrieval import select_workspace_context
from services.context_selector import infer_selection_mode
from services.document_chunker import SourceDocument, chunk_document
from services.text_search import rank_chunks, tokenize_query


class TestDocumentChunker(unittest.TestCase):
    def test_small_doc_single_chunk(self) -> None:
        doc = SourceDocument(name="a.txt", text="Hello world.")
        chunks = chunk_document(doc, max_chars=500)
        self.assertEqual(len(chunks), 1)
        self.assertIn("Hello", chunks[0].text)

    def test_long_doc_multiple_chunks(self) -> None:
        body = "Paragraph one.\n\n" + ("word " * 400) + "\n\nParagraph two."
        doc = SourceDocument(name="big.txt", text=body)
        chunks = chunk_document(doc, max_chars=200, overlap=20)
        self.assertGreater(len(chunks), 1)
        self.assertEqual(chunks[0].source, "big.txt")


class TestTextSearch(unittest.TestCase):
    def test_scores_matching_terms_higher(self) -> None:
        from services.types import TextChunk

        a = TextChunk("a:0", "f", "document", "revenue grew in Q3", 0)
        b = TextChunk("b:0", "f", "document", "unrelated weather report", 0)
        ranked = rank_chunks([a, b], "Q3 revenue")
        self.assertGreater(ranked[0][1], ranked[1][1])
        self.assertEqual(ranked[0][0].chunk_id, "a:0")

    def test_tokenize_drops_stopwords(self) -> None:
        self.assertIn("revenue", tokenize_query("what is the revenue"))
        self.assertNotIn("the", tokenize_query("what is the revenue"))


class TestContextSelector(unittest.TestCase):
    def test_passthrough_when_small(self) -> None:
        sources = [SourceDocument(name="x.txt", text="Short note.")]
        result = select_workspace_context(sources, "summarize this", char_budget=10_000)
        self.assertFalse(result.was_truncated)
        self.assertIn("Short note", result.text)

    def test_truncates_when_large(self) -> None:
        long_a = "alpha keyword " * 800
        long_b = "beta other " * 800
        sources = [
            SourceDocument(name="a.txt", text=long_a),
            SourceDocument(name="b.txt", text=long_b),
        ]
        result = select_workspace_context(
            sources,
            "find alpha keyword details",
            char_budget=3_000,
            mode="qa",
        )
        self.assertTrue(result.was_truncated)
        self.assertIn("PASSAGE", result.text)
        self.assertLess(len(result.text), len(long_a) + len(long_b))

    def test_infer_summarize_mode(self) -> None:
        self.assertEqual(infer_selection_mode("please summarize the report"), "summarize")


if __name__ == "__main__":
    unittest.main()
