#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Regression checks for per-source summary, image routing, spreadsheet plans."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def test_per_source_digest_isolation():
    from pipeline.direct.input_slicer import build_step_input_payload, digest_text_for_step
    from pipeline.direct.query_planner import PlannedStep

    class D:
        def __init__(self, name, text):
            self.name = name
            self.full_text = text

    class Bundle:
        context_strategy = "per_source"
        unified_text = "COMBINED PREVIEW — all three files mixed"
        source_digests = [
            D("1 測試文件.docx", "DOC_A_ONLY"),
            D("2 testppt_mutation.pptx", "PPTX_B_ONLY"),
            D("2 我看了一个电影s.pptx", "PPTX_C_ONLY"),
        ]

    b = Bundle()
    step_a = PlannedStep("Summarize 1 測試文件.docx", ["summarizer"], "chat_summary")
    step_b = PlannedStep("Summarize 2 testppt_mutation.pptx", ["summarizer"], "chat_summary")
    assert digest_text_for_step(step_a, b) == "DOC_A_ONLY"
    assert digest_text_for_step(step_b, b) == "PPTX_B_ONLY"
    payload = build_step_input_payload(
        step=step_b, mode="generation", output_type="chat", bundle=b
    )
    assert "PPTX_B_ONLY" in payload
    assert "DOC_A_ONLY" not in payload
    assert "COMBINED PREVIEW" not in payload
    print("per_source digest isolation: OK")


