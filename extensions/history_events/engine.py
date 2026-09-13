# -*- coding: utf-8 -*-
"""History Events — single-question encounters with A–F grading."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from extensions.history_events import prompts
from extensions.history_events.encounter_backgrounds import chat_background_for
from extensions.history_events.encounters import (
    HistoricalEncounter,
    normalize_era_bucket,
    pick_encounter,
)
from extensions.history_events.images import resolve_encounter_image
from extensions.ludicity_shared.content_locale import localize_history_encounter
from extensions.ludicity_shared.chat_flow import (
    begin_stream_bubble,
    finalize_bubble,
    post_assistant_message,
    post_player_line,
    stream_bubble,
)
from extensions.ludicity_shared.llm import ludicity_chat, ludicity_chat_stream
from extensions.ludicity_shared.narrative_budget import narrative_llm_options
from extensions.ludicity_shared.parse import clean_display_text, parse_tagged_sections

from pipeline.i18n import t as tr

_VALID_GRADES = frozenset("ABCDEF")
_GRADE_ORDER = "ABCDEF"
_MAX_SEEN = 60
_SCORE_KEYS = ("engagement", "reasoning", "factors", "historical_fit", "total")
_CRITERIA_KEYS = ("engagement", "reasoning", "factors", "historical_fit")
_DISMISSIVE = frozenset(
    {
        "nothing",
        "none",
        "n/a",
        "na",
        "idk",
        "dunno",
        "no idea",
        "pass",
        "skip",
        "whatever",
        "dont know",
        "don't know",
    }
)


@dataclass
class HistoryEventsState:
    busy: bool = False
    era_filter: str = ""
    encounter: HistoricalEncounter | None = None
    image_path: str = ""
    answered: bool = False
    grade: str = ""
    feedback: str = ""
    score_breakdown: dict[str, int] = field(default_factory=dict)
    seen_ids: list[str] = field(default_factory=list)


_state = HistoryEventsState()


def get_state() -> HistoryEventsState:
    return _state


def _remember_encounter(encounter_id: str) -> None:
    seen = _state.seen_ids
    if encounter_id in seen:
        seen.remove(encounter_id)
    seen.append(encounter_id)
    if len(seen) > _MAX_SEEN:
        del seen[: len(seen) - _MAX_SEEN]


def _expand_setup(loc: dict[str, Any]) -> str:
    """The bundled setup line is deliberately terse (feeds the grading prompt too), but
    that leaves readers with too little scene context to actually engage with the
    question. Ask the model to expand it into a fuller scene — strictly from the given
    facts, no invented specifics — for display only; the grading prompt still uses the
    original terse enc.setup, not this expansion."""
    factors = ", ".join(loc["factors"])
    system = (
        "Expand a terse historical scene-setting line into a fuller, vivid paragraph "
        "for a reader who is about to answer a decision-point question.\n"
        "STRICT RULES:\n"
        "- Use ONLY the facts given below — do not invent names, numbers, dates, quotes, "
        "or events not stated.\n"
        "- 3-5 sentences, ~120-180 words. Prose, not a list.\n"
        "- Present tense, scene-setting tone. End right at the decision point — do not "
        "reveal or hint at what happens next.\n"
        "- Output ONLY the expanded paragraph, no heading, no preamble."
    )
    user = (
        f"Title: {loc['title']}\n"
        f"Era/region/category: {loc['era']} · {loc['region']} · {loc['category']}\n"
        f"Background: {loc['background']}\n"
        f"Terse situation line to expand: {loc['setup']}\n"
        f"Known pressures/factors in play: {factors}\n"
        f"Question the reader must answer: {loc['question']}"
    )
    try:
        expanded = ludicity_chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            extra_options=narrative_llm_options(extension="history_events"),
        ).strip()
    except Exception:
        return loc["setup"]
    return expanded or loc["setup"]


def format_encounter_post(enc: HistoricalEncounter) -> str:
    loc = localize_history_encounter(enc)
    pressures = "\n".join(f"- {f}" for f in loc["factors"])
    situation = _expand_setup(loc)
    return (
        f"**{tr('history_events.chat_title')}**\n\n"
        f"**{loc['title']}**\n"
        f"_{loc['era']} · {loc['region']} · {loc['category']}_\n\n"
        f"### {tr('history_events.section_background')}\n\n{loc['background']}\n\n"
        f"### {tr('history_events.section_situation')}\n\n{situation}\n\n"
        f"### {tr('history_events.section_pressures')}\n\n{pressures}\n\n"
        f"### {tr('history_events.section_question')}\n\n{loc['question']}\n\n"
        f"{tr('history_events.no_spoiler_hint')}"
    )


_CRITERIA_KEYS_I18N = {
    "engagement": "history_events.criterion_engagement",
    "reasoning": "history_events.criterion_reasoning",
    "factors": "history_events.criterion_factors",
    "historical_fit": "history_events.criterion_fit",
}
def format_score_lines(scores: dict[str, int], *, grade: str = "") -> str:
    scores = finalize_scores(scores)
    lines: list[str] = []
    if grade:
        lines.append(tr("history_events.grade", grade=grade))
    for key in _CRITERIA_KEYS:
        if key in scores:
            label = tr(_CRITERIA_KEYS_I18N[key])
            lines.append(tr("history_events.criterion_score", label=label, score=scores[key]))
    if "total" in scores:
        lines.append(tr("history_events.total", total=scores["total"]))
    return "\n\n".join(lines)


_MAX_TOTAL_BY_CEILING = {"F": 15, "E": 35, "D": 50, "C": 65, "B": 80, "A": 100}


def cap_scores_for_answer(answer: str, scores: dict[str, int]) -> dict[str, int]:
    """Clamp LLM scores to match answer quality caps (dismissive answers stay very low)."""
    scores = finalize_scores(scores)
    text = (answer or "").strip().lower()
    if not _word_count(answer) or text in _DISMISSIVE:
        return {
            "engagement": 5,
            "reasoning": 0,
            "factors": 0,
            "historical_fit": 0,
            "total": 5,
        }

    ceiling = _grade_ceiling(answer)
    max_total = _MAX_TOTAL_BY_CEILING.get(ceiling, 100)
    total = scores.get("total", 0)
    if total <= max_total:
        return scores

    ratio = max_total / total if total > 0 else 0.0
    out: dict[str, int] = {}
    for key in _CRITERIA_KEYS:
        out[key] = max(0, min(25, int(round(scores.get(key, 0) * ratio))))
    return finalize_scores(out)


def finalize_scores(scores: dict[str, int]) -> dict[str, int]:
    """Recompute total as the sum of the four criteria (each 0–25)."""
    out = {k: scores[k] for k in _CRITERIA_KEYS if k in scores}
    for k in _CRITERIA_KEYS:
        if k in out:
            out[k] = max(0, min(25, int(out[k])))
    if out:
        out["total"] = max(0, min(100, sum(out.get(k, 0) for k in _CRITERIA_KEYS)))
    elif "total" in scores:
        out["total"] = max(0, min(100, int(scores["total"])))
    return out


def _word_count(answer: str) -> int:
    return len([w for w in re.split(r"\s+", (answer or "").strip()) if w])


def _grade_ceiling(answer: str) -> str:
    text = (answer or "").strip().lower()
    words = _word_count(text)
    if not words or text in _DISMISSIVE:
        return "F"
    if words <= 4:
        return "E"
    if words < 12:
        return "D"
    if words < 25:
        return "C"
    if words < 45:
        return "B"
    return "A"


def _worse_grade(a: str, b: str) -> str:
    ia = _GRADE_ORDER.index(a) if a in _GRADE_ORDER else 5
    ib = _GRADE_ORDER.index(b) if b in _GRADE_ORDER else 5
    return _GRADE_ORDER[max(ia, ib)]


def _parse_dimension_scores(dimensions_block: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for key in _SCORE_KEYS:
        m = re.search(rf"{key}\s*:\s*(\d+)", dimensions_block or "", re.I)
        if m:
            val = int(m.group(1))
            cap = 100 if key == "total" else 25
            scores[key] = max(0, min(cap, val))
    return scores


def _grade_from_total(total: int) -> str:
    if total >= 81:
        return "A"
    if total >= 66:
        return "B"
    if total >= 51:
        return "C"
    if total >= 36:
        return "D"
    if total >= 16:
        return "E"
    return "F"


_NUMBERED_ITEM_RE = re.compile(r"(?<=[.!?)\"'。！？])\s+(?=[1-9]\d?\.\s)")


def _split_numbered_feedback(text: str) -> str:
    """Force each numbered feedback point onto its own paragraph.

    The grading prompt asks the model for "1. ... 2. ... 3. ..." as separate points, but
    models don't reliably add the blank line between them — without it, markdown renders
    the whole thing as one run-on paragraph instead of a list. Insert the break wherever a
    numbered marker follows another point directly on the same line."""
    return _NUMBERED_ITEM_RE.sub("\n\n", text or "")


def _parse_grade(raw: str, *, answer: str) -> tuple[str, str, dict[str, int]]:
    parts = parse_tagged_sections(raw, "DIMENSIONS", "GRADE", "FEEDBACK")
    dims = parts.get("DIMENSIONS") or ""
    scores = cap_scores_for_answer(answer, _parse_dimension_scores(dims))

    grade = (parts.get("GRADE") or "").strip().upper()
    m = re.search(r"\b([A-F])\b", grade)
    llm_grade = m.group(1) if m and m.group(1) in _VALID_GRADES else "C"

    total = scores.get("total", 0)
    score_grade = _grade_from_total(total)
    merged = _worse_grade(llm_grade, score_grade)

    ceiling = _grade_ceiling(answer)
    final = _worse_grade(merged, ceiling)
    feedback = clean_display_text((parts.get("FEEDBACK") or raw).strip())
    feedback = _split_numbered_feedback(feedback)
    return final, feedback, scores


def _grade_messages(enc: HistoricalEncounter, answer: str) -> list[dict[str, str]]:
    factors = "\n".join(f"- {f}" for f in enc.key_factors)
    wc = _word_count(answer)
    user = (
        f"Encounter: {enc.title} ({enc.era})\n\n"
        f"Background and situation shown to student:\n{chat_background_for(enc)}\n\n{enc.setup}\n\n"
        f"Question:\n{enc.question}\n\n"
        f"Historical context (grading only):\n{enc.historical_context}\n\n"
        f"What actually happened:\n{enc.what_happened}\n\n"
        f"Key factors a strong answer should consider:\n{factors}\n\n"
        f"Student answer ({wc} words):\n{answer}\n\n"
        f"{prompts.GRADE_FMT}"
    )
    return [
        {"role": "system", "content": prompts.SYSTEM},
        {"role": "user", "content": user},
    ]


def start_encounter(state: HistoryEventsState, *, era_bucket: str = "") -> None:
    bucket = normalize_era_bucket(era_bucket or state.era_filter)
    state.era_filter = era_bucket or state.era_filter
    exclude = set(state.seen_ids)
    enc = pick_encounter(exclude=exclude, era_bucket=bucket)
    state.encounter = enc
    state.answered = False
    state.grade = ""
    state.feedback = ""
    state.score_breakdown = {}
    state.image_path = resolve_encounter_image(enc)
    _remember_encounter(enc.id)
    post_assistant_message(format_encounter_post(enc))


def submit_answer(state: HistoryEventsState, *, answer: str) -> None:
    enc = state.encounter
    if not enc:
        raise ValueError("Start an encounter first.")
    if state.answered:
        raise ValueError("Already graded — start a new encounter.")
    text = (answer or "").strip()
    if not text:
        raise ValueError("Write your answer first.")

    post_player_line(text, label=tr("history_events.player_answer_label"))
    header = f"**{tr('history_events.grading_header')}**"
    placeholder = tr("history_events.grading_placeholder")
    begin_stream_bubble(header)
    raw = ludicity_chat_stream(
        _grade_messages(enc, text),
        on_chunk=lambda _p: stream_bubble(header, placeholder),
        extra_options=narrative_llm_options(extension="history_events"),
    )
    grade, feedback, scores = _parse_grade(raw, answer=text)
    state.grade = grade
    state.feedback = feedback
    state.score_breakdown = dict(scores)
    state.answered = True
    score_block = format_score_lines(scores, grade=grade)
    body = f"{score_block}\n\n{feedback}"
    finalize_bubble(header, body)


def state_to_ui_dict(state: HistoryEventsState) -> dict[str, Any]:
    enc = state.encounter
    return {
        "busy": state.busy,
        "image_path": state.image_path,
        "answered": state.answered,
        "grade": state.grade,
        "score_breakdown": dict(state.score_breakdown),
        "era_filter": state.era_filter,
        "encounter_title": enc.title if enc else "",
        "encounter_era": enc.era if enc else "",
        "encounter_category": enc.category if enc else "",
        "has_encounter": enc is not None,
    }
