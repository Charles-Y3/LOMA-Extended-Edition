# -*- coding: utf-8 -*-
"""Image prompts must reach the diffusion model in English (SD/CLIP can't read Chinese), and a
batch (slides / document images) must translate with ONE LLM call up front — an LLM call
between two image generations makes the resource governor evict the warm image pipeline."""
from __future__ import annotations

import pytest

from services import image_generation as ig

ZH_DESCS = ["一隻毛茸茸的黃金獵犬在陽光下奔跑", "現代城市的夜景與霓虹燈", "森林裡的一間小木屋", "海邊日出的漁船", "雪山下的湖泊"]
ZH_QUERY = "製作一份關於自然與城市的簡報，風格簡潔明亮"


@pytest.fixture(autouse=True)
def _clean_glossary():
    ig._ENGLISH_GLOSSARY.clear()
    ig._failed_until.clear()
    yield
    ig._ENGLISH_GLOSSARY.clear()
    ig._failed_until.clear()


def _numbered_lines(content: str) -> list[str]:
    """Only the '1. text' data lines — the request also carries a 'Translate to English:' header."""
    return [ln for ln in content.splitlines() if ln[:1].isdigit() and ". " in ln]


def _source_lines(lines: list[str]) -> list[str]:
    """['1. text', '2. text'] -> ['text', 'text']"""
    return [ln.split(". ", 1)[1] for ln in lines]


class FakeLLM:
    """Stands in for the one function every translation goes through (_llm_translate).
    Speaks the numbered-lines protocol: "1. text" in -> "1. english" out."""

    def __init__(self, reply=None):
        self.calls = 0
        self.reply = reply  # optional callable(list_of_lines) -> raw reply text

    def __call__(self, messages):
        self.calls += 1
        content = messages[-1]["content"]
        lines = _numbered_lines(content)
        single = not lines  # the plain (un-numbered) single-prompt format
        if single:
            lines = [f"1. {content}"]
        if self.reply:
            return self.reply(lines)
        if single:
            return "english scene 0"  # a plain reply with NO "1." prefix, like the real 4B
        return "\n".join(f"{i + 1}. english scene {i}" for i, _ in enumerate(lines))


@pytest.fixture
def fake_llm(monkeypatch):
    fake = FakeLLM()
    monkeypatch.setattr(ig, "_llm_translate", fake)
    return fake


def test_needs_english_detection():
    assert ig._needs_english("一隻黃金獵犬")
    assert ig._needs_english("un perro pequeño")
    assert ig._needs_english("ein Mädchen")
    assert not ig._needs_english("a golden retriever running, 4k — cinematic")


def test_english_prompt_never_calls_llm(fake_llm):
    assert ig.ensure_english_prompt("a red apple on a table") == "a red apple on a table"
    assert fake_llm.calls == 0


def test_single_chinese_prompt_is_translated(fake_llm):
    out = ig.ensure_english_prompt(ZH_DESCS[0])
    assert out == "english scene 0"
    assert not ig._needs_english(out)
    assert fake_llm.calls == 1
    ig.ensure_english_prompt(ZH_DESCS[0])  # cached
    assert fake_llm.calls == 1


def test_batch_of_slides_uses_exactly_one_llm_call(fake_llm):
    """The slide loop builds f"{desc}. Presentation slide visual. {query[:120]}" per slide."""
    ig.pretranslate_prompts(ZH_DESCS + [ZH_QUERY[:120]])
    assert fake_llm.calls == 1
    for desc in ZH_DESCS:
        prompt = ig.prepare_image_prompt(f"{desc}. Presentation slide visual. {ZH_QUERY[:120]}")
        out = ig.ensure_english_prompt(prompt)
        assert not ig._needs_english(out), out
    assert fake_llm.calls == 1, "per-slide generation must not call the LLM again"


def test_control_without_pretranslate_calls_llm_per_slide(fake_llm):
    """Proves the test can fail: this is the swap-per-slide behaviour the pre-pass prevents."""
    for desc in ZH_DESCS:
        ig.ensure_english_prompt(desc)
    assert fake_llm.calls == len(ZH_DESCS)


