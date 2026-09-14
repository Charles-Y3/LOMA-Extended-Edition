# -*- coding: utf-8 -*-
"""Tests for repeating PDF header/footer stripping."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.extract import _strip_repeating_page_furniture


class PageFurnitureTests(unittest.TestCase):
    def test_repeating_header_stripped(self) -> None:
        pages = [
            "正信啟慧班 一\n\n若不納道於此身者，實難改變自己。",
            "正信啟慧班 二\n\n可見軟化自己學反省。",
            "正信啟慧班 三\n\n昔日湖南省，有一座銀山。",
            "正信啟慧班 四\n\n可拿吾，與你繳消。",
        ]
        out = _strip_repeating_page_furniture(pages)
        for text in out:
            self.assertNotIn("正信啟慧班", text)
        # Real content must survive.
        self.assertIn("若不納道於此身者", out[0])
        self.assertIn("可拿吾", out[3])

    def test_no_repetition_left_untouched(self) -> None:
        pages = [
            "Alpha section discusses foo.",
            "Beta section discusses bar.",
            "Gamma section discusses baz.",
        ]
        out = _strip_repeating_page_furniture(pages)
        self.assertEqual(out, pages)

    def test_too_few_pages_left_untouched(self) -> None:
        pages = ["header\ncontent one", "header\ncontent two"]
        out = _strip_repeating_page_furniture(pages)
        self.assertEqual(out, pages)

    def test_no_newlines_within_page_not_wiped(self) -> None:
        # Regression: _collapse_vertical_pdf_text can legitimately return a whole page
        # as ONE line with zero internal newlines (pypdf's raw extraction has no
        # blank-line paragraph markers). A newline-splitting approach here previously
        # treated "the whole blob" as "the first line" and wiped entire pages whenever
        # they started with the header prefix.
        pages = [
            "正信啟慧班 一 若不納道於此身者，實難改變自己。聖佛曰",
            "正信啟慧班 二 可見軟化自己學反省，在我們的人生是一門課題",
            "正信啟慧班 三 昔日湖南省，有一座銀山，山中有一寺",
            "正信啟慧班 四 可拿吾，與你繳消，忽然看見地上現紙一張",
        ]
        out = _strip_repeating_page_furniture(pages)
        for text in out:
            self.assertNotIn("正信啟慧班", text)
            self.assertTrue(text.strip())  # real content must survive, not be wiped

    def test_inconsistent_whitespace_still_covers_all_pages(self) -> None:
        # Regression: alternating single/double space after the header meant the
        # longest exact match only covered half the pages — the shorter, fully-
        # covering match must win instead.
        pages = [
            "正信啟慧班 一 若不納道於此身者，實難改變自己。",
            "正信啟慧班  二 可見軟化自己學反省，在我們的人生。",
            "正信啟慧班 三 昔日湖南省，有一座銀山。",
            "正信啟慧班  四 可拿吾，與你繳消。",
            "正信啟慧班 五 南海古佛慈示。",
        ]
        out = _strip_repeating_page_furniture(pages)
        for text in out:
            self.assertNotIn("正信啟慧班", text)

    def test_minority_repetition_not_stripped(self) -> None:
        # Only appears on 1 of 5 pages — not a real running header.
        pages = [
            "shared line\ncontent a",
            "unique b\ncontent b",
            "unique c\ncontent c",
            "unique d\ncontent d",
            "unique e\ncontent e",
        ]
        out = _strip_repeating_page_furniture(pages)
        self.assertIn("shared line", out[0])


if __name__ == "__main__":
    unittest.main()
