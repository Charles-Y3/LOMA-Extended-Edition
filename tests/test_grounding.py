# -*- coding: utf-8 -*-
import unittest

from services import grounding


class GroundingResolverTests(unittest.TestCase):
    def test_attached_sources_always_ground(self):
        self.assertTrue(grounding.needs_source_grounding(has_sources=True))
        self.assertTrue(
            grounding.needs_source_grounding(has_sources=True, query="anything", settings={})
        )

    def test_no_sources_web_disabled_no_grounding(self):
        self.assertFalse(
            grounding.needs_source_grounding(
                has_sources=False, query="what is the weather today",
                settings={"web_grounding_enabled": False},
            )
        )

    def test_no_sources_web_enabled_live_query_grounds(self):
        self.assertTrue(
            grounding.needs_source_grounding(
                has_sources=False, query="what is the weather in Paris today",
                settings={"web_grounding_enabled": True},
            )
        )

    def test_no_sources_web_enabled_timeless_query_no_web(self):
        # A non-live-fact question shouldn't trigger a web search even with web on.
        self.assertFalse(
            grounding.needs_source_grounding(
                has_sources=False, query="explain how photosynthesis works",
                settings={"web_grounding_enabled": True},
            )
        )

    def test_extended_priority_includes_web(self):
        prio = grounding.source_priority()
        self.assertEqual(prio[0], "attached_sources")
        self.assertIn("web", prio)
        self.assertLess(prio.index("web"), prio.index("model_knowledge"))


if __name__ == "__main__":
    unittest.main()
