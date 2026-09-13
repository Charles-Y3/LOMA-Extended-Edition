# -*- coding: utf-8 -*-
"""Tests for the shared fully-excluded-file helper (tree icon + stats count)."""
from __future__ import annotations

import unittest

from extensions.document_intelligence.corpus.types import ChunkRecord, fully_excluded_file_keys


def _chunk(source: str, idx: int, *, is_low_content: bool) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"{source}-{idx}",
        text=f"chunk {idx}",
        source=source,
        vault_path=f"{source}.pdf",
        file_path=f"{source}.pdf",
        is_low_content=is_low_content,
    )


class FullyExcludedFileKeysTests(unittest.TestCase):
    def test_file_with_one_excluded_chunk_of_many_not_counted(self) -> None:
        # This is the exact bug reported: "10648 segments · 94 (3 excluded) files"
        # counted a file as excluded because it had ONE low-content chunk among many.
        chunks = [_chunk("doc", i, is_low_content=(i == 0)) for i in range(285)]
        keys = fully_excluded_file_keys(chunks, key_fn=lambda ch: ch.file_path)
        self.assertNotIn("doc.pdf", keys)

    def test_genuinely_fully_excluded_file_counted(self) -> None:
        chunks = [_chunk("cover", 0, is_low_content=True)]
        keys = fully_excluded_file_keys(chunks, key_fn=lambda ch: ch.file_path)
        self.assertIn("cover.pdf", keys)

    def test_mixed_corpus_counts_only_fully_excluded(self) -> None:
        chunks = (
            [_chunk("cover", 0, is_low_content=True)]
            + [_chunk("partial", i, is_low_content=(i < 3)) for i in range(50)]
            + [_chunk("clean", i, is_low_content=False) for i in range(10)]
        )
        keys = fully_excluded_file_keys(chunks, key_fn=lambda ch: ch.file_path)
        self.assertEqual(keys, {"cover.pdf"})


if __name__ == "__main__":
    unittest.main()
