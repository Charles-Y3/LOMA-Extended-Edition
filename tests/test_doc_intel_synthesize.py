# -*- coding: utf-8 -*-
"""Tests for citation page-range labeling."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from extensions.document_intelligence.corpus.types import ChunkRecord, HitRecord, TaskKind
from extensions.document_intelligence.retrieval.synthesize import _page_label, synthesize


def _hit(page: int) -> HitRecord:
    return HitRecord(
        chunk=ChunkRecord(
            chunk_id=f"c{page}",
            text="x",
            source="doc",
            vault_path="doc.pdf",
            file_path="doc.pdf",
            page_number=page,
        ),
        score=1.0,
    )


class PageLabelTests(unittest.TestCase):
    def test_single_page(self) -> None:
        self.assertEqual(_page_label([_hit(5)]), "5")

    def test_contiguous_range_uses_dash(self) -> None:
        group = [_hit(p) for p in range(24, 84)]  # 24..83, contiguous
        self.assertEqual(_page_label(group), "24–83")

    def test_sparse_pages_listed_not_ranged(self) -> None:
        group = [_hit(24), _hit(45), _hit(83)]
        label = _page_label(group)
        self.assertEqual(label, "24, 45, 83")
        self.assertNotIn("–", label)

    def test_sparse_pages_capped(self) -> None:
        group = [_hit(p) for p in (1, 5, 10, 15, 20, 25, 30, 35)]
        label = _page_label(group)
        self.assertIn("more", label)

    def test_no_page_numbers(self) -> None:
        ch = ChunkRecord(
            chunk_id="c", text="x", source="doc", vault_path="doc.txt", file_path="doc.txt"
        )
        self.assertEqual(_page_label([HitRecord(chunk=ch, score=1.0)]), "—")


def _file_hit(name: str) -> HitRecord:
    return HitRecord(
        chunk=ChunkRecord(
            chunk_id=name, text=f"content {name}", source=name,
            vault_path=f"{name}.pdf", file_path=f"{name}.pdf",
        ),
        score=1.0,
    )


class CitationRenumberingTests(unittest.TestCase):
    @patch("extensions.document_intelligence.retrieval.synthesize.ludicity_chat")
    def test_gap_left_by_uncited_source_is_closed(self, mock_chat) -> None:
        # Model cites [1] and [3] but never [2] — the Sources list must not show a
        # gap ([1], [3] with nothing labeled [2]), and the body's own "[3]" marker
        # must be rewritten to match wherever the renumbered list puts that source.
        mock_chat.return_value = "First fact [1]. Third fact [3]."
        hits = [_file_hit("a"), _file_hit("b"), _file_hit("c")]
        out = synthesize("query", hits, task=TaskKind.ASK, citation_required=True)
        self.assertNotIn("[3]", out)  # original numbering must not survive anywhere
        self.assertIn("[2]", out)  # renumbered sequentially: a=[1], c=[2]
        self.assertIn("**[1]**", out)
        self.assertIn("**[2]**", out)
        self.assertNotIn("**[3]**", out)
        # The renumbered body citation and the Sources entry must refer to the same
        # source (c), not just both exist somewhere in the text.
        self.assertIn("Third fact [2]", out)

    @patch("extensions.document_intelligence.retrieval.synthesize.ludicity_chat")
    def test_no_gap_when_all_cited(self, mock_chat) -> None:
        mock_chat.return_value = "One [1]. Two [2]."
        hits = [_file_hit("a"), _file_hit("b")]
        out = synthesize("query", hits, task=TaskKind.ASK, citation_required=True)
        self.assertIn("**[1]**", out)
        self.assertIn("**[2]**", out)


if __name__ == "__main__":
    unittest.main()
