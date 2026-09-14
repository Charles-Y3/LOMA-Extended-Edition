# -*- coding: utf-8 -*-
"""Tests for query-framing auto-detection."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.retrieval.framing import is_analysis_style_query


class FramingTests(unittest.TestCase):
    def test_keyword_match_triggers_analysis(self) -> None:
        self.assertTrue(is_analysis_style_query("Please summarize these reports"))
        self.assertTrue(is_analysis_style_query("Compare the two proposals"))

    def test_no_match_defaults_false(self) -> None:
        self.assertFalse(is_analysis_style_query("What is the termination clause?"))

    def test_no_match_respects_default_bias(self) -> None:
        self.assertTrue(
            is_analysis_style_query("What is the termination clause?", default_analysis=True)
        )

    def test_empty_query_respects_default(self) -> None:
        self.assertFalse(is_analysis_style_query(""))
        self.assertTrue(is_analysis_style_query("", default_analysis=True))

    def test_cjk_keyword_match(self) -> None:
        self.assertTrue(is_analysis_style_query("請總結這份報告"))
        self.assertTrue(is_analysis_style_query("请比较这两份文件"))


if __name__ == "__main__":
    unittest.main()
