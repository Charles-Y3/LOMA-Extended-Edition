# -*- coding: utf-8 -*-
"""Tests for Formslator style mapping persistence."""
from __future__ import annotations

import os
import tempfile
import unittest

from services.formslator.mapping_service import load_mapping_for_file, save_mapping
from services.formslator.paths import MAPPING_DIR, ensure_dirs


class TestFormslatorMapping(unittest.TestCase):
    def setUp(self) -> None:
        ensure_dirs()
        self._tmpdir = tempfile.mkdtemp()
        self._orig_mapping_dir = MAPPING_DIR
        import services.formslator.mapping_service as ms
        import services.formslator.paths as paths

        self._ms = ms
        self._paths = paths
        ms.MAPPING_DIR = self._tmpdir
        paths.MAPPING_DIR = self._tmpdir

    def tearDown(self) -> None:
        import shutil

        self._ms.MAPPING_DIR = self._orig_mapping_dir
        self._paths.MAPPING_DIR = self._orig_mapping_dir
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_save_and_reload(self) -> None:
        mapping = {
            "NoSpacing|?|18": ("C3a 內文（仿宋）", "E3ab Plain Text"),
            "NoSpacing|?|16": ("C2a 標題", "E2a Heading"),
        }
        save_mapping("sample_doc", "default_template_v2.docx", mapping)
        loaded, tpl, meta = load_mapping_for_file(
            os.path.join("data", "formslator", "uploads", "sample_doc.docx")
        )
        self.assertEqual(tpl, "default_template_v2.docx")
        self.assertEqual(loaded, mapping)
        self.assertEqual(meta.get("original_column"), "left")

    def test_update_overwrites_same_template_file(self) -> None:
        save_mapping(
            "sample_doc",
            "default_template_v2.docx",
            {"A|?|12": ("C1", "E1")},
        )
        path = save_mapping(
            "sample_doc",
            "default_template_v2.docx",
            {"A|?|12": ("C3a 內文（仿宋）", "E3ab Plain Text")},
        )
        with open(path, encoding="utf-8") as f:
            body = f.read()
        self.assertIn("C3a 內文（仿宋）", body)
        self.assertNotIn("|||C1|||", body)


if __name__ == "__main__":
    unittest.main()
