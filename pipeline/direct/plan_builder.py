# -*- coding: utf-8 -*-
"""Deterministic task/delivery/contract spine for the direct pipeline.

This module is the single, pure, testable place that answers three questions for any
request, independent of each other:

1. **What task(s)** does the query ask for? (`classify_tasks`) — translate, summarize,
   analyze, extract, rewrite, transcribe, or a plain answer.
2. **What contract** does each task substance get? (`contract_for_role`) — delivery-
   independent; a translator gets the same translation contract whether the result lands
   in chat, a .docx, or feeds a slide deck. Delegates to
   `output_constraints.default_constraint_id`, which is the actual live choke point every
   plan-construction path in this package already calls — fixing contract selection there
   fixes it for every caller at once, so this function is a thin, explicit alias rather
   than a second source of truth.
3. **How do multiple sources combine** for a given task? (`decide_fanout_assemble`) —
   once per file (fanout="per_source") vs. once over everything (fanout="whole"), and
   whether multi-source/multi-step results get concatenated under headings or synthesized
   into one coherent answer.

Two real bugs motivated pulling these into one place: a translator asked to "author a
document with headings" (delivery overriding task substance) and a plain-answer role
inheriting a document contract's dataset/correlation rules and hallucinating statistics for
a source with no table at all. Both came from contract selection being keyed on output
format instead of task. See `tests/test_plan_matrix.py` for the scenario matrix this module
is verified against.
"""
from __future__ import annotations

import re
from typing import Literal

TaskKind = Literal[
    "translate",
    "summarize",
    "analyze",
    "extract",
    "rewrite",
    "transcribe",
    "answer",
    "author",
]

Fanout = Literal["whole", "per_source"]
Assemble = Literal["none", "concat", "synthesize"]

# Role vocabulary IS the task-kind vocabulary in this codebase (translator=translate,
# summarizer=summarize, data_analyst=analyze, ...). Keeping one enum instead of a second,
# parallel "TaskKind -> role_id" table avoids the two ever drifting apart.
ROLE_FOR_TASK: dict[TaskKind, str] = {
    "translate": "translator",
    "summarize": "summarizer",
    "analyze": "data_analyst",
    "extract": "extractor",
    "rewrite": "editor",
    "transcribe": "transcriber",
    "answer": "general_answer",
    "author": "writer",
}
TASK_FOR_ROLE: dict[str, TaskKind] = {v: k for k, v in ROLE_FOR_TASK.items()}

# Word/phrase lists formerly here now live in pipeline/query_intent_i18n.py's CONCEPTS
# (verb_translate, discussion_signals, verb_summarize, verb_analyze, verb_extract,
# verb_rewrite, verb_transcribe, verb_write_author, per_source, cross_source_compare) —
# one shared, multilingual source instead of a per-file English(+partial zh) tuple. See
# CLAUDE.md section 8. `_lang_pair_search`/`_per_source_search`/`_cross_source_search`
# below wrap the concept matcher with this file's original English structural regex
# (kept for its grammatical precision) as an additional signal.


def _lang_pair_search(text: str) -> bool:
    from pipeline.query_intent_i18n import find_language_target, matches

    if _LANG_PAIR_EN.search(text):
        return True
    return matches(text, "verb_translate") and find_language_target(text) is not None


_LANG_PAIR_EN = re.compile(
    r"\b(chinese|english|spanish|french|german|mandarin|cantonese|japanese|korean)"
    r"\s+(?:to|into)\s+"
    r"(chinese|english|spanish|french|german|mandarin|cantonese|japanese|korean)\b"
    r"|翻譯成|翻译成|譯成|译成|翻成",
    re.I,
)


class _LangPairRE:
    def search(self, text: str):
        return _lang_pair_search(text) or None


_LANG_PAIR = _LangPairRE()

# Fanout/assemble decision words — same intent this codebase already encodes across
# query_planner.py's `_PER_SOURCE_SUMMARY`/`_wants_combined_summary` and
# context/strategy.py's `_PER_SOURCE_EACH`/`_CROSS_SOURCE_COMPARE`, consolidated into one
# pure, testable decision instead of three call sites independently re-deriving it.
_PER_SOURCE_EN = re.compile(
    r"\b(?:each|every|per|for\s+each)\s+(?:source|document|file|upload|input)s?\b"
    r"|\beach\s+input\b",
    re.I,
)
_CROSS_SOURCE_COMPARE_EN = re.compile(
    r"\b(common|compare|comparison|contrast|difference|differences|across|"
    r"all (?:five|four|three|sources|files|inputs)|findings from|"
    r"synthesize|synthesise|between these)\b",
    re.I,
)


class _PerSourceRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        if _PER_SOURCE_EN.search(text):
            return True
        return matches(text, "per_source") or None