def test_overlay_band_suffix_still_hits_glossary(fake_llm):
    ig.pretranslate_prompts([ZH_DESCS[0]])
    suffixed = f"{ZH_DESCS[0]}, main subject composed in the upper two-thirds of the frame"
    assert not ig._needs_english(ig.ensure_english_prompt(suffixed))
    assert fake_llm.calls == 1


def test_llm_failure_falls_back_to_original(monkeypatch):
    def boom(messages):
        raise RuntimeError("no model")

    monkeypatch.setattr(ig, "_llm_translate", boom)
    ig.pretranslate_prompts(ZH_DESCS)  # must not raise
    assert ig.ensure_english_prompt(ZH_DESCS[0]) == ZH_DESCS[0]


def test_a_failed_batch_costs_one_call_and_never_disables_translation(monkeypatch):
    """A model that echoes Chinese back for the numbered batch. There is NO breaker: slower-but-produces-something
    beats fast-but-nothing, so each later item is simply tried individually (and can succeed)."""
    def reply(lines):
        if len(lines) > 1:  # the numbered batch: echo the Chinese back
            return "\n".join(f"{i + 1}. {t}" for i, t in enumerate(_source_lines(lines)))
        return "A fluffy dog running in a park"  # a single prompt in the plain format works

    fake = FakeLLM(reply=reply)
    monkeypatch.setattr(ig, "_llm_translate", fake)
    ig.pretranslate_prompts(ZH_DESCS)
    assert fake.calls == 1, "a failed batch is not repeated in the same format"
    assert not ig._ENGLISH_GLOSSARY
    for desc in ZH_DESCS:  # every item then gets its own attempt, and succeeds
        assert ig.ensure_english_prompt(desc) == "A fluffy dog running in a park"
    assert fake.calls == 1 + len(ZH_DESCS)


def test_identical_text_that_just_failed_is_not_re_asked_but_other_text_is(monkeypatch):
    fake = FakeLLM(reply=lambda lines: "sorry")
    monkeypatch.setattr(ig, "_llm_translate", fake)
    assert ig.ensure_english_prompt("一隻狗") == "一隻狗"
    after_first = fake.calls  # plain + numbered format
    assert ig.ensure_english_prompt("一隻狗") == "一隻狗"
    assert fake.calls == after_first, "the identical text just failed: temperature 0 gives the same answer"
    ig.ensure_english_prompt("一隻貓")
    assert fake.calls > after_first, "a different prompt is always tried"


def test_the_identical_text_guard_expires(monkeypatch):
    fake = FakeLLM(reply=lambda lines: "sorry")
    monkeypatch.setattr(ig, "_llm_translate", fake)
    ig.ensure_english_prompt("一隻狗")
    n = fake.calls
    ig._failed_until.clear()  # simulate the short guard expiring
    ig.ensure_english_prompt("一隻狗")
    assert fake.calls > n


def test_batch_prompt_has_examples_and_repeats_the_instruction_in_every_user_turn():
    msgs = ig._batch_messages(ZH_DESCS[:2])
    user_turns = [m["content"] for m in msgs if m["role"] == "user"]
    assert len(user_turns) == len(ig._TRANSLATE_EXAMPLES) + 1
    assert all(t.startswith(ig._TRANSLATE_INSTRUCTION) for t in user_turns)


def test_single_prompt_uses_plain_few_shot_turns_ending_with_the_text():
    msgs = ig._single_messages(ZH_DESCS[0])
    assert msgs[-1] == {"role": "user", "content": ZH_DESCS[0]}
    assert len([m for m in msgs if m["role"] == "assistant"]) == len(ig._SINGLE_EXAMPLES)


def test_reply_without_numbering_is_accepted_for_a_single_prompt(monkeypatch):
    """Live bug: the 4B translated correctly but answered 'A fluffy cat…' with no '1.' prefix, which the numbered
    parser discarded as 'not English' -> Chinese went to the image model -> unrelated pictures."""
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "A fluffy orange house cat lying on a sunlit window sill")
    assert ig.ensure_english_prompt(ZH_DESCS[0]) == "A fluffy orange house cat lying on a sunlit window sill"


