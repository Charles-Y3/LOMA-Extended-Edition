# -*- coding: utf-8 -*-
"""Tests for Document Intelligence password prompts."""
from __future__ import annotations

import threading
import unittest

from extensions.document_intelligence import passwords as pw
from extensions.document_intelligence.translation import (
    TranslationIndex,
    TranslationPair,
    global_translation_index,
    match_translation,
)


class TestDocumentIntelligencePasswords(unittest.TestCase):
    def setUp(self) -> None:
        with pw._lock:
            pw._pending = None
        pw._active_dialog = False

    def test_password_attempts_include_empty_first(self) -> None:
        attempts = pw.password_attempts("/tmp/file.pdf", "secret")
        self.assertEqual(attempts[0], "")
        self.assertIn("secret", attempts)

    def test_pending_request_from_worker_thread(self) -> None:
        seen: dict[str, str | None] = {"basename": None}

        def worker() -> None:
            with pw._lock:
                pw._pending = pw._PasswordRequest(
                    path="/tmp/encrypted.pdf",
                    basename="encrypted.pdf",
                )
            seen["basename"] = pw.pending_password_basename()
            if pw._pending:
                pw._pending.result = "test-pass"
                pw._pending.event.set()

        t = threading.Thread(target=worker)
        t.start()
        t.join(timeout=2)
        self.assertEqual(seen["basename"], "encrypted.pdf")

    def test_poll_without_ui_leaves_shown_false(self) -> None:
        req = pw._PasswordRequest(path="/tmp/x.pdf", basename="x.pdf")
        with pw._lock:
            pw._pending = req
        orig = pw._schedule_on_main
        try:
            pw._schedule_on_main = lambda _cb: None
            pw.poll_password_dialog()
            self.assertFalse(req.shown)
        finally:
            pw._schedule_on_main = orig


class TestTranslationVaultMatch(unittest.TestCase):
    def test_exact_match(self) -> None:
        index = TranslationIndex()
        index.build(
            [
                TranslationPair(
                    source_text="人生真諦",
                    target_text="The True Meaning of Life",
                )
            ]
        )
        hit = match_translation(index, "人生真諦")
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.target_text, "The True Meaning of Life")

    def test_global_index_is_translation_index(self) -> None:
        index = global_translation_index()
        self.assertIsInstance(index, TranslationIndex)


if __name__ == "__main__":
    unittest.main()
