# -*- coding: utf-8 -*-
"""Tests for chat OS-open link rendering."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.ui.path_links import format_file_path_links
from ui.components.chat_message import _legacy_loma_open_to_html


class ChatOsLinkTests(unittest.TestCase):
    def test_html_preserves_data_path_for_unsanitized_markdown(self) -> None:
        raw = format_file_path_links("OneDrive/Desktop/rag1/test.docx")
        html = _legacy_loma_open_to_html(raw)
        self.assertIn('class="loma-os-open"', html)
        self.assertIn("data-path=", html)
        self.assertIn("folder location:", html)
        self.assertIn("\nfile:", raw)


if __name__ == "__main__":
    unittest.main()
