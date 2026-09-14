# -*- coding: utf-8 -*-
"""Tests for agent loop stop conditions."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from extensions.knowledge_vault.agent.loop import run_agent
from extensions.knowledge_vault.corpus.types import ChunkRecord
from extensions.knowledge_vault.corpus.workspace import WorkspaceSession
from extensions.knowledge_vault.index.lexical import LexicalIndex


class AgentLoopTests(unittest.TestCase):
    def _backend(self) -> WorkspaceSession:
        ws = WorkspaceSession()
        ch = ChunkRecord(
            chunk_id="1",
            text="BM25 is a ranking function",
            source="t",
            vault_path="t.txt",
            file_path="t.txt",
        )
        ws.chunks = [ch]
        ws.lexical = LexicalIndex()
        ws.lexical.build([ch])
        return ws

    @patch("extensions.knowledge_vault.agent.loop.run_analyze", return_value="draft answer")
    @patch(
        "extensions.knowledge_vault.agent.loop._review_sufficiency",
        return_value={"sufficient": True, "confidence": 0.9, "follow_up_queries": []},
    )
    def test_stops_when_sufficient(self, _rev, _analyze) -> None:
        out = run_agent(self._backend(), "What is BM25?")
        self.assertIn("draft answer", out)
        self.assertEqual(_analyze.call_count, 1)

    @patch("extensions.knowledge_vault.agent.loop.run_analyze", return_value="draft")
    @patch(
        "extensions.knowledge_vault.agent.loop._review_sufficiency",
        side_effect=[
            {"sufficient": False, "confidence": 0.2, "follow_up_queries": ["BM25 formula"]},
            {"sufficient": True, "confidence": 0.85, "follow_up_queries": []},
        ],
    )
    def test_loops_on_insufficient(self, _rev, _analyze) -> None:
        run_agent(self._backend(), "query")
        self.assertEqual(_analyze.call_count, 2)


if __name__ == "__main__":
    unittest.main()
