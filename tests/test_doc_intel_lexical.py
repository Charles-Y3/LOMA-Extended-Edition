# -*- coding: utf-8 -*-
"""Tests for lexical index."""
from __future__ import annotations

import unittest

from extensions.document_intelligence.corpus.types import ChunkRecord
from extensions.document_intelligence.index.lexical import LexicalIndex, branch_matches_chunk


class LexicalTests(unittest.TestCase):
    def _chunks(self) -> list[ChunkRecord]:
        return [
            ChunkRecord(
                chunk_id="a",
                text="BM25 sparse retrieval ranking function",
                source="doc_a",
                vault_path="rag1/a.txt",
                file_path="rag1/a.txt",
                ingest_root=r"C:\data\rag1",
            ),
            ChunkRecord(
                chunk_id="b",
                text="butterfly metamorphosis pupa stage",
                source="doc_b",
                vault_path="rag1/1/b.txt",
                file_path="rag1/1/b.txt",
                ingest_root=r"C:\data\rag1",
            ),
        ]

    def test_search_finds_term(self) -> None:
        idx = LexicalIndex()
        idx.build(self._chunks())
        hits = idx.search("BM25 retrieval", limit=5, score_cutoff=0.0)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.source, "doc_a")

    def test_branch_filter(self) -> None:
        idx = LexicalIndex()
        idx.build(self._chunks())
        hits = idx.search("butterfly", branch="rag1/1", limit=5, score_cutoff=0.0)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].chunk.source, "doc_b")

    def test_branch_matches_chunk(self) -> None:
        ch = self._chunks()[1]
        self.assertTrue(branch_matches_chunk(ch, "rag1/1"))
        self.assertFalse(branch_matches_chunk(ch, "rag2"))

    def test_partial_cjk_substring(self) -> None:
        idx = LexicalIndex()
        idx.build(
            [
                ChunkRecord(
                    chunk_id="c",
                    text="咖啡和紅茶有何不同？這是測試段落。",
                    source="doc",
                    vault_path="a.txt",
                    file_path="a.txt",
                )
            ]
        )
        hits = idx.search("咖啡和紅茶", limit=5, score_cutoff=0.0)
        self.assertTrue(hits)

    def test_partial_rejects_weak_single_term(self) -> None:
        idx = LexicalIndex()
        idx.build(
            [
                ChunkRecord(
                    chunk_id="weak",
                    text="身體不舒服需要休息",
                    source="soft",
                    vault_path="0.docx",
                    file_path="rag1/0.docx",
                ),
                ChunkRecord(
                    chunk_id="strong",
                    text="跑步游泳鍛鍊身體，這是正文。",
                    source="good",
                    vault_path="1.docx",
                    file_path="rag1/1.docx",
                ),
            ]
        )
        hits = idx.search("跑步游泳鍛鍊身體", limit=5, score_cutoff=0.0)
        self.assertTrue(hits)
        self.assertEqual(hits[0].chunk.source, "good")

    def test_weak_char_match_filtered(self) -> None:
        idx = LexicalIndex()
        idx.build(
            [
                ChunkRecord(
                    chunk_id="x",
                    text="早晨運動路線圖只有一個練習相關",
                    source="doc",
                    vault_path="a.docx",
                    file_path="a.docx",
                ),
            ]
        )
        hits = idx.search("學習之目的在充實自己", limit=5, score_cutoff=0.0)
        self.assertFalse(hits)

    def test_file_branch_matches_exact_file(self) -> None:
        ch = ChunkRecord(
            chunk_id="f",
            text="測試文件正文",
            source="1 測試文件",
            vault_path="1 測試文件.docx",
            file_path="rag1/1 測試文件.docx",
            ingest_root=r"C:\data\rag1",
        )
        self.assertTrue(branch_matches_chunk(ch, "rag1/1 測試文件.docx"))
        self.assertFalse(branch_matches_chunk(ch, "rag1/0 其他文件.docx"))


if __name__ == "__main__":
    unittest.main()