def test_image_mutation_routing():
    from pipeline.routing_helpers import query_requests_image_mutation, should_route_image_mutation
    from pipeline.schemas.task_schema import InputMetadata

    q = "change all the muffins colour to blue"
    assert query_requests_image_mutation(q)
    meta = InputMetadata(
        query=q,
        preferred_output_format="image",
        file_count=1,
        link_count=0,
        links=[],
        files=[{"name": "5 count.jpg", "type": "image"}],
        has_docs=False,
        has_image=True,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    assert should_route_image_mutation(meta)
    print("image mutation routing (muffins): OK")


def test_spreadsheet_analyse_plans():
    from pipeline.direct.query_planner import DirectPlan, _apply_spreadsheet_report_plan
    from pipeline.schemas.task_schema import InputMetadata

    def meta(q: str):
        return InputMetadata(
            query=q,
            preferred_output_format="document",
            file_count=1,
            link_count=0,
            links=[],
            files=[{"name": "3 Social Media Analyticss_csv.csv", "type": "spreadsheet"}],
            has_docs=True,
            has_image=False,
            profile_id="none",
            message_count=0,
            has_excerpt=False,
            has_preview_selection=False,
            has_valid_preview_selection=False,
            query_source="user",
        )

    plan_analyse = DirectPlan(express=False, step_count=1, mode="generation", output_type="document", steps=[])
    _apply_spreadsheet_report_plan(plan_analyse, meta("analyse"), has_charts=True)
    assert len(plan_analyse.steps) == 1
    assert plan_analyse.steps[0].roles == ["data_analyst"]

    plan_report = DirectPlan(express=False, step_count=1, mode="generation", output_type="document", steps=[])
    _apply_spreadsheet_report_plan(
        plan_report, meta("analyse and write a report"), has_charts=True
    )
    assert len(plan_report.steps) == 2
    assert plan_report.steps[1].roles == ["writer"]
    print("spreadsheet analyse plans: OK")


def test_image_recolor_rules():
    from services.image_inpaint import is_simple_color_edit

    assert not is_simple_color_edit("change all the muffins colour to blue")
    assert is_simple_color_edit("change the frog colour to green")
    print("image recolor vs inpaint: OK")


def test_sound_dialogue_lines():
    from services.sound_synthesis import _dialogue_lines_from_prose, infer_sound_mode

    q = "a conversation of a male and female arguing about parenthood"
    script = "We need to talk about this. I don't think you're ready. That's not fair."
    assert infer_sound_mode(q, script) == "dialogue"
    lines = _dialogue_lines_from_prose(script, q)
    assert len(lines) >= 2
    assert lines[0][0] in ("male", "female")
    print("sound dialogue line split: OK")


def test_per_source_summary_plan():
    from pipeline.direct.query_planner import DirectPlan, _expand_per_source_steps, _wants_per_source_summary
    from pipeline.schemas.task_schema import InputMetadata

    class D:
        def __init__(self, name, text):
            self.name = name
            self.full_text = text
            self.kind = "document"
            self.preview = text[:80]

    class Bundle:
        context_strategy = "per_source"
        source_digests = [D("a.pdf", "A"), D("b.pptx", "B"), D("c.pptx", "C"), D("news", "D")]

    meta = InputMetadata(
        query="give a summary for each source",
        preferred_output_format="chat",
        file_count=3,
        link_count=1,
        links=["http://example.com"],
        files=[],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    assert _wants_per_source_summary(meta.query, 4)
    plan = DirectPlan(express=False, step_count=4, mode="generation", output_type="chat", steps=[])
    _expand_per_source_steps(plan, Bundle(), meta)
    assert len(plan.steps) == 4
    assert all(s.roles == ["summarizer"] for s in plan.steps)
    print("per_source summary plan: OK")


def test_sound_normalize():
    from services.sound_synthesis import normalize_sound_script

    raw = "Elias: (Sighs) I cannot keep up.\nSarah: You are doing fine."
    out = normalize_sound_script(raw, "dialogue between father and mother")
    assert "Elias:" in out or "Sarah:" in out or "Male:" in out or "Female:" in out
    assert "(Sighs)" not in out
    print("sound normalize: OK")


def test_sound_voice_map():
    from services.sound_synthesis import _build_voice_map, _parse_cast_block, _unique_speakers, _parse_dialogue_lines

    script = (
        "Characters:\n"
        "Elias (male): father\n"
        "Sarah (female): mother\n"
        "Alex (neutral): child\n\n"
        "Elias: Hello there.\n"
        "Sarah: Hi Elias.\n"
        "Alex: Can we talk?\n"
    )
    cast = _parse_cast_block(script)
    lines = _parse_dialogue_lines(script)
    speakers = _unique_speakers(lines)
    assert len(speakers) == 3
    voice_map = _build_voice_map(speakers, cast, "family dialogue")
    voices = {voice_map[s][0] for s in speakers}
    assert len(voices) == 3
    print("sound voice map: OK")


def test_presentation_plan():
    from pipeline.direct.planner_worker import ensure_presentation_pipeline
    from pipeline.direct.query_planner import DirectPlan, PlannedStep
    from pipeline.schemas.task_schema import InputMetadata

    meta = InputMetadata(
        query="generate a presentation on kindness",
        preferred_output_format="presentation",
        file_count=0,
        link_count=0,
        links=[],
        files=[],
        has_docs=False,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    plan = DirectPlan(
        express=False,
        step_count=1,
        mode="generation",
        output_type="presentation",
        steps=[
            PlannedStep(
                "create deck",
                ["pres_slide_planner", "pres_narrative", "pres_author"],
                "chat_default",
            ),
            PlannedStep("format", ["slide_author"], "chat_default"),
        ],
    )
    ensure_presentation_pipeline(plan, meta)
    roles = [s.roles[0] for s in plan.steps]
    assert roles == ["deck_planner", "slide_author"]
    print("presentation plan: OK")


def test_force_per_source_plan():
    from pipeline.direct.query_planner import (
        DirectPlan,
        _finalize_plan,
        _force_per_source_summary_plan,
    )
    from pipeline.schemas.task_schema import InputMetadata

    class D:
        def __init__(self, name, text):
            self.name = name
            self.full_text = text
            self.kind = "document"
            self.preview = text[:80]

    class Bundle:
        context_strategy = "per_source"
        source_digests = [D("a.pdf", "A"), D("b.pptx", "B"), D("c.pptx", "C")]

    meta = InputMetadata(
        query="give a summary for each source",
        preferred_output_format="chat",
        file_count=3,
        link_count=0,
        links=[],
        files=[],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    plan = DirectPlan(
        express=False,
        step_count=2,
        mode="generation",
        output_type="chat",
        steps=[],
    )
    from pipeline.direct.query_planner import _build_step

    plan.steps = [
        _build_step("extract", "chat", "generation", meta, roles=["extractor"]),
        _build_step("synthesize", "chat", "generation", meta, roles=["synthesizer"]),
    ]
    _force_per_source_summary_plan(plan, Bundle(), meta)
    assert len(plan.steps) == 3
    assert all(s.roles == ["summarizer"] for s in plan.steps)
    print("force per-source plan: OK")


def test_dialogue_part_cleanup():
    import tempfile
    from pathlib import Path

    from services.sound_synthesis import _cleanup_dialogue_parts

    with tempfile.TemporaryDirectory() as tmp:
        final = os.path.join(tmp, "dialogue_loma.wav")
        legacy_part = f"{final}.part0.wav"
        Path(legacy_part).write_bytes(b"RIFF")
        _cleanup_dialogue_parts(final)
        assert not os.path.isfile(legacy_part)
    print("dialogue part cleanup: OK")


def test_father_mother_cast_traits():
    from services.sound_synthesis import (
        _dialogue_lines_from_prose,
        _parse_cast,
        normalize_sound_script,
    )

    script = (
        "Father: male: supportive, patient\n\n"
        "Mother: female: supportive, patient\n\n"
        "Father: I know you've been thinking a lot.\n"
        "Mother: I have been wondering too.\n"
    )
    cast = _parse_cast(script)
    assert cast.get("father") == "male"
    assert cast.get("mother") == "female"
    lines = _dialogue_lines_from_prose(script, "conversation between father and mother")
    assert len(lines) == 2
    assert "male:" not in lines[0][1].lower()
    out = normalize_sound_script(script, "father and mother parenting")
    assert "male: supportive" not in out
    print("father/mother cast traits: OK")


def test_each_input_summary_intent():
    from pipeline.direct.query_planner import _wants_per_source_summary

    assert _wants_per_source_summary("give me a summary for each input", 2)
    print("each input summary intent: OK")


def test_dialogue_between_mode():
    from services.sound_synthesis import infer_sound_mode

    assert infer_sound_mode("a dialogue between father and mother", "") == "dialogue"
    print("dialogue between mode: OK")


def test_per_source_stream_slice():
    """Step 2 stream must not include step 1 text in step_out."""
    from pipeline.capability_runtime.chat_runner import stream_chat_response
    from pipeline.state_machine import UISink
    from services.session import state

    class FakeSink(UISink):
        def log(self, msg):
            pass

        def refresh_chat(self):
            pass

        def refresh_chat_throttled(self):
            pass

        def scroll_chat(self):
            pass

        def ensure_assistant_message(self):
            if not state.messages or state.messages[-1].get("role") != "assistant":
                state.messages.append({"role": "assistant", "content": ""})

        def append_assistant_token(self, token: str):
            self.ensure_assistant_message()
            state.messages[-1]["content"] += token

        def set_assistant_content(self, content: str):
            self.ensure_assistant_message()
            state.messages[-1]["content"] = content

    state.messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "DOC_SUMMARY"}]
    sink = FakeSink()

    class FakeStream:
        def __iter__(self):
            for ch in "PPT_ONLY":
                yield {"message": {"content": ch}}

    import pipeline.capability_runtime.chat_runner as cr

    orig = cr._chat_call
    cr._chat_call = lambda *a, **k: (FakeStream(), 0)
    try:
        out = stream_chat_response(
            profile={},
            model="test",
            messages=[{"role": "user", "content": "x"}],
            sink=sink,
            is_cancelled=lambda: False,
            stream_base="DOC_SUMMARY",
        )
        assert out == "PPT_ONLY"
        assert state.messages[-1]["content"] == "DOC_SUMMARY\n\nPPT_ONLY"
    finally:
        cr._chat_call = orig
    print("per_source stream slice: OK")


def test_combined_both_sources_plan():
    from pipeline.direct.query_planner import (
        DirectPlan,
        _force_combined_multi_source_plan,
        _wants_combined_summary,
    )
    from pipeline.schemas.task_schema import InputMetadata

    q = "write a report on cultivation from both sources"
    assert _wants_combined_summary(q, 2)

    class D:
        def __init__(self, name, text):
            self.name = name
            self.full_text = text
            self.kind = "document"

    class Bundle:
        source_digests = [D("a.docx", "A"), D("b.pptx", "B")]

    meta = InputMetadata(
        query=q,
        preferred_output_format="chat",
        file_count=2,
        link_count=0,
        links=[],
        files=[],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    plan = DirectPlan(
        express=False,
        step_count=2,
        mode="generation",
        output_type="chat",
        steps=[],
    )
    from pipeline.direct.query_planner import _build_step

    plan.steps = [
        _build_step("extract from a.docx", "chat", "generation", meta, roles=["extractor"]),
        _build_step(q, "chat", "generation", meta, roles=["synthesizer"]),
    ]
    _force_combined_multi_source_plan(plan, Bundle(), meta)
    assert len(plan.steps) == 3
    assert plan.steps[0].roles == ["extractor"]
    assert plan.steps[1].roles == ["extractor"]
    assert plan.steps[-1].roles == ["synthesizer"]
    print("combined both-sources plan: OK")


def test_extract_intent_digest_match():
    from pipeline.direct.input_slicer import digest_text_for_step
    from pipeline.direct.query_planner import PlannedStep

    class D:
        def __init__(self, name, text):
            self.name = name
            self.full_text = text

    bundle = type(
        "B",
        (),
        {
            "source_digests": [
                D("1 測試文件.docx", "DOCX_TEXT"),
                D("2 testppt_mutation.pptx", "PPTX_TEXT"),
            ]
        },
    )()

    step = PlannedStep(
        intent="Extract key points from 2 testppt_mutation.pptx",
        roles=["extractor"],
        output_constraint_id="",
    )
    assert digest_text_for_step(step, bundle) == "PPTX_TEXT"
    print("extract intent digest match: OK")


def test_all_sources_combined_intent():
    from pipeline.direct.query_planner import _wants_combined_summary

    q = "provide a summary on cultivation from all sources"
    assert _wants_combined_summary(q, 2)
    print("all sources combined intent: OK")


def test_presentation_multi_source_finalize():
    from pipeline.direct.query_planner import DirectPlan, PlannedStep, _finalize_plan
    from pipeline.schemas.task_schema import InputMetadata

    class D:
        def __init__(self, name, text, kind="document"):
            self.name = name
            self.full_text = text
            self.kind = kind

    class Bundle:
        context_strategy = "per_source"
        source_digests = [
            D("2 心得筆記.docx", "A"),
            D("1 測試文件.docx", "B"),
            D("2 testppt_mutation.pptx", "C", "presentation"),
        ]

    meta = InputMetadata(
        query="do a presentation on kindness and cultivation using all sources",
        preferred_output_format="presentation",
        file_count=3,
        link_count=0,
        links=[],
        files=[
            {"name": "2 心得筆記.docx", "type": "document"},
            {"name": "1 測試文件.docx", "type": "document"},
            {"name": "2 testppt_mutation.pptx", "type": "presentation"},
        ],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    plan = DirectPlan(
        express=False,
        step_count=3,
        mode="mutation",
        output_type="presentation",
        steps=[
            PlannedStep("Summarize 2 心得筆記.docx", ["summarizer"], "chat_default"),
            PlannedStep("Summarize 1 測試文件.docx", ["summarizer"], "chat_default"),
            PlannedStep("Summarize 2 testppt_mutation.pptx", ["summarizer"], "chat_default"),
        ],
    )
    _finalize_plan(plan, meta, bundle=Bundle())
    assert plan.mode == "generation"
    assert plan.source_strategy == "combined"
    roles = [s.roles[0] for s in plan.steps]
    assert roles.count("extractor") == 3
    assert roles.count("synthesizer") == 1
    assert "deck_planner" in roles
    assert roles[-1] == "slide_author"
    print("presentation multi-source finalize: OK")


def test_scratch_document_single_writer_step():
    from pipeline.direct.query_planner import (
        DirectPlan,
        PlannedStep,
        _ensure_deliverable_author_step,
    )
    from pipeline.schemas.task_schema import InputMetadata

    meta = InputMetadata(
        query="kindness",
        preferred_output_format="document",
        file_count=0,
        link_count=0,
        links=[],
        files=[],
        has_docs=False,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )
    plan = DirectPlan(
        express=False,
        step_count=1,
        mode="generation",
        output_type="document",
        steps=[PlannedStep("kindness", ["general_answer"], "chat_default")],
    )
    _ensure_deliverable_author_step(plan, meta)
    assert len(plan.steps) == 1
    assert plan.steps[0].roles == ["writer"]
    assert plan.steps[0].output_constraint_id == "document_markdown"
    print("scratch document single writer: OK")


def test_step_prompt_no_duplicate_prior_output():
    from pipeline.direct.input_slicer import build_step_input_payload
    from pipeline.direct.prompt_hygiene import user_step_content
    from pipeline.direct.query_planner import PlannedStep

    class Bundle:
        unified_text = ""

    prior = "Section one about kindness."
    step = PlannedStep("Format content as document deliverable", ["writer"], "document_markdown")
    body = build_step_input_payload(
        step=step,
        mode="generation",
        output_type="document",
        bundle=Bundle(),
        working_text=prior,
    )
    message = user_step_content(
        user_query=step.intent,
        step_body=body,
        working_text=prior,
    )
    assert message.count(prior) == 1
    print("step prompt prior-output dedup: OK")


def _two_source_meta(query: str):
    from pipeline.schemas.task_schema import InputMetadata

    return InputMetadata(
        query=query,
        preferred_output_format="chat",
        file_count=2,
        link_count=0,
        links=[],
        files=[
            {"name": "1 測試文件.docx", "type": "document"},
            {"name": "2 testppt_mutation.pptx", "type": "presentation"},
        ],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )


def _two_source_bundle():
    class D:
        def __init__(self, name, text, kind="document"):
            self.name = name
            self.full_text = text
            self.kind = kind

    class Bundle:
        source_digests = [
            D("1 測試文件.docx", "DOCX_TEXT", "document"),
            D("2 testppt_mutation.pptx", "PPTX_TEXT", "presentation"),
        ]

    return Bundle()


def test_finalize_multi_source_summarize_translate():
    from pipeline.direct.query_planner import DirectPlan, _finalize_plan

    plan = DirectPlan(
        express=False,
        step_count=1,
        mode="generation",
        output_type="chat",
        steps=[],
    )
    out = _finalize_plan(
        plan,
        _two_source_meta("summarise and then translate to english"),
        bundle=_two_source_bundle(),
    )
    assert out.step_count == 3
    assert out.steps[0].roles == ["extractor"]
    assert out.steps[1].roles == ["extractor"]
    assert out.steps[-1].roles == ["synthesizer"]
    assert "translate" in out.steps[-1].intent.lower()
    assert out.mode == "generation"
    print("finalize multi-source summarize+translate: OK")


def test_finalize_multi_source_translate_only():
    from pipeline.direct.query_planner import DirectPlan, _finalize_plan

    plan = DirectPlan(express=False, step_count=1, mode="generation", output_type="chat", steps=[])
    out = _finalize_plan(
        plan,
        _two_source_meta("translate both files to english"),
        bundle=_two_source_bundle(),
    )
    assert out.step_count == 3
    assert out.steps[-1].roles == ["synthesizer"]
    assert out.mode == "generation"
    print("finalize multi-source translate only: OK")


def test_finalize_multi_source_summarize_only():
    from pipeline.direct.query_planner import DirectPlan, _finalize_plan

    plan = DirectPlan(express=False, step_count=1, mode="generation", output_type="chat", steps=[])
    out = _finalize_plan(
        plan,
        _two_source_meta("summarise both files"),
        bundle=_two_source_bundle(),
    )
    assert out.step_count == 3
    assert out.steps[-1].roles == ["synthesizer"]
    assert out.mode == "generation"
    print("finalize multi-source summarize only: OK")


def test_finalize_single_file_translate_generation():
    from pipeline.direct.query_planner import DirectPlan, _finalize_plan
    from pipeline.schemas.task_schema import InputMetadata

    meta = InputMetadata(
        query="translate to english",
        preferred_output_format="chat",
        file_count=1,
        link_count=0,
        links=[],
        files=[{"name": "1 測試文件.docx", "type": "document"}],
        has_docs=True,
        has_image=False,
        profile_id="none",
        message_count=0,
        has_excerpt=False,
        has_preview_selection=False,
        has_valid_preview_selection=False,
        query_source="user",
    )

    class D:
        name = "1 測試文件.docx"
        full_text = "DOCX"
        kind = "document"

    class Bundle:
        source_digests = [D()]

    plan = DirectPlan(express=False, step_count=1, mode="generation", output_type="chat", steps=[])
    out = _finalize_plan(plan, meta, bundle=Bundle())
    assert out.step_count == 1
    assert out.steps[0].roles == ["translator"]
    assert out.mode == "generation"
    print("finalize single-file translate generation: OK")


def test_finalize_per_source_summarize_preserved():
    from pipeline.direct.query_planner import DirectPlan, _finalize_plan

    meta = _two_source_meta("summarize each file")
    bundle = _two_source_bundle()
    plan = DirectPlan(express=False, step_count=2, mode="generation", output_type="chat", steps=[])
    from pipeline.direct.planner_worker import apply_source_strategy_steps

    apply_source_strategy_steps(plan, bundle, meta)
    assert len(plan.steps) == 2
    assert all(s.roles == ["summarizer"] for s in plan.steps)
    out = _finalize_plan(plan, meta, bundle=bundle)
    assert out.step_count == 2
    assert all(s.roles == ["summarizer"] for s in out.steps)
    print("finalize per-source summarize preserved: OK")


def test_express_lane_skips_enforce():
    from pipeline.direct.query_planner import DirectPlan, _enforce_document_transform_plan

    plan = DirectPlan(express=True, step_count=0, mode="generation", output_type="chat", steps=[])
    _enforce_document_transform_plan(
        plan,
        _two_source_meta("summarise and translate to english"),
        bundle=_two_source_bundle(),
    )
    assert plan.express is True
    assert not plan.steps
    print("express lane skips document enforce: OK")


if __name__ == "__main__":
    test_per_source_digest_isolation()
    test_per_source_summary_plan()
    test_image_mutation_routing()
    test_spreadsheet_analyse_plans()
    test_image_recolor_rules()
    test_sound_dialogue_lines()
    test_sound_normalize()
    test_sound_voice_map()
    test_presentation_plan()
    test_force_per_source_plan()
    test_dialogue_part_cleanup()
    test_father_mother_cast_traits()
    test_each_input_summary_intent()
    test_dialogue_between_mode()
    test_per_source_stream_slice()
    test_combined_both_sources_plan()
    test_extract_intent_digest_match()
    test_all_sources_combined_intent()
    test_presentation_multi_source_finalize()
    test_scratch_document_single_writer_step()
    test_step_prompt_no_duplicate_prior_output()
    test_finalize_multi_source_summarize_translate()
    test_finalize_multi_source_translate_only()
    test_finalize_multi_source_summarize_only()
    test_finalize_single_file_translate_generation()
    test_finalize_per_source_summarize_preserved()
    test_express_lane_skips_enforce()
    print("DONE")