def test_unnumbered_batch_reply_is_mapped_by_position(monkeypatch):
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "\n".join(f"english {i}" for i in range(len(ZH_DESCS))))
    ig.pretranslate_prompts(ZH_DESCS)
    assert len(ig._ENGLISH_GLOSSARY) == len(ZH_DESCS)


def test_single_prompt_falls_back_to_the_numbered_format_when_the_plain_one_fails(monkeypatch):
    def reply(lines):
        return "".join(lines[0].split(". ", 1)[1:])  # echo -> fails; overridden below for numbered

    calls = {"n": 0}

    def llm(messages):
        calls["n"] += 1
        content = messages[-1]["content"]
        if content.startswith(ig._TRANSLATE_INSTRUCTION):  # numbered format
            return "1. a dog running in a park"
        return content  # plain format: model echoes the Chinese

    monkeypatch.setattr(ig, "_llm_translate", llm)
    assert ig.ensure_english_prompt("一隻狗在公園奔跑") == "a dog running in a park"
    assert calls["n"] == 2


def test_untranslatable_chinese_is_refused_not_sent_to_the_image_model(monkeypatch):
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "還是中文")
    with pytest.raises(ig.ImagePromptUntranslatedError) as exc:
        ig.require_english_prompt("一隻狗在公園奔跑")
    assert "untranslated" not in str(exc.value) and str(exc.value).strip()


def test_accented_latin_text_is_passed_through_when_translation_fails(monkeypatch):
    """CLIP copes with Spanish/German; only CJK (unreadable to it) is refused."""
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "no english here ñ")
    assert ig.require_english_prompt("un perro pequeño") == "un perro pequeño"


def test_generate_image_refuses_before_drawing_when_the_prompt_cannot_be_translated(monkeypatch, tmp_path):
    monkeypatch.setattr(ig, "GENERATED_IMAGE_DIR", str(tmp_path))
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "還是中文")  # the model never returns English
    drew = {"n": 0}
    monkeypatch.setattr(ig, "_run_generate_image", lambda **kw: drew.__setitem__("n", drew["n"] + 1))
    with pytest.raises(ig.ImagePromptUntranslatedError):
        ig.generate_image("一隻毛茸茸的橘色家貓")
    assert drew["n"] == 0, "the diffusion model must never see untranslated Chinese"


def test_a_failed_single_chat_prompt_never_switches_translation_off_for_the_next_request(monkeypatch):
    """Live bug: one bad reply for 狗 paused translation, so the next request (貓) was never translated."""
    replies = iter(["sorry", "sorry", "A fluffy orange cat lying on a sunny window sill", ""])
    monkeypatch.setattr(ig, "_llm_translate", lambda m: next(replies))
    assert ig.ensure_english_prompt("一隻狗") == "一隻狗"  # plain + numbered format both failed
    assert ig.ensure_english_prompt("一隻毛茸茸的橘色家貓") == "A fluffy orange cat lying on a sunny window sill"


def test_single_prompt_tries_the_plain_format_then_the_numbered_one(monkeypatch):
    fake = FakeLLM(reply=lambda lines: "sorry")
    monkeypatch.setattr(ig, "_llm_translate", fake)
    ig.ensure_english_prompt("一隻狗")
    assert fake.calls == 2


def test_there_is_no_translation_pause_mechanism_left():
    assert not hasattr(ig, "_breaker_until") and not hasattr(ig, "_BREAKER_SECONDS")


def _collector():
    pytest.importorskip("pipeline.workflow")
    from pipeline.deliverables.presentation_compile import _collect_slide_image_descriptions

    return _collect_slide_image_descriptions


