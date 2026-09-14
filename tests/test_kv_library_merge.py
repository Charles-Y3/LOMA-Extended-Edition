# -*- coding: utf-8 -*-
"""Library root merge tests."""
from __future__ import annotations

import os
import tempfile
import unittest

from extensions.knowledge_vault.corpus.library import merge_library_roots


class LibraryRootTests(unittest.TestCase):
    def test_parent_absorbs_child(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            parent = os.path.join(base, "rag1")
            child = os.path.join(parent, "1")
            os.makedirs(child)
            roots = merge_library_roots([child], parent)
            self.assertEqual(len(roots), 1)
            self.assertEqual(os.path.normcase(roots[0]), os.path.normcase(parent))

    def test_child_ignored_when_parent_exists(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            parent = os.path.join(base, "rag1")
            child = os.path.join(parent, "1")
            os.makedirs(child)
            roots = merge_library_roots([parent], child)
            self.assertEqual(len(roots), 1)
            self.assertEqual(os.path.normcase(roots[0]), os.path.normcase(parent))

    def test_siblings_kept(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            a = os.path.join(base, "rag1")
            b = os.path.join(base, "rag2")
            os.makedirs(a)
            os.makedirs(b)
            roots = merge_library_roots([a], b)
            self.assertEqual(len(roots), 2)


if __name__ == "__main__":
    unittest.main()