class _CrossSourceCompareRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        if _CROSS_SOURCE_COMPARE_EN.search(text):
            return True
        return matches(text, "cross_source_compare") or None


_PER_SOURCE = _PerSourceRE()
_CROSS_SOURCE_COMPARE = _CrossSourceCompareRE()
_TABULAR_SUFFIXES = (".csv", ".xlsx", ".xls", ".tsv")


def classify_tasks(query: str, *, has_tabular: bool = False) -> list[TaskKind]:
    """Deterministic task classification. Returns 1+ task kinds in the order the query
    implies them ("summarize both and translate" -> [summarize, translate]).

    `has_tabular` gates "analyze": a data-analysis task is only ever selected when a real
    tabular source is present, so a plain .docx can never be classified as "analyze" and
    therefore can never reach the analysis contract that talks about correlations and
    tables — the gate belongs in classification, not in the contract's wording.
    """
    q = (query or "").strip()
    if not q:
        return ["answer"]
    lower = q.lower()

    tasks: list[TaskKind] = []

    def _add(kind: TaskKind) -> None:
        if kind not in tasks:
            tasks.append(kind)

    # Compound "X and Y" where both halves name a task verb — same split query_planner.py's
    # _COMPOUND_AND already performs; kept in classification order (first verb first).
    and_match = re.search(r"^(.+?)\band\b(.+)$", q, re.I)
    if and_match:
        left, right = and_match.group(1).strip(), and_match.group(2).strip()
        left_tasks = _classify_single(left, has_tabular=has_tabular)
        right_tasks = _classify_single(right, has_tabular=has_tabular)
        if left_tasks and right_tasks and left_tasks != right_tasks:
            for t in left_tasks + right_tasks:
                _add(t)
            return tasks

    for t in _classify_single(lower, has_tabular=has_tabular):
        _add(t)
    return tasks or ["answer"]


def _classify_single(text: str, *, has_tabular: bool) -> list[TaskKind]:
    from pipeline.query_intent_i18n import matches

    lower = text.lower()
    if has_tabular and matches(lower, "verb_analyze"):
        return ["analyze"]
    wants_translation = matches(lower, "verb_translate") or bool(_LANG_PAIR.search(lower))
    if wants_translation and not matches(lower, "discussion_signals"):
        return ["translate"]
    if matches(lower, "verb_summarize"):
        return ["summarize"]
    if matches(lower, "verb_transcribe"):
        return ["transcribe"]
    if matches(lower, "verb_extract"):
        return ["extract"]
    if matches(lower, "verb_rewrite"):
        return ["rewrite"]
    if matches(lower, "verb_write_author"):
        return ["author"]
    return []


def contract_for_role(role_id: str, *, output_type: str = "chat", mode: str = "generation") -> str:
    """Task-scoped, delivery-independent contract for a role. Thin alias over
    `output_constraints.default_constraint_id` — see module docstring for why this isn't a
    second source of truth."""
    from pipeline.direct.output_constraints import default_constraint_id

    return default_constraint_id(output_type, mode, role_id)


def contract_for_task(kind: TaskKind, *, output_type: str = "chat", mode: str = "generation") -> str:
    return contract_for_role(ROLE_FOR_TASK[kind], output_type=output_type, mode=mode)


def decide_fanout_assemble(
    query: str, source_count: int, kind: TaskKind = "answer"
) -> tuple[Fanout, Assemble]:
    """How multiple sources combine for a given task. Single/zero sources are always
    "whole"/"none" — there is nothing to fan out or assemble.

    `kind` matters, not just wording: translating N documents can only ever mean translating
    each one in full and concatenating the results — there is no such thing as "one combined
    translation" of two unrelated documents, so "translate" always fans out per-source
    regardless of phrasing. Every other task defaults to "whole"/"synthesize" for an
    unqualified multi-source request, so an ambiguous phrasing combines everything into one
    coherent answer rather than silently answering from only the first file.
    """
    if source_count < 2:
        return "whole", "none"
    q = (query or "").strip()
    if _PER_SOURCE.search(q):
        return "per_source", "concat"
    if _CROSS_SOURCE_COMPARE.search(q):
        return "whole", "synthesize"
    if kind == "translate":
        return "per_source", "concat"
    return "whole", "synthesize"


def needs_tabular_source(kind: TaskKind) -> bool:
    return kind == "analyze"


def has_tabular_source(file_names: list[str]) -> bool:
    return any(
        (name or "").lower().endswith(_TABULAR_SUFFIXES) for name in file_names
    )


def needs_format_stage(output_type: str) -> bool:
    """True for delivery formats with real shape constraints (fixed slide count, per-slide
    length caps) that a flowing format like docx never has. A task step's output must be
    reflowed by a dedicated format-shaping stage before it can compile — see
    `planner_worker.ensure_presentation_pipeline`, which this predicate should stay
    consistent with."""
    return (output_type or "").strip().lower() == "presentation"
