# -*- coding: utf-8 -*-
"""Tests for the cover-page filename heuristic (whole-file low-content detection)."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.extract import _looks_like_cover_filename


class CoverFilenameTests(unittest.TestCase):
    def test_real_cover_filenames_match(self) -> None:
        self.assertTrue(_looks_like_cover_filename("Cover Page"))
        self.assertTrue(_looks_like_cover_filename("0_Cover"))
        self.assertTrue(_looks_like_cover_filename("titlepage_v2"))
        self.assertTrue(_looks_like_cover_filename("封面_2024"))
        self.assertTrue(_looks_like_cover_filename("封面設計提案"))

    def test_substring_collisions_do_not_match(self) -> None:
        # "cover" glued onto another English word must not false-positive — this was
        # the bug: a 90+ page report titled e.g. "...Coverage..." was wrongly excluded
        # from retrieval entirely because "cover" matched as a bare substring.
        self.assertFalse(_looks_like_cover_filename("Discovery Report"))
        self.assertFalse(_looks_like_cover_filename("Insurance Coverage Policy"))
        self.assertFalse(_looks_like_cover_filename("Annual Recovery Plan"))
        self.assertFalse(_looks_like_cover_filename("Uncovered Assets Report"))
        self.assertFalse(_looks_like_cover_filename("Discoveries and Recoveries 2024"))

    def test_unrelated_filenames_do_not_match(self) -> None:
        self.assertFalse(_looks_like_cover_filename("0 軟化自己學反省"))


if __name__ == "__main__":
    unittest.main()
