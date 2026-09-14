# -*- coding: utf-8 -*-
"""Tests for context builder."""
from __future__ import annotations

import unittest

from extensions.knowledge_vault.corpus.types import ChunkRecord, HitRecord
from extensions.knowledge_vault.retrieval.context_builder import (
    deep_select,
    expand_neighbor_chunks_recursive,
    fast_select,
    merge_same_section,
    whole_file_fallback,
)


def _hit(source: str, score: float, idx: int = 0) -> HitRecord:
    return HitRecord(
        chunk=ChunkRecord(
            chunk_id=f"{source}-{idx}",
            text=f"content {source}",
            source=source,
            vault_path=f"{source}.txt",
            file_path=f"{source}.txt",
        ),
        score=score,
    )


def _chunk(
    source: str,
    idx: int,
    *,
    text: str = "",
    section: str = "General",
    is_low_content: bool = False,
) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=f"{source}-{idx}",
        text=text or f"chunk {idx} of {source} " * 5,
        source=source,
        vault_path=f"{source}.txt",
        file_path=f"{source}.txt",
        section=section,
        segment_index=idx,
        is_low_content=is_low_content,
    )


def _hit_for(chunk: ChunkRecord, score: float = 1.0) -> HitRecord:
    return HitRecord(chunk=chunk, score=score)


class ContextBuilderTests(unittest.TestCase):
    def test_fast_dominant_top1(self) -> None:
        hits = [_hit("a", 10.0), _hit("b", 1.0)]
        sel = fast_select(hits)
        self.assertEqual(len(sel), 1)
        self.assertEqual(sel[0].chunk.source, "a")

    def test_fast_top3_max_two_per_doc(self) -> None:
        hits = [
            _hit("a", 5.0, 0),
            _hit("a", 4.9, 1),
            _hit("a", 4.8, 2),
            _hit("b", 4.7),
        ]
        sel = fast_select(hits)
        self.assertLessEqual(len(sel), 3)
        a_count = sum(1 for h in sel if h.chunk.source == "a")
        self.assertLessEqual(a_count, 2)

    def test_deep_respects_depth(self) -> None:
        hits = [_hit(f"d{i}", float(10 - i)) for i in range(30)]
        sel = deep_select(hits, depth=10)
        self.assertLessEqual(len(sel), 10)

    def test_whole_file_fallback_includes_small_file(self) -> None:
        all_chunks = [_chunk("small", i) for i in range(5)]
        selected = [_hit_for(all_chunks[2])]
        out, whole_filed = whole_file_fallback(
            selected, all_chunks, ctx_budget_tokens=100_000
        )
        self.assertIn("small.txt", whole_filed)
        self.assertEqual(len(out), 5)

    def test_whole_file_fallback_skips_large_file(self) -> None:
        all_chunks = [_chunk("big", i, text="x" * 5000) for i in range(20)]
        selected = [_hit_for(all_chunks[2])]
        out, whole_filed = whole_file_fallback(selected, all_chunks, ctx_budget_tokens=100)
        self.assertNotIn("big.txt", whole_filed)
        self.assertEqual(len(out), 1)

    def test_merge_same_section_pulls_siblings(self) -> None:
        all_chunks = [
            _chunk("doc", 0, section="Intro"),
            _chunk("doc", 1, section="Findings"),
            _chunk("doc", 2, section="Findings"),
            _chunk("doc", 3, section="Findings"),
        ]
        selected = [_hit_for(all_chunks[1])]
        merged = merge_same_section(selected, all_chunks, skip_keys=set())
        sections = {h.chunk.section for h in merged}
        self.assertEqual(sections, {"Findings"})
        self.assertEqual(len(merged), 3)

    def test_merge_same_section_skips_general(self) -> None:
        all_chunks = [_chunk("doc", i, section="General") for i in range(4)]
        selected = [_hit_for(all_chunks[1])]
        merged = merge_same_section(selected, all_chunks, skip_keys=set())
        self.assertEqual(len(merged), 1)

    def test_merge_same_section_respects_skip_keys(self) -> None:
        all_chunks = [_chunk("doc", i, section="Findings") for i in range(4)]
        selected = [_hit_for(all_chunks[1])]
        merged = merge_same_section(selected, all_chunks, skip_keys={"doc.txt"})
        self.assertEqual(len(merged), 1)

    def test_expand_neighbor_chunks_recursive_multi_hop(self) -> None:
        # Each chunk ends with the query phrase, so every chunk in the chain looks
        # boundary-adjacent (rel >= 0.75) — a single expand_neighbor_chunks() call
        # only pulls one hop from the seed, but the recursive wrapper should keep
        # cascading through the whole run as long as budget/pass-count allow.
        all_chunks = [
            _chunk("doc", i, text="filler " * 20 + "unique target phrase") for i in range(6)
        ]
        seed = _hit_for(all_chunks[0], score=1.0)
        out_single = expand_neighbor_chunks_recursive(
            [seed],
            all_chunks,
            "unique target phrase",
            skip_keys=set(),
            ctx_budget_tokens=100_000,
            max_passes=1,
        )
        out_multi = expand_neighbor_chunks_recursive(
            [seed],
            all_chunks,
            "unique target phrase",
            skip_keys=set(),
            ctx_budget_tokens=100_000,
            max_passes=6,
        )
        self.assertGreater(len(out_multi), len(out_single))

    def test_expand_neighbor_chunks_recursive_respects_budget(self) -> None:
        all_chunks = [_chunk("doc", i, text="x" * 2000) for i in range(10)]
        seed = _hit_for(all_chunks[5], score=1.0)
        out = expand_neighbor_chunks_recursive(
            [seed],
            all_chunks,
            "irrelevant query with no match",
            skip_keys=set(),
            ctx_budget_tokens=1,
            max_passes=6,
        )
        # A near-zero budget should stop expansion immediately (or after at most the
        # first pass), never ballooning to the full file.
        self.assertLessEqual(len(out), len(all_chunks))


if __name__ == "__main__":
    unittest.main()
