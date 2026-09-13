# -*- coding: utf-8 -*-
"""Translation run diagnostics for the Formslator Translate tab output."""
from __future__ import annotations

import time
from typing import Callable

LogFn = Callable[[str], None]


class TranslateDiag:
    def __init__(self, log_fn: LogFn | None = None) -> None:
        self._log = log_fn or (lambda _m: None)
        self.batch_calls = 0
        self.single_calls = 0
        self.batch_paragraphs = 0
        self.single_paragraphs = 0
        self.batch_misses = 0
        self.batch_seconds = 0.0
        self.single_seconds = 0.0
        self._batch_t0 = 0.0
        self._single_t0 = 0.0
        self._call_idx = 0

    def log(self, msg: str) -> None:
        self._log(msg)

    def batch_start(self, *, count: int, source_chars: int, indices: list[int]) -> None:
        self._call_idx += 1
        self._batch_t0 = time.time()
        idx_preview = ",".join(str(i) for i in indices[:6])
        if len(indices) > 6:
            idx_preview += f",…+{len(indices) - 6}"
        self.log(
            f"▶ LLM batch #{self._call_idx}: {count} paragraph(s), "
            f"{source_chars:,} source chars [rows {idx_preview}]"
        )

    def batch_end(
        self,
        *,
        parsed: int,
        valid: int,
        expected: int,
        attempts: int,
        note: str = "",
    ) -> None:
        elapsed = time.time() - self._batch_t0
        self.batch_calls += 1
        self.batch_paragraphs += expected
        self.batch_seconds += elapsed
        miss = max(0, expected - valid)
        self.batch_misses += miss
        status = "OK" if valid == expected and parsed == expected else "PARTIAL"
        extra = f" — {note}" if note else ""
        self.log(
            f"◀ Batch #{self._call_idx} {status} in {elapsed:.1f}s "
            f"(parsed {parsed}/{expected}, accepted {valid}/{expected}, "
            f"attempts {attempts}){extra}"
        )

    def single_start(self, *, idx: int, source_chars: int) -> None:
        self._call_idx += 1
        self._single_t0 = time.time()
        self.log(f"▶ LLM single #{self._call_idx}: row {idx}, {source_chars:,} source chars")

    def single_end(self, *, ok: bool) -> None:
        elapsed = time.time() - self._single_t0
        self.single_calls += 1
        self.single_paragraphs += 1
        self.single_seconds += elapsed
        self.log(f"◀ Single #{self._call_idx} done in {elapsed:.1f}s ({'ok' if ok else 'fallback'})")

    def summary(self, *, total_nonempty: int, total_seconds: float) -> None:
        llm_calls = self.batch_calls + self.single_calls
        self.log("--------------------------------------------------")
        self.log("Translation diagnostics")
        self.log(f"  LLM calls: {llm_calls} ({self.batch_calls} batch, {self.single_calls} single)")
        self.log(
            f"  Paragraphs: {total_nonempty} nonempty "
            f"({self.batch_paragraphs} via batch, {self.single_paragraphs} via single)"
        )
        if self.batch_misses:
            self.log(f"  Batch fallbacks (miss): {self.batch_misses}")
        if self.batch_calls:
            avg_b = self.batch_seconds / self.batch_calls
            self.log(
                f"  Batch time: {self.batch_seconds:.1f}s total "
                f"({avg_b:.1f}s avg per batch call)"
            )
        if self.single_calls:
            avg_s = self.single_seconds / self.single_calls
            self.log(
                f"  Single time: {self.single_seconds:.1f}s total "
                f"({avg_s:.1f}s avg per single call)"
            )
        self.log(f"  Wall time: {total_seconds:.1f}s")
        if llm_calls and total_seconds > 0:
            self.log(f"  Avg wall per LLM call: {total_seconds / llm_calls:.1f}s")
        self.log("--------------------------------------------------")
