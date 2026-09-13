# -*- coding: utf-8 -*-
import unittest

from extensions.history_events.encounters import ENCOUNTERS, ERA_ANY, ERA_BUCKET_LABELS, pick_encounter
from extensions.history_events.engine import (
    _grade_ceiling,
    _parse_grade,
    _worse_grade,
    cap_scores_for_answer,
    finalize_scores,
    format_encounter_post,
    format_score_lines,
)


class TestHistoryEvents(unittest.TestCase):
    def test_encounter_count_and_eras(self) -> None:
        self.assertGreaterEqual(len(ENCOUNTERS), 50)
        buckets = {e.era_bucket for e in ENCOUNTERS}
        for key in ERA_BUCKET_LABELS:
            if key and key != ERA_ANY:
                self.assertIn(key, buckets)

    def test_pick_respects_era(self) -> None:
        enc = pick_encounter(era_bucket="ancient")
        self.assertEqual(enc.era_bucket, "ancient")

    def test_encounter_post_has_background(self) -> None:
        enc = next(e for e in ENCOUNTERS if e.id == "berlin_wall_fall_1989")
        post = format_encounter_post(enc)
        self.assertIn("### Background", post)
        self.assertIn("### The situation", post)
        self.assertIn("### Pressures to weigh", post)
        self.assertNotIn(enc.what_happened, post)

    def test_format_score_lines(self) -> None:
        text = format_score_lines(
            {"engagement": 8, "reasoning": 10, "factors": 6, "historical_fit": 7},
            grade="D",
        )
        self.assertIn("Engagement: 8/25", text)
        self.assertIn("Total: 31/100", text)
        self.assertIn("\n\n", text)

    def test_finalize_scores_sums_criteria(self) -> None:
        scores = finalize_scores(
            {
                "engagement": 10,
                "reasoning": 0,
                "factors": 0,
                "historical_fit": 0,
                "total": 15,
            }
        )
        self.assertEqual(scores["total"], 10)

    def test_parse_grade_uses_summed_total(self) -> None:
        raw = (
            "---DIMENSIONS---\nengagement:10\nreasoning:0\nfactors:0\n"
            "historical_fit:0\ntotal:15\n\n---GRADE---\nE\n\n---FEEDBACK---\nBrief."
        )
        grade, _, scores = _parse_grade(raw, answer="a short useless reply here today")
        self.assertEqual(scores["total"], 10)
        self.assertEqual(grade, "F")

    def test_dismissive_scores(self) -> None:
        scores = cap_scores_for_answer(
            "nothing",
            {"engagement": 15, "reasoning": 10, "factors": 10, "historical_fit": 10},
        )
        self.assertEqual(scores["total"], 5)
        self.assertEqual(scores["reasoning"], 0)

    def test_grade_ceiling_dismissive(self) -> None:
        self.assertEqual(_grade_ceiling("nothing"), "F")
        self.assertEqual(_grade_ceiling("war fight revolt"), "E")

    def test_parse_grade_caps_lenient_llm(self) -> None:
        raw = "---DIMENSIONS---\nengagement:20\nreasoning:18\nfactors:19\nhistorical_fit:18\ntotal:75\n\n---GRADE---\nB\n\n---FEEDBACK---\nToo kind."
        grade, _, scores = _parse_grade(raw, answer="nothing")
        self.assertEqual(grade, "F")
        self.assertEqual(scores.get("total"), 5)
        grade2, _, scores2 = _parse_grade(raw, answer="war fight and revolt")
        self.assertIn(grade2, ("D", "E", "F"))
        self.assertLessEqual(scores2.get("total", 100), 35)

    def test_worse_grade(self) -> None:
        self.assertEqual(_worse_grade("B", "F"), "F")
        self.assertEqual(_worse_grade("A", "C"), "C")


if __name__ == "__main__":
    unittest.main()