def test_prepass_finds_whole_line_markers():
    collect = _collector()
    slides = ["--- Slide 1 ---\n# 標題\n- 要點\n[IMAGE: 台北夜市的人群]", "--- Slide 2 ---\n# 標題二\n[IMAGE_PROMPT: 熱騰騰的蚵仔煎]"]
    assert collect(slides, None) == ["台北夜市的人群", "熱騰騰的蚵仔煎"]


def test_prepass_finds_inline_markers_inside_bullets():
    collect = _collector()
    slides = ["--- Slide 1 ---\n# 標題\n- 夜市很熱鬧 [IMAGE: 燈籠與人潮]\n- 第二點"]
    assert collect(slides, None) == ["燈籠與人潮"]


def test_prepass_includes_the_planners_visual_descriptions():
    """Live decks take image descriptions from the planner's slide_visuals, not only [IMAGE:] lines."""
    collect = _collector()
    visuals = [{"visual_description": "珍珠奶茶攤位"}, {"visual_description": ""}, {"visual_description": "雨夜霓虹街景"}]
    assert collect(["--- Slide 1 ---\n# 標題"], visuals) == ["珍珠奶茶攤位", "雨夜霓虹街景"]


def test_prepass_dedupes_and_ignores_slides_without_images():
    collect = _collector()
    slides = ["--- Slide 1 ---\n# A\n[IMAGE: 同一張圖]", "--- Slide 2 ---\n# B\n[IMAGE: 同一張圖]", "--- Slide 3 ---\n# C\n- 只有文字"]
    assert collect(slides, None) == ["同一張圖"]


def test_partial_failure_retries_only_the_failed_lines(monkeypatch):
    seen: list[int] = []

    def reply(lines):
        seen.append(len(lines))
        if len(seen) == 1:  # first call: only line 1 is translated, the rest are echoed back
            src = _source_lines(lines)
            return "\n".join(f"{i + 1}. " + ("fine english" if i == 0 else src[i]) for i in range(len(lines)))
        return "\n".join(f"{i + 1}. retried english {i}" for i, _ in enumerate(lines))

    monkeypatch.setattr(ig, "_llm_translate", FakeLLM(reply=reply))
    ig.pretranslate_prompts(ZH_DESCS)
    assert seen == [len(ZH_DESCS), len(ZH_DESCS) - 1]
    assert len(ig._ENGLISH_GLOSSARY) == len(ZH_DESCS)


def test_garbage_reply_is_ignored(monkeypatch):
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "sorry, I can't do that")
    ig.pretranslate_prompts(ZH_DESCS)
    assert not ig._ENGLISH_GLOSSARY


def test_multiline_prompt_is_flattened_to_one_numbered_line(fake_llm):
    out = ig.ensure_english_prompt("一隻狗\n在公園裡\n奔跑")
    assert out == "english scene 0"
    assert fake_llm.calls == 1


def test_document_builder_pretranslates_all_markers_once(monkeypatch):
    # Import in the app's own order: services.artifact_build is circular if imported first.
    pytest.importorskip("pipeline.workflow")
    artifact_build = pytest.importorskip("services.artifact_build")
    seen: list[list[str]] = []
    monkeypatch.setattr(ig, "pretranslate_prompts", lambda texts: seen.append(list(texts)))
    md = "# 報告\n\n[IMAGE_PROMPT: 一隻黃金獵犬]\n\n文字\n\n[IMAGE_PROMPT: 城市夜景]\n"
    artifact_build._pretranslate_marker_prompts(md)
    assert len(seen) == 1
    assert len(seen[0]) == 2


def _count_pipeline_evictions(monkeypatch):
    """Route _llm_translate through the REAL governor the way generate_text_sync does
    (ResourceGovernor.acquire("llm_chat")) and count how often the warm image pipeline is
    unloaded."""
    gov = pytest.importorskip("services.resource_governor")
    evictions = {"n": 0}
    monkeypatch.setattr(ig, "pipeline_is_loaded", lambda: True)

    def _unload():
        evictions["n"] += 1

    monkeypatch.setattr(ig, "unload_pipeline", _unload)
    monkeypatch.setattr(gov.ResourceGovernor, "_clear_torch_cache", classmethod(lambda cls: None))
    monkeypatch.setattr(gov.ResourceGovernor, "_needs_gpu_serialization", classmethod(lambda cls: False))

    def _translate(messages):
        with gov.ResourceGovernor.acquire("llm_chat"):
            lines = _numbered_lines(messages[-1]["content"])
            if not lines:  # plain single-prompt format
                return "english scene 0"
            return "\n".join(f"{i + 1}. english scene {i}" for i, _ in enumerate(lines))

    monkeypatch.setattr(ig, "_llm_translate", _translate)
    return evictions


