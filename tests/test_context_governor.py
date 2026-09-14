# -*- coding: utf-8 -*-
import unittest

from services.context_governor import PromptParts, estimate_tokens, plan_call
from services.providers.base import ProviderCapabilities

# Roomy hardware budget (Ollama can raise within num_ctx_raw).
BIG = {"num_ctx": 4096, "num_ctx_raw": 32768, "max_tokens": 2048}
OLLAMA = ProviderCapabilities(can_set_ctx=True, reports_loaded_ctx=True)
LMSTUDIO = ProviderCapabilities(can_set_ctx=False, reports_loaded_ctx=False)


class TokenEstimateTests(unittest.TestCase):
    def test_cjk_counts_higher_than_latin(self):
        latin = "word " * 100            # ~500 chars
        cjk = "字" * 100                  # 100 chars, but ~100 tokens
        self.assertGreater(estimate_tokens(cjk), estimate_tokens(latin) // 2)
        # 100 CJK chars should estimate ~>=100 tokens; 500 latin chars ~>=125 tokens
        self.assertGreaterEqual(estimate_tokens(cjk), 100)

    def test_empty(self):
        self.assertEqual(estimate_tokens(""), 0)


class GovernorPlanTests(unittest.TestCase):
    def test_small_prompt_single_pass_no_raise(self):
        p = PromptParts(fixed="sys", payload="short doc")
        plan = plan_call(p, budget=BIG, caps=OLLAMA)
        self.assertEqual(plan.strategy, "single")
        self.assertEqual(plan.num_ctx, 4096)  # no raise needed

    def test_large_prompt_raises_ctx_on_ollama(self):
        big_doc = "word " * 6000  # ~30k chars -> ~8k+ tokens, over 4096 ctx
        p = PromptParts(fixed="sys", payload=big_doc)
        plan = plan_call(p, budget=BIG, caps=OLLAMA)
        self.assertEqual(plan.strategy, "single")
        self.assertGreater(plan.num_ctx, 4096)         # raised
        self.assertLessEqual(plan.num_ctx, 32768)       # within hw ceiling

    def test_exceeds_hardware_triggers_multipass(self):
        huge = "word " * 60000  # ~300k chars, far over 32768 ctx
        p = PromptParts(fixed="sys", payload=huge)
        plan = plan_call(p, budget=BIG, caps=OLLAMA)
        self.assertEqual(plan.strategy, "multipass")
        self.assertGreaterEqual(plan.passes, 2)
        self.assertEqual(plan.num_ctx, 32768)           # capped at ceiling

    def test_lmstudio_never_raises_ctx(self):
        big_doc = "word " * 6000
        p = PromptParts(fixed="sys", payload=big_doc)
        # loaded window known to be small -> must multipass, not raise
        plan = plan_call(p, budget=BIG, caps=LMSTUDIO, loaded_ctx=4096)
        self.assertIn(plan.strategy, ("multipass", "shrink"))
        self.assertEqual(plan.ceiling, 4096)

    def test_lmstudio_fits_loaded_window_single(self):
        p = PromptParts(fixed="sys", payload="short")
        plan = plan_call(p, budget=BIG, caps=LMSTUDIO, loaded_ctx=8192)
        self.assertEqual(plan.strategy, "single")
        self.assertFalse(plan.can_set_ctx)

    def test_expected_output_reserves_room(self):
        p = PromptParts(fixed="sys", payload="x" * 4000, expected_output_chars=40000)
        plan = plan_call(p, budget=BIG, caps=OLLAMA)
        # needs room for big output -> ctx raised above default
        self.assertGreater(plan.num_ctx, 4096)
        self.assertGreater(plan.num_predict, 2048)

    def test_output_never_exceeds_ceiling_room(self):
        p = PromptParts(fixed="x" * 100000, expected_output_chars=100000)
        plan = plan_call(p, budget=BIG, caps=OLLAMA)
        self.assertLessEqual(plan.num_predict, plan.ceiling)

    def test_none_caps_defaults_to_full_capability(self):
        p = PromptParts(payload="word " * 6000)
        plan = plan_call(p, budget=BIG, caps=None)
        self.assertTrue(plan.can_set_ctx)


class LlmBridgeHelperTests(unittest.TestCase):
    def test_is_context_overflow_error_matches(self):
        from services.context_governor import is_context_overflow_error

        self.assertTrue(is_context_overflow_error(RuntimeError("the input exceeds the context length")))
        self.assertTrue(is_context_overflow_error(ValueError("n_ctx too small")))
        self.assertFalse(is_context_overflow_error(RuntimeError("connection refused")))

    def test_messages_text_extracts_dict_and_array(self):
        from services.context_governor import _messages_text

        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": [{"type": "text", "text": "world"}, {"type": "image_url"}]},
        ]
        out = _messages_text(msgs)
        self.assertIn("hello", out)
        self.assertIn("world", out)

    def test_govern_options_is_raise_only(self):
        from services.context_governor import govern_options
        from services.providers.base import ProviderCapabilities

        caps = ProviderCapabilities(can_set_ctx=True)
        # caller already asked for a huge window; governor must not lower it
        opts = govern_options(text="short", options={"num_ctx": 99999}, model="", caps=caps)
        self.assertGreaterEqual(opts["num_ctx"], 99999)

    def test_govern_options_fixed_window_untouched(self):
        from services.context_governor import govern_options
        from services.providers.base import ProviderCapabilities

        caps = ProviderCapabilities(can_set_ctx=False)
        opts = govern_options(text="x" * 50000, options={"num_ctx": 4096}, model="", caps=caps)
        self.assertEqual(opts["num_ctx"], 4096)  # not resized on a fixed-window backend


if __name__ == "__main__":
    unittest.main()
