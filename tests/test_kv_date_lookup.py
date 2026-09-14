# -*- coding: utf-8 -*-
"""Tests for deterministic cover-page date lookup."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.corpus.types import ChunkRecord
from extensions.knowledge_vault.retrieval.date_lookup import (
    extract_dates,
    find_cover_page_dates,
    is_date_query,
)


def _chunk(source: str, text: str, *, is_low_content: bool, vault_path: str = "") -> ChunkRecord:
    return ChunkRecord(
        chunk_id=source,
        text=text,
        source=source,
        vault_path=vault_path or f"{source}.pdf",
        file_path=f"{source}.pdf",
        is_low_content=is_low_content,
    )


class DateQueryIntentTests(unittest.TestCase):
    def test_detects_date_intent(self) -> None:
        self.assertTrue(is_date_query("When was this event held?"))
        self.assertTrue(is_date_query("這份文件的日期是什麼？"))

    def test_rejects_unrelated_query(self) -> None:
        self.assertFalse(is_date_query("Summarize the key points"))
        self.assertFalse(is_date_query(""))


class ExtractDatesTests(unittest.TestCase):
    def test_gregorian_range_with_stray_spacing(self) -> None:
        # Real example from a PDF with irregular inter-character spacing.
        text = "天 人交流籌備會 前 人 慈 語 公元二○ 一一 年一月二十三日至二 月二 日 歲 次 庚 寅 十 二 月 二 十 日 至 "
        dates = extract_dates(text)
        self.assertIn("公元二○一一年一月二十三日至二月二日", dates)

    def test_no_date_returns_empty(self) -> None:
        self.assertEqual(extract_dates("this text has no date in it"), [])

    def test_iso_range(self) -> None:
        self.assertIn("2024-01-01to2024-01-31", extract_dates("Period: 2024-01-01 to 2024-01-31."))


class FindCoverPageDatesTests(unittest.TestCase):
    def test_only_low_content_chunks_considered(self) -> None:
        chunks = [
            _chunk("cover", "公元二○一一年一月二十三日", is_low_content=True),
            _chunk("body", "公元二○二○年五月五日", is_low_content=False),
        ]
        matches = find_cover_page_dates(chunks)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0].source, "cover")

    def test_no_date_in_low_content_chunk_excluded(self) -> None:
        chunks = [_chunk("header", "正信啟慧班", is_low_content=True)]
        self.assertEqual(find_cover_page_dates(chunks), [])

    def test_branch_filter_applied(self) -> None:
        chunks = [
            _chunk("a", "公元二○一一年一月一日", is_low_content=True, vault_path="folderA/a.pdf"),
            _chunk("b", "公元二○一二年二月二日", is_low_content=True, vault_path="folderB/b.pdf"),
        ]
        for ch in chunks:
            ch.file_path = ch.vault_path
        matches = find_cover_page_dates(chunks, branch="folderA")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0][0].source, "a")


if __name__ == "__main__":
    unittest.main()
