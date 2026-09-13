# -*- coding: utf-8 -*-
"""Query planner: deterministic fallback + plan finalization (guardrails, source
strategy, deliverable pipelines). The LLM planner itself lives in planner_worker.py."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from pipeline.output_format import (
    DEFAULT_OUTPUT_FORMAT,
    infer_format_from_query,
    normalize_output_type,
)
from pipeline.direct.output_constraints import default_constraint_id
from pipeline.direct.task_roles import _ROLES as TASK_ROLES
from pipeline.direct.express_lane import can_use_express_lane
from pipeline.schemas.task_schema import InputMetadata
import pipeline.query_intent_i18n as _qi

# Word/phrase lists formerly here (translate/summarize/rewrite vocabulary) now live in
# pipeline/query_intent_i18n.py's CONCEPTS — one shared, multilingual source. See
# CLAUDE.md section 8. Structural regexes (verb-and-verb compounds, "each source",
# language-pair, slide-number references) keep their English grammar-shaped pattern and
# gain a concept-matcher fallback for other locales via the wrapper classes below.
_MULTI_STEP_SEP_EN = re.compile(
    r"\s*(?:,?\s*(?:then|and then|after that|next)\s+|\s*→\s*|\s*->\s*)",
    re.I,
)
_MULTI_STEP_SEP_OTHER = re.compile(
    r"\s*(?:,?\s*(?:然後|然后|接著|接着|之後|之后|luego|despu[eé]s|a continuaci[oó]n|dann|danach|anschlie[ßs]end)\s+)",
    re.I,
)


class _MultiStepSepRE:
    def split(self, text: str):
        parts = _MULTI_STEP_SEP_EN.split(text)
        out: list[str] = []
        for part in parts:
            out.extend(_MULTI_STEP_SEP_OTHER.split(part))
        return out


_MULTI_STEP_SEP = _MultiStepSepRE()

_COMPOUND_AND_EN = re.compile(
    r"^(.+?\b(?:summarize|summarise|summar\w*|translate|transcri\w*|rewrite|rephrase|"
    r"convert|localize|localise|edit|polish|fix|output)\b)\s+and\s+(.+)$",
    re.I,
)


class _CompoundAndRE:
    def match(self, text: str):
        return _COMPOUND_AND_EN.match(text)


_COMPOUND_AND = _CompoundAndRE()

_TASK_VERBS_EN = re.compile(
    r"\b(summarize|summarise|summar\w*|translate|transcri\w*|rewrite|rephrase|"
    r"convert|localize|localise|edit|polish|fix|output|bullet)\b",
    re.I,
)
_TASK_VERB_CONCEPTS = ("verb_translate", "verb_summarize", "verb_transcribe", "verb_rewrite")


class _TaskVerbsRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import any_matches

        return _TASK_VERBS_EN.search(text) or (any_matches(text, _TASK_VERB_CONCEPTS) or None)


_TASK_VERBS = _TaskVerbsRE()

_LANG_PAIR_EN = re.compile(
    r"\b(chinese|english|spanish|french|german|mandarin|cantonese|japanese|korean)"
    r"\s+(?:to|into)\s+"
    r"(chinese|english|spanish|french|german|mandarin|cantonese|japanese|korean)\b"
    r"|翻譯成|翻译成|譯成|译成|翻成",
    re.I,
)


class _LangPairRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import find_language_target, matches

        if _LANG_PAIR_EN.search(text):
            return True
        return (matches(text, "verb_translate") and find_language_target(text) is not None) or None


_LANG_PAIR = _LangPairRE()

_PER_SOURCE_SUMMARY_EN = re.compile(
    r"\b(?:each|every|per|for\s+each)\s+(?:source|document|file|upload|input)s?\b"
    r"|\beach\s+input\b"
    r"|summary\s+(?:for|of)\s+each\b",
    re.I,
)


class _PerSourceSummaryRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        return _PER_SOURCE_SUMMARY_EN.search(text) or (matches(text, "per_source") or None)


_PER_SOURCE_SUMMARY = _PerSourceSummaryRE()

_EXTRACT_EACH_EN = re.compile(
    r"\b(?:extract|main points?|key points?)\b.*\b(?:each|every|per)\s+(?:source|document|file|upload)s?\b"
    r"|\b(?:each|every|per)\s+(?:source|document|file|upload)s?\b.*\b(?:extract|main points?|key points?)\b",
    re.I,
)


class _ExtractEachRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        if _EXTRACT_EACH_EN.search(text):
            return True
        return (matches(text, "verb_extract") and matches(text, "per_source")) or None


_EXTRACT_EACH = _ExtractEachRE()

_COMBINED_SUMMARY_AFTER_EACH_EN = re.compile(
    r"\b(?:produce|create|write|generate|make)\s+(?:a\s+)?summary\b"
    r"|\b(?:one|single|combined|overall|unified)\s+summary\b"
    r"|\bsummary\s+(?:from|of)\s+(?:both|all)\b",
    re.I,
)


class _CombinedSummaryAfterEachRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        if _COMBINED_SUMMARY_AFTER_EACH_EN.search(text):
            return True
        return (matches(text, "verb_summarize") and matches(text, "combined_summary")) or None


_COMBINED_SUMMARY_AFTER_EACH = _CombinedSummaryAfterEachRE()

_COMBINED_SUMMARY_EN = re.compile(
    r"\b(?:combined|single|one|overall|together|all\s+sources?|across\s+all|"
    r"both\s+sources?|from\s+both|using\s+both|both\s+files?|both\s+inputs?|"
    r"merge|synthesi[sz]e|integrated|unified)\b"
    r"|\b(?:write|create|generate|prepare)\s+(?:a\s+)?report\b"
    r"|\breport\s+(?:on|from|using)\b",
    re.I,
)


class _CombinedSummaryRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        if _COMBINED_SUMMARY_EN.search(text):
            return True
        return (matches(text, "combined_summary") or matches(text, "verb_analyze")) or None


_COMBINED_SUMMARY = _CombinedSummaryRE()

_SLIDE_REF_EN = re.compile(r"\bslide\s+(\d+)\b", re.I)
_SLIDE_REF_OTHER = re.compile(r"(?:投影片|幻灯片|diapositiva|folie)\s*(\d+)", re.I)


class _SlideRefRE:
    def finditer(self, text: str):
        import itertools

        return itertools.chain(_SLIDE_REF_EN.finditer(text or ""), _SLIDE_REF_OTHER.finditer(text or ""))


_SLIDE_REF = _SlideRefRE()

_VALID_ROLES = frozenset(TASK_ROLES.keys())


@dataclass
class StepScope:
    slides: list[int] = field(default_factory=list)
    sheets: list[int] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    target_language: str = ""
    selection: bool = False
    units: str = "auto"


@dataclass
class PlannedStep:
    intent: str
    roles: list[str]
    output_constraint_id: str
    scope: StepScope = field(default_factory=StepScope)


@dataclass
class DirectPlan:
    express: bool = False
    step_count: int = 1
    mode: str = "generation"
    output_type: str = "chat"
    steps: list[PlannedStep] = field(default_factory=list)
    source_strategy: str = "auto"


def resolve_output_type_for_direct(
    query: str,
    preferred_ui: str | None,
    profile: dict | None = None,
) -> str:
    """Priority: query explicit > UI dropdown > profile > chat."""
    explicit = infer_format_from_query(query)
    if explicit:
        return explicit
    pref = normalize_output_type(preferred_ui)
    if pref != DEFAULT_OUTPUT_FORMAT:
        return pref
    prof = profile or {}
    out_cfg = prof.get("OUTPUT") or {}
    for key in ("deliverable_type", "default_output_format", "output_type"):
        val = out_cfg.get(key)
        if val and normalize_output_type(str(val)) != DEFAULT_OUTPUT_FORMAT:
            return normalize_output_type(str(val))
    return DEFAULT_OUTPUT_FORMAT


_IN_PLACE_HINTS_EN = (
    "in place", "in the file", "in the document", "in the pptx", "in the docx",
    "in the spreadsheet", "uploaded file", "same file", "keep format", "mutate",
    "only slide", "only paragraph", "only chinese", "only english", "only translate",
    "translate only", "just translate", "edit the", "edit this", "update slide",
    "update cell",
)
_IN_PLACE_HINTS_OTHER = (
    "原地", "在檔案中", "在文件中", "在文档中", "上傳的檔案", "上传的文件", "只改投影片",
    "只翻譯", "只翻译", "en el lugar", "en el archivo", "en el documento",
    "archivo subido", "solo traducir", "an ort und stelle", "in der datei",
    "im dokument", "hochgeladene datei", "nur übersetzen", "nur uebersetzen",
)


def _wants_in_place_file_edit(query: str) -> bool:
    lower = (query or "").lower()
    return any(h in lower for h in _IN_PLACE_HINTS_EN) or any(h in lower for h in _IN_PLACE_HINTS_OTHER)


def infer_mode_for_direct(
    output_type: str,
    metadata: InputMetadata,
    query: str,
) -> str:
    """Mutation vs generation — heuristic resolver (no LLM)."""
    from pipeline.direct.mode_resolver import resolve_direct_mode

    return resolve_direct_mode(output_type, metadata, query)


def _split_intents(query: str) -> list[str]:
    q = (query or "").strip()
    if not q:
        return [""]
    parts = _MULTI_STEP_SEP.split(q)
    intents = [p.strip() for p in parts if p.strip()]
    if len(intents) > 1:
        return intents
    # N-way "and" split when every clause is its own task ("transcribe and translate and
    # summarize"). Guard: each clause must contain a task verb, so "translate to French and
    # Spanish" (Spanish has no verb) does NOT split.
    and_parts = [p.strip() for p in re.split(r"\s+and\s+", q, flags=re.I) if p.strip()]
    if len(and_parts) > 1 and all(_TASK_VERBS.search(p) for p in and_parts):
        return and_parts
    m = _COMPOUND_AND.match(q)
    if m:
        left, right = m.group(1).strip(), m.group(2).strip()
        if _TASK_VERBS.search(left) and _TASK_VERBS.search(right):
            return [left, right]
    lower = q.lower()
    if " and " in lower:
        idx = lower.find(" and ")
        left, right = q[:idx].strip(), q[idx + 5 :].strip()
        if _TASK_VERBS.search(left) and _TASK_VERBS.search(right):
            return [left, right]
    return [q]


def _infer_roles(intent: str, *, has_charts: bool = False, mode: str = "generation") -> list[str]:
    lower = (intent or "").lower()
    if mode == "mutation":
        if _qi.matches(lower, "verb_translate") or _LANG_PAIR.search(lower):
            from services.selective_translate import has_explicit_source_lang_pair

            if (
                _qi.matches(lower, "selective_scope")
                or has_explicit_source_lang_pair(intent)
            ):
                return ["selective_translator"]
            return ["translator"]
        if _qi.matches(lower, "verb_rewrite"):
            return ["editor"]
        return ["editor"]
    if has_charts and _qi.matches(lower, "verb_analyze"):
        return ["data_analyst"]
    if _qi.matches(lower, "verb_translate"):
        if _qi.matches(lower, "selective_scope"):
            return ["selective_translator"]
        return ["translator"]
    if _qi.matches(lower, "verb_summarize"):
        return ["summarizer"]
    if "bullet" in lower:
        return ["bullet_formatter"]
    if _qi.matches(lower, "verb_rewrite"):
        if _qi.matches(lower, "tone_formal") or _qi.matches(lower, "tone_casual"):
            return ["tone_rewriter"]
        return ["editor"]
    if _qi.matches(lower, "verb_outline"):
        return ["outliner"]
    if _qi.matches(lower, "verb_extract"):
        return ["extractor"]
    if _qi.matches(lower, "verb_write_author"):
        return ["writer"]
    if _qi.matches(lower, "presentation_format_hint"):
        if _qi.matches(lower, "verb_outline"):
            return ["deck_planner"]
        return ["slide_author"]
    if _qi.matches(lower, "image_deliverable_hints") or _qi.matches(lower, "wants_images"):
        return ["image_prompt_author"]
    if _qi.matches(lower, "verb_transcribe"):
        return ["transcriber"]
    if re.search(r"\boutput\s+(?:as|in)\b", lower):
        return ["translator"]
    return ["general_answer"]


def _parse_scope(intent: str, metadata: InputMetadata) -> StepScope:
    scope = StepScope()
    if metadata.has_valid_preview_selection:
        scope.selection = True
    for m in _SLIDE_REF.finditer(intent or ""):
        try:
            scope.slides.append(int(m.group(1)))
        except ValueError:
            pass
    col_m = re.search(r"\bcolumn\s+([A-Z]+)\b", intent or "", re.I)
    if col_m:
        scope.columns = [col_m.group(1).upper()]
    sheet_m = re.search(r"\bsheet\s+(\d+)\b", intent or "", re.I)
    if sheet_m:
        try:
            scope.sheets = [int(sheet_m.group(1))]
        except ValueError:
            pass
    return scope


def _normalize_roles(role_ids: list[str]) -> list[str]:
    out: list[str] = []
    for rid in role_ids or []:
        key = (rid or "").strip().lower()
        if key in _VALID_ROLES and key not in out:
            out.append(key)
    return out or ["general_answer"]


def _build_step(
    intent: str,
    output_type: str,
    mode: str,
    metadata: InputMetadata,
    *,
    has_charts: bool = False,
    roles: list[str] | None = None,
    constraint_id: str | None = None,
) -> PlannedStep:
    rids = _normalize_roles(roles or _infer_roles(intent, has_charts=has_charts, mode=mode))
    cid = constraint_id or default_constraint_id(output_type, mode, rids[0])
    return PlannedStep(
        intent=intent,
        roles=rids,
        output_constraint_id=cid,
        scope=_parse_scope(intent, metadata),
    )


def plan_direct_query_fallback(
    metadata: InputMetadata,
    *,
    profile: dict | None = None,
    has_charts: bool = False,
    bundle: Any | None = None,
) -> DirectPlan:
    """Deterministic fallback when LLM planner unavailable."""
    query = (metadata.query or "").strip()
    intents = _split_intents(query)
    output_type = resolve_output_type_for_direct(
        query, metadata.preferred_output_format, profile
    )
    mode = infer_mode_for_direct(output_type, metadata, query)
    steps = [
        _build_step(intent, output_type, mode, metadata, has_charts=has_charts and i == 0)
        for i, intent in enumerate(intents)
    ]
    if len(steps) > 1 and mode == "generation":
        for i, step in enumerate(steps[:-1]):
            if step.roles == ["general_answer"]:
                steps[i] = _build_step(
                    step.intent,
                    "chat",
                    "generation",
                    metadata,
                    roles=["summarizer"] if "summar" in step.intent.lower() else ["writer"],
                )
    return DirectPlan(
        express=False,
        step_count=len(steps),
        mode=mode,
        output_type=output_type,
        steps=steps,
    )


def _wants_extract_each_then_combine(query: str, digest_count: int) -> bool:
    """Extract per file, then one combined summary (optionally translate) — not per-file outputs."""
    q = (query or "").strip()
    if not q or digest_count < 2:
        return False
    if not _EXTRACT_EACH.search(q):
        return False
    if _COMBINED_SUMMARY_AFTER_EACH.search(q):
        return True
    if re.search(r"\bsummary\b", q, re.I) and not re.search(
        r"\bsummary\s+(?:for|of)\s+each\b", q, re.I
    ):
        return True
    return False


def _wants_combined_summary(query: str, digest_count: int = 0) -> bool:
    q = (query or "").strip()
    if not q:
        return False
    if _PER_SOURCE_SUMMARY.search(q):
        return False
    if _COMBINED_SUMMARY.search(q):
        return True
    if digest_count >= 2 and re.search(
        r"\b(?:report|synthesis|synthesise|synthesize|compare|contrast|combine)\b",
        q,
        re.I,
    ):
        return True
    return False


def _wants_per_source_summary(query: str, digest_count: int) -> bool:
    q = (query or "").strip()
    if not q or digest_count < 2:
        return False
    if _wants_extract_each_then_combine(query, digest_count):
        return False
    if _wants_combined_summary(query, digest_count):
        return False
    return bool(_PER_SOURCE_SUMMARY.search(q))


def _expand_per_source_steps(
    plan: DirectPlan,
    bundle: Any,
    metadata: InputMetadata,
    *,
    profile: dict | None = None,
    has_charts: bool = False,
) -> None:
    """Deprecated alias — use planner_worker.apply_source_strategy_steps."""
    from pipeline.direct.planner_worker import apply_source_strategy_steps

    apply_source_strategy_steps(plan, bundle, metadata)


def _force_per_source_summary_plan(
    plan: DirectPlan,
    bundle: Any,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    if plan.express or plan.mode == "mutation" or plan.output_type != "chat":
        return
    plan.source_strategy = "per_source"
    from pipeline.direct.planner_worker import apply_source_strategy_steps

    apply_source_strategy_steps(plan, bundle, metadata, log_fn=log_fn)


def _force_combined_multi_source_plan(
    plan: DirectPlan,
    bundle: Any,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    plan.source_strategy = "combined"
    plan.mode = "generation"
    from pipeline.direct.planner_worker import apply_source_strategy_steps

    apply_source_strategy_steps(plan, bundle, metadata, log_fn=log_fn)


def _enforce_output_preference(
    plan: DirectPlan,
    metadata: InputMetadata,
    profile: dict | None,
    *,
    has_charts: bool = False,
    log_fn=None,
) -> bool:
    """Honor query > UI > profile > chat; never infer deliverable from attachments alone."""
    from pipeline.output_format import infer_format_from_query

    previous = plan.output_type
    explicit = infer_format_from_query(metadata.query or "")
    if explicit:
        plan.output_type = explicit
    else:
        plan.output_type = resolve_output_type_for_direct(
            metadata.query or "",
            metadata.preferred_output_format,
            profile,
        )

    changed = plan.output_type != previous
    if changed and log_fn:
        log_fn(f"Output type corrected: {previous} → {plan.output_type} (query/UI priority)")

    if plan.output_type == "chat":
        prev_mode = plan.mode
        plan.mode = "generation"
        if changed or previous != "chat" or prev_mode == "mutation":
            intents = _split_intents(metadata.query or "")
            plan.steps = [
                _build_step(
                    intent,
                    "chat",
                    "generation",
                    metadata,
                    has_charts=has_charts and i == 0,
                )
                for i, intent in enumerate(intents)
            ]
            plan.step_count = len(plan.steps) or 1
    return changed


def _metadata_has_spreadsheet(metadata: InputMetadata) -> bool:
    for f in metadata.files or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("name") or f.get("filename") or "").lower()
        ftype = (f.get("type") or "").lower()
        if ftype == "spreadsheet" or name.endswith((".xlsx", ".xls", ".csv")):
            return True
    return False


def _translate_roles_for_query(query: str) -> list[str]:
    q = (query or "").strip()
    from services.selective_translate import has_explicit_source_lang_pair

    if (
        re.search(r"\bonly\b", q, re.I)
        or re.search(r"\bjust\b", q, re.I)
        or has_explicit_source_lang_pair(q)
    ):
        return ["selective_translator"]
    return ["translator"]


def _multi_source_text_count(metadata: InputMetadata, bundle: Any | None = None) -> int:
    n = metadata.file_count or 0
    if bundle is not None:
        from pipeline.direct.planner_worker import _text_digests

        n = max(n, len(_text_digests(bundle)))
    link_n = metadata.link_count or 0
    if link_n and n < 2:
        n = max(n, 1 + link_n)
    return n


def _is_extract_combine_plan(plan: DirectPlan) -> bool:
    roles = [(s.roles[0] if s.roles else "") for s in (plan.steps or [])]
    if len(roles) < 3:
        return False
    extractors = sum(1 for r in roles if r == "extractor")
    if extractors < 2:
        return False
    if "synthesizer" not in roles:
        return False
    return roles[-1] in ("synthesizer", "translator")


def _is_per_source_summarizer_plan(plan: DirectPlan) -> bool:
    roles = [(s.roles[0] if s.roles else "") for s in (plan.steps or [])]
    return len(roles) >= 2 and all(r == "summarizer" for r in roles)


def _ensure_final_synthesizer_step(
    plan: DirectPlan,
    metadata: InputMetadata,
    query: str,
) -> None:
    if not plan.steps:
        return
    last = plan.steps[-1]
    plan.steps[-1] = _build_step(
        query,
        plan.output_type,
        plan.mode,
        metadata,
        roles=["synthesizer"],
        constraint_id=last.output_constraint_id,
    )
    plan.step_count = len(plan.steps)


def _enforce_document_transform_plan(
    plan: DirectPlan,
    metadata: InputMetadata,
    *,
    log_fn=None,
    bundle: Any | None = None,
) -> None:
    """Whole-document translate/summarize → generation + correct role; partial edits → mutation."""
    if plan.express:
        return
    q = (metadata.query or "").strip()
    lower = q.lower()
    is_translate = _qi.matches(lower, "verb_translate") or bool(_LANG_PAIR.search(lower))
    is_summarize = _qi.matches(lower, "verb_summarize")
    if not is_translate and not is_summarize:
        return

    partial = metadata.has_valid_preview_selection or _wants_in_place_file_edit(q)
    target_mode = "mutation" if partial else "generation"
    if plan.mode != target_mode:
        if log_fn:
            log_fn(f"Plan: document task mode corrected {plan.mode} → {target_mode}")
        plan.mode = target_mode

    multi_source = _multi_source_text_count(metadata, bundle) >= 2
    if multi_source and plan.mode == "generation" and not partial:
        if _is_extract_combine_plan(plan):
            if log_fn:
                log_fn(f"Plan: extract each → summarize → translate ({plan.step_count} steps)")
            return
        if _is_per_source_summarizer_plan(plan):
            if log_fn:
                log_fn(f"Plan: per-source summarize ({plan.step_count} steps)")
            return
        if len(plan.steps) >= 2:
            _ensure_final_synthesizer_step(plan, metadata, q)
            if log_fn:
                if is_translate and is_summarize:
                    log_fn(f"Plan: summarize + translate ({plan.step_count} steps)")
                elif is_translate:
                    log_fn(f"Plan: multi-source translate ({plan.step_count} steps)")
                else:
                    log_fn(f"Plan: multi-source summarize ({plan.step_count} steps)")
            return

    if is_translate and is_summarize:
        plan.steps = [
            _build_step(q, plan.output_type, plan.mode, metadata, roles=["summarizer"]),
            _build_step(q, plan.output_type, plan.mode, metadata, roles=["translator"]),
        ]
        plan.step_count = 2
        if log_fn:
            log_fn("Plan: summarize then translate (2 steps)")
        return

    roles = _translate_roles_for_query(q) if is_translate else ["summarizer"]
    wrong_roles = frozenset(
        {
            "transcriber",
            "tone_rewriter",
            "extractor",
            "general_answer",
            "editor",
            "writer",
        }
    )
    if is_translate:
        wrong_roles = wrong_roles | frozenset({"summarizer"})
    else:
        wrong_roles = wrong_roles | frozenset({"translator", "selective_translator"})

    expected = frozenset(roles)
    has_role = any(expected & frozenset(s.roles or []) for s in plan.steps)
    needs_fix = not plan.steps or not has_role or any(
        r in wrong_roles for s in plan.steps for r in (s.roles or [])
    )
    if not needs_fix:
        return

    plan.steps = [
        _build_step(q, plan.output_type, plan.mode, metadata, roles=roles)
    ]
    plan.step_count = 1
    if log_fn:
        log_fn(f"Plan: document task → {roles[0]} ({plan.mode})")


def _apply_spreadsheet_report_plan(
    plan: DirectPlan,
    metadata: InputMetadata,
    *,
    has_charts: bool = False,
    log_fn=None,
) -> None:
    """Spreadsheet + new .docx report → analyse then write (not synthesizer summary)."""
    if plan.mode != "generation" or plan.output_type != "document":
        return
    if not _metadata_has_spreadsheet(metadata):
        return
    q = (metadata.query or "").strip()
    if not (
        re.search(r"\b(analys\w*|report|document|docx)\b", q, re.I)
        or _qi.matches(q, "verb_analyze")
        or _qi.matches(q, "document_format_hint")
    ):
        return
    wants_report = bool(
        re.search(r"\b(write|report|docx|document)\b", q, re.I)
        or _qi.matches(q, "verb_write_author")
        or _qi.matches(q, "document_format_hint")
    )
    query = q or "Produce an analysis report from the spreadsheet"
    if wants_report:
        plan.steps = [
            _build_step(
                "Analyze the uploaded spreadsheet: key metrics, trends, outliers, and insights.",
                "document",
                "generation",
                metadata,
                has_charts=has_charts,
                roles=["data_analyst"],
                constraint_id="chat_default",
            ),
            _build_step(
                query,
                "document",
                "generation",
                metadata,
                roles=["writer"],
                constraint_id="document_markdown",
            ),
        ]
        plan.step_count = 2
        if log_fn:
            log_fn("Plan: spreadsheet → document report (analyse + writer)")
    else:
        plan.steps = [
            _build_step(
                query or "Analyze the uploaded spreadsheet with figures and recommendations.",
                "document",
                "generation",
                metadata,
                has_charts=has_charts,
                roles=["data_analyst"],
                constraint_id="document_markdown",
            ),
        ]
        plan.step_count = 1
        if log_fn:
            log_fn("Plan: spreadsheet → document analysis (single step)")


def _apply_spreadsheet_query_plan(
    plan: DirectPlan,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    """Chat question with a spreadsheet attached → compute the answer, don't eyeball a text dump.

    Independent of the deliverable-only graph-analysis gate and any "analyze/chart/trend"
    keyword match — a plain "what's the average revenue?" question gets this too.
    """
    if plan.express or plan.mode != "generation" or plan.output_type != "chat":
        return
    if not _metadata_has_spreadsheet(metadata):
        return
    q = (metadata.query or "").strip()
    if not q:
        return
    lower = q.lower()
    if _qi.matches(lower, "verb_translate") or _LANG_PAIR.search(lower):
        return
    if _qi.matches(lower, "verb_summarize"):
        return
    plan.steps = [
        _build_step(
            q,
            "chat",
            "generation",
            metadata,
            roles=["spreadsheet_query"],
            constraint_id="chat_default",
        )
    ]
    plan.step_count = 1
    if log_fn:
        log_fn("Plan: spreadsheet chat question → spreadsheet_query (computed answer)")


def _finalize_plan(
    plan: DirectPlan,
    metadata: InputMetadata,
    *,
    profile: dict | None = None,
    has_charts: bool = False,
    model: str | None = None,
    log_fn=None,
    bundle: Any | None = None,
) -> DirectPlan:
    """Apply output preference, guardrails, source strategy, and deliverable templates."""
    from pipeline.direct.planner_worker import (
        apply_plan_guardrails,
        apply_source_strategy_steps,
        ensure_presentation_pipeline,
    )

    _enforce_output_preference(
        plan,
        metadata,
        profile,
        has_charts=has_charts,
        log_fn=log_fn,
    )
    if plan.express and not can_use_express_lane(metadata):
        plan = plan_direct_query_fallback(
            metadata, profile=profile, has_charts=has_charts, bundle=bundle
        )
        plan.express = False
    if not plan.express and not plan.steps:
        plan = plan_direct_query_fallback(
            metadata, profile=profile, has_charts=has_charts, bundle=bundle
        )

    apply_plan_guardrails(plan, metadata, bundle, log_fn=log_fn)

    if plan.mode == "mutation" and not plan.express:
        _sanitize_mutation_steps(plan, metadata)

    if bundle is not None and not plan.express:
        apply_source_strategy_steps(plan, bundle, metadata, log_fn=log_fn)

    if not plan.express:
        _enforce_document_transform_plan(plan, metadata, log_fn=log_fn, bundle=bundle)
        _ensure_deliverable_author_step(plan, metadata)
        _apply_intermediate_step_constraints(plan)
        _apply_spreadsheet_report_plan(
            plan, metadata, has_charts=has_charts, log_fn=log_fn
        )
        _apply_spreadsheet_query_plan(plan, metadata, log_fn=log_fn)
        ensure_presentation_pipeline(plan, metadata, log_fn=log_fn)
    return plan


def _apply_presentation_generation_plan(
    plan: DirectPlan,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    from pipeline.direct.planner_worker import append_presentation_pipeline

    append_presentation_pipeline(plan, metadata, log_fn=log_fn)


def _apply_intermediate_step_constraints(plan: DirectPlan) -> None:
    """Non-final steps use chat constraints for their role; final step keeps deliverable constraint."""
    if len(plan.steps) <= 1:
        return
    for i, step in enumerate(plan.steps[:-1]):
        role = step.roles[0] if step.roles else "general_answer"
        cid = default_constraint_id(plan.output_type or "chat", plan.mode, role)
        plan.steps[i] = PlannedStep(
            intent=step.intent,
            roles=step.roles,
            output_constraint_id=cid,
            scope=step.scope,
        )


def _is_translate_document_export(plan: DirectPlan, metadata: InputMetadata) -> bool:
    # Generation mode hit the same gap as mutation: the translator step already
    # produces the complete, correctly-batched deliverable body (batch_processor.py
    # chunks at <=2000 chars per pass) — appending a "writer" reformatting step re-runs
    # the whole document through a single unbudgeted completion capped at ~2048 output
    # tokens, silently truncating anything longer. Skip the extra step for both modes.
    if plan.mode not in ("mutation", "generation") or plan.output_type != "document":
        return False
    return bool(re.search(r"\btranslat", (metadata.query or ""), re.I))


def _ensure_deliverable_author_step(plan: DirectPlan, metadata: InputMetadata) -> None:
    """When final output is a deliverable, append author role if last step only prepared text."""
    if plan.mode == "mutation" or not plan.steps:
        return
    if _is_translate_document_export(plan, metadata):
        return
    ot = (plan.output_type or "chat").strip().lower()
    if ot not in ("document",):
        return
    last_role = (plan.steps[-1].roles[0] if plan.steps[-1].roles else "")
    if last_role in ("slide_author", "writer"):
        return
    if (
        ot == "document"
        and len(plan.steps) == 1
        and last_role == "general_answer"
        and (metadata.file_count or 0) == 0
        and (metadata.link_count or 0) == 0
        and not metadata.has_valid_preview_selection
    ):
        step = plan.steps[0]
        plan.steps[0] = _build_step(
            (step.intent or metadata.query or "").strip(),
            ot,
            "generation",
            metadata,
            roles=["writer"],
            constraint_id="document_markdown",
        )
        return
    prep_roles = frozenset(
        {
            "translator",
            "summarizer",
            "writer",
            "extractor",
            "general_answer",
            "synthesizer",
        }
    )
    if last_role not in prep_roles:
        return
    author_role = {
        "document": "writer",
    }.get(ot)
    if not author_role:
        return
    plan.steps.append(
        _build_step(
            f"Format content as {ot} deliverable",
            ot,
            "generation",
            metadata,
            roles=[author_role],
        )
    )
    plan.step_count = len(plan.steps)


def _sanitize_mutation_steps(plan: DirectPlan, metadata: InputMetadata) -> None:
    """Replace generation-style steps (transcriber/slide_author) when mode is mutation."""
    from pipeline.direct.mode_resolver import metadata_has_office_artifact

    if not metadata_has_office_artifact(metadata):
        return
    gen_roles = frozenset(
        {"transcriber", "slide_author", "deck_planner", "writer", "outliner"}
    )
    if not any(r in gen_roles for s in plan.steps for r in s.roles):
        return
    query = (metadata.query or "").strip()
    intent = query
    roles = _infer_roles(intent, mode="mutation")
    plan.steps = [
        _build_step(intent, plan.output_type, "mutation", metadata, roles=roles)
    ]
    plan.step_count = 1


def plan_direct_query(
    metadata: InputMetadata,
    *,
    profile: dict | None = None,
    has_charts: bool = False,
    model: str | None = None,
    log_fn=None,
    bundle: Any | None = None,
) -> DirectPlan:
    """LLM planner with deterministic fallback."""
    from pipeline.direct.express_lane import can_use_express_lane, should_use_image_qa_fast_path

    if should_use_image_qa_fast_path(metadata):
        if log_fn:
            log_fn("Planner: image Q&A fast path (skipping planner LLM)")
        plan = plan_direct_query_fallback(
            metadata, profile=profile, has_charts=has_charts, bundle=bundle
        )
        return _finalize_plan(
            plan,
            metadata,
            profile=profile,
            has_charts=has_charts,
            model=model,
            log_fn=log_fn,
            bundle=bundle,
        )
    if can_use_express_lane(metadata):
        return _finalize_plan(
            DirectPlan(express=True, step_count=1, mode="generation", output_type="chat", steps=[]),
            metadata,
            profile=profile,
            has_charts=has_charts,
            model=model,
            log_fn=log_fn,
            bundle=bundle,
        )
    plan: DirectPlan | None = None
    if model:
        from pipeline.direct.planner_worker import plan_with_worker

        plan = plan_with_worker(
            metadata,
            profile=profile,
            model=model,
            has_charts=has_charts,
            log_fn=log_fn,
            bundle=bundle,
        )
        if plan is None and log_fn:
            log_fn("Planner worker unavailable; using fallback.")
    if plan is None:
        plan = plan_direct_query_fallback(
            metadata, profile=profile, has_charts=has_charts, bundle=bundle
        )
    return _finalize_plan(
        plan,
        metadata,
        profile=profile,
        has_charts=has_charts,
        model=model,
        log_fn=log_fn,
        bundle=bundle,
    )


def plan_to_dict(plan: DirectPlan) -> dict[str, Any]:
    return {
        "express": plan.express,
        "step_count": plan.step_count,
        "mode": plan.mode,
        "output_type": plan.output_type,
        "source_strategy": getattr(plan, "source_strategy", "auto"),
        "steps": [
            {
                "intent": s.intent,
                "roles": s.roles,
                "output_constraint_id": s.output_constraint_id,
                "scope": {
                    "slides": s.scope.slides,
                    "sheets": s.scope.sheets,
                    "columns": s.scope.columns,
                    "selection": s.scope.selection,
                    "units": s.scope.units,
                },
            }
            for s in plan.steps
        ],
    }


def plan_from_dict(data: dict[str, Any]) -> DirectPlan:
    steps = []
    for raw in data.get("steps") or []:
        sc = raw.get("scope") or {}
        steps.append(
            PlannedStep(
                intent=str(raw.get("intent") or ""),
                roles=_normalize_roles(list(raw.get("roles") or [])),
                output_constraint_id=str(
                    raw.get("output_constraint_id") or "chat_default"
                ),
                scope=StepScope(
                    slides=list(sc.get("slides") or []),
                    sheets=list(sc.get("sheets") or []),
                    columns=list(sc.get("columns") or []),
                    languages=[],
                    target_language="",
                    selection=bool(sc.get("selection")),
                    units=str(sc.get("units") or "auto"),
                ),
            )
        )
    return DirectPlan(
        express=bool(data.get("express")),
        step_count=int(data.get("step_count") or len(steps) or 1),
        mode=str(data.get("mode") or "generation"),
        output_type=str(data.get("output_type") or "chat"),
        source_strategy=str(data.get("source_strategy") or "auto"),
        steps=steps,
    )
