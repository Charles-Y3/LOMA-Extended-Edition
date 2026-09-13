# -*- coding: utf-8 -*-
"""Scenario matrix for the direct pipeline's task/delivery/contract spine
(pipeline/direct/plan_builder.py). Pure, deterministic, no LLM — this is the regression net
for the architectural fix: contract selection must key on TASK, never on delivery format.

Each test corresponds to one row of the scenario matrix in the approved plan
(C:\\Users\\charl\\.claude\\plans\\robust-strolling-popcorn.md), covering 1-3 uploads,
single/multi step, chat/document delivery, and the two real bugs this fixed:
  1. translate -> document must use the translation contract, not an authoring contract
     (previously produced a half-translated, half-echoed source).
  2. a plain-answer role over a non-tabular document must never see the analysis
     contract's dataset/correlation rules (previously hallucinated "r=0.89").
"""
from __future__ import annotations

import unittest

from pipeline.direct.plan_builder import (
    classify_tasks,
    contract_for_role,
    decide_fanout_assemble,
    has_tabular_source,
    needs_format_stage,
)


class PlanMatrixTests(unittest.TestCase):
    # Row 1: 1 pdf, "translate to english" -> chat.
    def test_row1_translate_chat(self) -> None:
        self.assertEqual(classify_tasks("translate to english"), ["translate"])
        self.assertEqual(contract_for_role("translator", output_type="chat"), "chat_translation")

    # Row 2: 1 pdf, "translate to english, as docx" -> document. The key assertion: the
    # translation contract is IDENTICAL whether delivery is chat or document — this is the
    # fix for bug 1 (translator no longer told to author headings/tables).
    def test_row2_translate_document_contract_unchanged_by_delivery(self) -> None:
        self.assertEqual(classify_tasks("translate to english, as a docx"), ["translate"])
        chat_contract = contract_for_role("translator", output_type="chat")
        doc_contract = contract_for_role("translator", output_type="document")
        self.assertEqual(chat_contract, doc_contract)
        self.assertEqual(doc_contract, "chat_translation")

    # Row 3/4: 1 docx, "summarize" -> chat / document. Same invariant as row 2.
    def test_row3_row4_summarize_contract_unchanged_by_delivery(self) -> None:
        self.assertEqual(classify_tasks("summarize this document"), ["summarize"])
        self.assertEqual(contract_for_role("summarizer", output_type="chat"), "chat_summary")
        self.assertEqual(contract_for_role("summarizer", output_type="document"), "chat_summary")

    # Row 5: 1 xlsx, "analyse and produce a report" -> document, tabular source present.
    def test_row5_analyze_with_tabular_source(self) -> None:
        self.assertEqual(
            classify_tasks("analyse and produce a full analytical report", has_tabular=True),
            ["analyze"],
        )
        self.assertEqual(
            contract_for_role("data_analyst", output_type="document"), "content_analysis"
        )
        self.assertTrue(has_tabular_source(["3 Social Media Analytics.xlsx"]))

    # Row 6: 1 xlsx, a plain computed question -> chat. Handled by the pre-existing,
    # untouched spreadsheet_query role (exact pandas computation, not narrative analysis) —
    # confirm this fix didn't disturb its contract.
    def test_row6_spreadsheet_query_contract_unchanged(self) -> None:
        self.assertEqual(contract_for_role("spreadsheet_query", output_type="chat"), "chat_default")

    # Row 7: 1 docx (NOT tabular), "analyse and produce a report" -> document. This is bug 2:
    # without a tabular source, "analyze" must never be selected, and the plain-answer
    # role's contract must carry zero dataset/correlation rules.
    def test_row7_analyze_downgrades_without_tabular_source(self) -> None:
        self.assertFalse(has_tabular_source(["0 \u8edf\u5316\u81ea\u5df1\u5b78\u53cd\u7701.docx"]))
        tasks = classify_tasks("analyse and produce a full analytical report", has_tabular=False)
        self.assertNotIn("analyze", tasks)
        self.assertEqual(tasks, ["answer"])
        contract = contract_for_role("general_answer", output_type="document")
        self.assertEqual(contract, "chat_default")
        self.assertNotEqual(contract, "content_analysis")
        self.assertNotEqual(contract, "document_markdown")

    # Row 8: 2 docx, "translate both to english" -> chat. Translation always fans out
    # per-source and concatenates — there is no coherent "single combined translation" of
    # two unrelated documents.
    def test_row8_translate_multi_source_always_per_source(self) -> None:
        tasks = classify_tasks("translate both documents to english")
        self.assertEqual(tasks, ["translate"])
        fanout, assemble = decide_fanout_assemble(
            "translate both documents to english", source_count=2, kind="translate"
        )
        self.assertEqual((fanout, assemble), ("per_source", "concat"))

    # Row 9: 2 docx, "summarize both and translate, as docx" -> document, two chained tasks.
    def test_row9_summarize_then_translate_task_order(self) -> None:
        tasks = classify_tasks("summarize both documents and translate, as a docx")
        self.assertEqual(tasks, ["summarize", "translate"])
        fanout, assemble = decide_fanout_assemble(
            "summarize both documents and translate, as a docx", source_count=2, kind="summarize"
        )
        self.assertEqual((fanout, assemble), ("whole", "synthesize"))

    # Row 10: 3 docx, "what are the differences?" -> chat. Comparison words route to a
    # single combined answer over all sources, never per-source isolation (that was the
    # earlier "only answers from document 1" bug this session already fixed elsewhere).
    def test_row10_compare_multi_source(self) -> None:
        tasks = classify_tasks("what are the key differences between these three documents?")
        self.assertEqual(tasks, ["answer"])
        fanout, assemble = decide_fanout_assemble(
            "what are the key differences between these three documents?", source_count=3
        )
        self.assertEqual((fanout, assemble), ("whole", "synthesize"))

    # Row 11: mixed pdf+docx+xlsx, "summarize everything as a report" -> document. The
    # overall task is summarize; the xlsx is independently identifiable as tabular so a
    # later per-file pass can still apply analysis to it specifically.
    def test_row11_mixed_sources_summarize_and_tabular_detection(self) -> None:
        self.assertEqual(classify_tasks("summarize everything as a report"), ["summarize"])
        names = ["notes.pdf", "brief.docx", "figures.xlsx"]
        self.assertTrue(has_tabular_source(names))
        self.assertFalse(has_tabular_source(names[:2]))

    # Row 12: 0 sources, "write a report about X as docx" -> document, from-scratch
    # authoring. This is the one case that legitimately keeps the authoring contract.
    def test_row12_author_from_scratch(self) -> None:
        self.assertEqual(classify_tasks("write a report about renewable energy"), ["author"])
        self.assertEqual(contract_for_role("writer", output_type="document"), "document_markdown")

    # Format-stage predicate: only presentation delivery needs the reflow stage; chat and
    # document are flowing formats with no fixed shape to reflow into.
    def test_format_stage_only_for_presentation(self) -> None:
        self.assertFalse(needs_format_stage("chat"))
        self.assertFalse(needs_format_stage("document"))
        self.assertTrue(needs_format_stage("presentation"))


if __name__ == "__main__":
    unittest.main()
