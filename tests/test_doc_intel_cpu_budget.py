# -*- coding: utf-8 -*-
"""Tests for the CPU-only context ceiling."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from extensions.document_intelligence.retrieval import cpu_budget


class _FakeProfile:
    def __init__(self, gpu_backend: str) -> None:
        self.gpu_backend = gpu_backend


class CpuBudgetTests(unittest.TestCase):
    @patch("services.system.profiler.get_system_profile", return_value=_FakeProfile("cuda"))
    def test_gpu_machine_not_cpu_only(self, _prof) -> None:
        self.assertFalse(cpu_budget.is_cpu_only())

    @patch("services.system.profiler.get_system_profile", return_value=_FakeProfile("cpu"))
    def test_cpu_machine_is_cpu_only(self, _prof) -> None:
        self.assertTrue(cpu_budget.is_cpu_only())

    @patch("services.system.profiler.get_system_profile", return_value=_FakeProfile("cuda"))
    @patch(
        "pipeline.direct.batch_budget.resolve_batch_budget",
        return_value={"num_ctx_raw": 32768, "num_ctx": 8192},
    )
    def test_gpu_uses_uncapped_raw_ceiling(self, _budget, _prof) -> None:
        self.assertEqual(cpu_budget.effective_ctx_ceiling_tokens("m"), 32768)

    @patch("services.system.profiler.get_system_profile", return_value=_FakeProfile("cpu"))
    @patch(
        "pipeline.direct.batch_budget.resolve_batch_budget",
        return_value={"num_ctx_raw": 32768, "num_ctx": 8192},
    )
    def test_cpu_only_caps_regardless_of_raw_tier(self, _budget, _prof) -> None:
        # Same underlying RAM-driven tier as the GPU test above, but CPU-only should
        # not scale up with it — this is the exact gap the safeguard closes.
        ceiling = cpu_budget.effective_ctx_ceiling_tokens("m")
        self.assertLess(ceiling, 32768)
        self.assertEqual(ceiling, cpu_budget._CPU_ONLY_CTX_CAP)

    @patch("services.system.profiler.get_system_profile", return_value=_FakeProfile("cpu"))
    @patch(
        "pipeline.direct.batch_budget.resolve_batch_budget",
        return_value={"num_ctx_raw": 32768, "num_ctx": 8192},
    )
    def test_very_slow_throughput_caps_further(self, _budget, _prof) -> None:
        ceiling = cpu_budget.effective_ctx_ceiling_tokens("m", tokens_per_second=1.0)
        self.assertEqual(ceiling, cpu_budget._VERY_SLOW_CTX_CAP)

    def test_tighten_for_cpu_noop_on_gpu(self) -> None:
        with patch(
            "services.system.profiler.get_system_profile", return_value=_FakeProfile("cuda")
        ):
            cfg = {"max_sources": 8, "retrieval_depth": 40}
            self.assertEqual(cpu_budget.tighten_for_cpu(cfg), cfg)

    def test_tighten_for_cpu_scales_down(self) -> None:
        with patch(
            "services.system.profiler.get_system_profile", return_value=_FakeProfile("cpu")
        ):
            cfg = {"max_sources": 8, "retrieval_depth": 40}
            tightened = cpu_budget.tighten_for_cpu(cfg)
            self.assertLess(tightened["max_sources"], cfg["max_sources"])
            self.assertLess(tightened["retrieval_depth"], cfg["retrieval_depth"])


if __name__ == "__main__":
    unittest.main()
