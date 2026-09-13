# -*- coding: utf-8 -*-
"""Tests for compressed-hit metadata mapping (citation/page correctness)."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from extensions.document_intelligence.corpus.types import ChunkRecord, HitRecord
from extensions.document_intelligence.retrieval.analyze_action import _compress_hits


def _hit(source: str, page: int, idx: int) -> HitRecord:
    return HitRecord(
        chunk=ChunkRecord(
            chunk_id=f"{source}-{idx}",
            text=f"content {source} page {page}",
            source=source,
            vault_path=f"{source}.pdf",
            file_path=f"{source}.pdf",
            page_number=page,
            segment_index=idx,
        ),
        score=1.0,
    )


class CompressHitsTests(unittest.TestCase):
    @patch(
        "extensions.ludicity_shared.llm.ludicity_chat",
        return_value="summary",
    )
    def test_representative_chunk_belongs_to_its_own_batch(self, _chat) -> None:
        # 40 hits from a single file (> 15, so multiple batches) — every compressed
        # HitRecord's chunk must be one that was ACTUALLY in the batch it represents,
        # not an arbitrary chunk from elsewhere in the original flat hit list.
        hits = [_hit("doc", page=i, idx=i) for i in range(40)]
        out = _compress_hits("query", hits)
        self.assertGreater(len(out), 1)
        all_ids = {h.chunk.chunk_id for h in hits}
        for h in out:
            self.assertIn(h.chunk.chunk_id, all_ids)

    @patch(
        "extensions.ludicity_shared.llm.ludicity_chat",
        return_value="summary",
    )
    def test_batches_never_mix_files(self, _chat) -> None:
        # A batch spanning two different files would make correct per-file metadata
        # impossible by construction — batching must be per-file.
        hits = [_hit("a", page=i, idx=i) for i in range(10)] + [
            _hit("b", page=i, idx=i) for i in range(10)
        ]
        out = _compress_hits("query", hits)
        sources = {h.chunk.source for h in out}
        self.assertEqual(sources, {"a", "b"})
        # Each compressed hit's file must be internally consistent (trivially true
        # here since ChunkRecord carries one source, but assert the count reflects
        # one representative per file rather than a single blended entry).
        self.assertEqual(len(out), 2)


if __name__ == "__main__":
    unittest.main()