def test_governor_evicts_image_pipeline_once_per_batch_not_per_slide(monkeypatch):
    evictions = _count_pipeline_evictions(monkeypatch)
    ig.pretranslate_prompts(ZH_DESCS + [ZH_QUERY[:120]])
    for desc in ZH_DESCS:
        ig.ensure_english_prompt(ig.prepare_image_prompt(f"{desc}. Presentation slide visual. {ZH_QUERY[:120]}"))
    assert evictions["n"] == 1


def test_control_governor_evicts_per_slide_without_pretranslate(monkeypatch):
    evictions = _count_pipeline_evictions(monkeypatch)
    for desc in ZH_DESCS:
        ig.ensure_english_prompt(desc)
    assert evictions["n"] == len(ZH_DESCS)


def _capture_photo_prompt(monkeypatch, description, query):
    pytest.importorskip("pipeline.workflow")
    from pipeline.deliverables import presentation_compile as pc

    captured = {}

    def fake_resolve(desc, **kwargs):
        captured["photo_prompt"] = kwargs["photo_prompt"]
        return None, []

    monkeypatch.setattr("services.marker_visual.resolve_marker_visual", fake_resolve)
    monkeypatch.setattr("services.model_router.image_generation_deps_available", lambda: (True, ""))
    pc._resolve_slide_image(description, 1, query, prof={}, model="m", log_fn=None)
    return captured["photo_prompt"]


def test_slide_photo_prompt_drops_untranslatable_query_instead_of_sending_chinese(monkeypatch):
    """Live deck: the planner wrote English image descriptions, only the Chinese query slice needed
    translating, and translating an imperative sentence alone failed -> every slide called the LLM."""
    calls = FakeLLM(reply=lambda lines: "sorry")  # the model can't translate the query
    monkeypatch.setattr(ig, "_llm_translate", calls)
    prompt = _capture_photo_prompt(monkeypatch, "a bustling night market at dusk", ZH_QUERY)
    assert "night market" in prompt
    assert not ig._needs_english(prompt), prompt
    n = calls.calls
    for _ in range(3):  # later slides carry the identical query slice: not re-asked
        _capture_photo_prompt(monkeypatch, "a quiet harbour at dawn", ZH_QUERY)
    assert calls.calls == n


def test_slide_photo_prompt_uses_the_pretranslated_query(monkeypatch):
    monkeypatch.setattr(ig, "_llm_translate", FakeLLM())
    ig.pretranslate_prompts([ZH_QUERY[:120]])
    calls = FakeLLM()
    monkeypatch.setattr(ig, "_llm_translate", calls)
    prompt = _capture_photo_prompt(monkeypatch, "a bustling night market at dusk", ZH_QUERY)
    assert "english scene 0" in prompt
    assert calls.calls == 0


@pytest.mark.parametrize(
    "reply", ["sorry", "Sorry, I can't translate that.", "Sure! Here's the translation: a dog", "I cannot help with that", "Translation: a dog"]
)
def test_model_chatter_is_not_accepted_as_a_translation(monkeypatch, reply):
    monkeypatch.setattr(ig, "_llm_translate", lambda m: reply)
    assert ig.ensure_english_prompt("一隻狗在公園奔跑") == "一隻狗在公園奔跑"


def test_absurdly_long_reply_is_rejected(monkeypatch):
    monkeypatch.setattr(ig, "_llm_translate", lambda m: "a dog " * 300)
    assert ig.ensure_english_prompt("狗") == "狗"
