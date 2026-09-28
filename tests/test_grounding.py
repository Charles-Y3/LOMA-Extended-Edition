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


class ExplicitSearchRequestTests(unittest.TestCase):
    """An explicit "search the internet" instruction must ground regardless of
    topic — the topic-only keyword gate previously meant "search the internet
    about X" silently never searched unless X itself was weather/price/sports/
    news-shaped (reported: "Search to internet. About AI usage." never grounded
    even with the toggle on)."""

    def _grounds(self, query: str) -> bool:
        return grounding.needs_source_grounding(
            has_sources=False, query=query, settings={"web_grounding_enabled": True}
        )

    def test_reported_case_now_grounds(self):
        self.assertTrue(self._grounds("Search to internet. About AI usage."))

    def test_explicit_search_phrasing_variants_ground(self):
        for q in (
            "search the internet about AI usage trends",
            "search net for AI adoption stats",
            "search the web for quantum computing breakthroughs",
            "please search online for the tallest building in the world",
            "google it: best programming language 2026",
            "look this up online: history of chess",
            "internet search: current inflation rate",
        ):
            with self.subTest(q=q):
                self.assertTrue(self._grounds(q))

    def test_explicit_search_disabled_by_toggle(self):
        # An explicit request still needs the toggle on — it doesn't bypass that gate.
        self.assertFalse(
            grounding.needs_source_grounding(
                has_sources=False, query="search the internet about AI usage",
                settings={"web_grounding_enabled": False},
            )
        )

    def test_non_search_intent_still_does_not_ground(self):
        # "net"/"search" appearing incidentally must not misfire.
        for q in (
            "write a search algorithm in python",
            "look at the net profit for this quarter",
            "find the net income in this spreadsheet",
            "translate this sentence into French",
            "once upon a time there was a dragon",
        ):
            with self.subTest(q=q):
                self.assertFalse(self._grounds(q))

    def test_multilingual_explicit_search_grounds(self):
        for q in (
            "幫我上網查一下AI使用趨勢",  # zh_tw
            "搜索网络关于人工智能的使用",  # zh_cn
            "busca en internet sobre el uso de la IA",  # es
            "suche im internet nach KI-Nutzungstrends",  # de
        ):
            with self.subTest(q=q):
                self.assertTrue(self._grounds(q))


if __name__ == "__main__":
    unittest.main()
