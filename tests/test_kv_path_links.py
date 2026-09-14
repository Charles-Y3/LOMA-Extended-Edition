# -*- coding: utf-8 -*-
"""Tests for path links."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.ui.chat_paths import split_content_with_path_blocks
from extensions.knowledge_vault.ui.path_links import (
    format_file_path_links,
    loma_open_link,
)


class PathLinkTests(unittest.TestCase):
    def test_loma_open_markdown(self) -> None:
        link = loma_open_link("test.docx", r"C:\data\test.docx")
        self.assertTrue(link.startswith("[test.docx](loma-open:"))

    def test_two_line_layout(self) -> None:
        out = format_file_path_links("OneDrive/Desktop/rag1/file.docx")
        self.assertIn("folder location:", out)
        self.assertIn("file:", out)
        self.assertIn("loma-open:", out)
        self.assertEqual(out.count("loma-open:"), 2)
        self.assertIn("\n", out)
        self.assertNotIn("\n\n", out)


class SplitContentWithPathBlocksTests(unittest.TestCase):
    def test_each_block_stays_next_to_its_own_result(self) -> None:
        block1 = format_file_path_links("OneDrive/Desktop/rag1/a.docx")
        block2 = format_file_path_links("OneDrive/Desktop/rag1/b.docx")
        raw = (
            f"**1.** a.docx · score 1.0\n\n{block1}\n\nsnippet one\n\n"
            f"**2.** b.docx · score 0.5\n\n{block2}\n\nsnippet two"
        )
        parts = split_content_with_path_blocks(raw)
        kinds = [("block" if isinstance(p, dict) else "text") for p in parts]
        self.assertEqual(kinds, ["text", "block", "text", "block", "text"])
        # Block 1 must land between the two text runs that mention "a.docx"/"snippet one" —
        # i.e. right after result 1, not shifted to the end alongside block 2.
        self.assertIn("a.docx", parts[0])
        self.assertEqual(parts[1]["file_label"], "a.docx")
        self.assertIn("snippet one", parts[2])
        self.assertIn("b.docx", parts[2])
        self.assertEqual(parts[3]["file_label"], "b.docx")
        self.assertIn("snippet two", parts[4])


if __name__ == "__main__":
    unittest.main()
