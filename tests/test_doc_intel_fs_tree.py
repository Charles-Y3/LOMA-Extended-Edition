# -*- coding: utf-8 -*-
"""Tests for the indexed-tree file exclusion flag."""
from __future__ import annotations

import unittest

from extensions.document_intelligence.corpus.types import ChunkRecord
from extensions.document_intelligence.ui.fs_tree import build_index_tree


def _chunk(source: str, idx: int, *, is_low_content: bool) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"{source}-{idx}",
        text=f"chunk {idx}",
        source=source,
        vault_path=f"{source}.pdf",
        file_path=f"{source}.pdf",
        ingest_root="root",
        segment_index=idx,
        is_low_content=is_low_content,
    )


class FsTreeFlagTests(unittest.TestCase):
    def test_partially_excluded_file_not_flagged(self) -> None:
        # 4 of 285 chunks low-content (a stray header/footer fragment) — the file as a
        # whole must NOT show up as excluded; only a true whole-file cover-page case
        # (every chunk low-content) should.
        chunks = [_chunk("doc", i, is_low_content=(i < 4)) for i in range(285)]
        tree = build_index_tree(chunks)
        flags = tree["root"].get("__file_flags__", {})
        self.assertNotIn("doc.pdf", flags)

    def test_fully_excluded_file_flagged(self) -> None:
        chunks = [_chunk("cover", 0, is_low_content=True)]
        tree = build_index_tree(chunks)
        flags = tree["root"]["__file_flags__"]
        self.assertIn("cover.pdf", flags)

    def test_no_low_content_no_flag(self) -> None:
        chunks = [_chunk("doc", i, is_low_content=False) for i in range(5)]
        tree = build_index_tree(chunks)
        flags = tree["root"].get("__file_flags__", {})
        self.assertNotIn("doc.pdf", flags)


if __name__ == "__main__":
    unittest.main()
