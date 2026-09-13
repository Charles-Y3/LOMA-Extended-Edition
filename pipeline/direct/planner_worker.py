# -*- coding: utf-8 -*-
"""Unified planner worker: one LLM decides mode, steps, and multi-source strategy."""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from pipeline.output_format import infer_format_from_query, normalize_output_type
from pipeline.schemas.task_schema import InputMetadata

from pipeline.direct.express_lane import can_use_express_lane
from pipeline.direct.mode_resolver import metadata_has_office_artifact

_BUILD_STEP: Callable[..., Any] | None = None
_DIRECT_PLAN: type | None = None
_PLANNED_STEP: type | None = None
_STEP_SCOPE: type | None = None
_NORMALIZE_ROLES: Callable[..., list[str]] | None = None
_DEFAULT_CONSTRAINT: Callable[..., str] | None = None
_RESOLVE_OUTPUT: Callable[..., str] | None = None
_WANTS_COMBINED: Callable[..., bool] | None = None
_WANTS_PER_SOURCE: Callable[..., bool] | None = None
_WANTS_EXTRACT_EACH_COMBINE: Callable[..., bool] | None = None


def _lazy_imports() -> None:
    global _BUILD_STEP, _DIRECT_PLAN, _PLANNED_STEP, _STEP_SCOPE
    global _NORMALIZE_ROLES, _DEFAULT_CONSTRAINT, _RESOLVE_OUTPUT
    global _WANTS_COMBINED, _WANTS_PER_SOURCE, _WANTS_EXTRACT_EACH_COMBINE
    if _BUILD_STEP is not None:
        return
    from pipeline.direct import query_planner as qp

    _BUILD_STEP = qp._build_step
    _DIRECT_PLAN = qp.DirectPlan
    _PLANNED_STEP = qp.PlannedStep
    _STEP_SCOPE = qp.StepScope
    _NORMALIZE_ROLES = qp._normalize_roles
    _DEFAULT_CONSTRAINT = qp.default_constraint_id
    _RESOLVE_OUTPUT = qp.resolve_output_type_for_direct
    _WANTS_COMBINED = qp._wants_combined_summary
    _WANTS_PER_SOURCE = qp._wants_per_source_summary
    _WANTS_EXTRACT_EACH_COMBINE = qp._wants_extract_each_then_combine


PLANNER_WORKER_SYSTEM = """You are LOMA's Planner Worker. Return ONLY valid JSON (no markdown fences).

Decide how to execute the user request: mode, deliverable type, multi-source strategy, and steps.

JSON schema:
{
  "express": boolean,
  "mode": "generation" | "mutation",
  "output_type": "chat" | "document" | "presentation" | "image",
  "source_strategy": "single" | "per_source" | "combined" | "auto",
  "reason": "one short sentence",
  "steps": [
    {
      "intent": "clear sub-task instruction",
      "roles": ["role_id"],
      "output_constraint_id": "",
      "scope": {"slides": [], "sheets": [], "columns": [], "selection": false}
    }
  ]
}

source_strategy (when 2+ source files/links with text):
- combined: ONE answer/deck/report synthesizing ALL sources (e.g. "from all sources", "using both files").
- per_source: separate output per file (e.g. "summary for each source", "each input").
- single: one step using merged context (one file or trivial multi-source).
- auto: let LOMA infer from the user query.

mode:
- mutation = edit the uploaded Office file IN PLACE (translate, fix slide 3, update cells, rewrite paragraph).
- generation = CREATE a new deliverable (new presentation deck, new report, chat answer from sources).

CRITICAL mode rules:
- output_type=presentation + user asks to create/do/make/build/prepare a presentation/deck → ALWAYS generation.
- Multiple source files + new presentation/document/report from them → ALWAYS generation, NEVER mutation.
- output_type differs from uploaded file type (e.g. docx sources → presentation output) → generation.
- Only choose mutation when the user clearly wants the uploaded file changed, not a new artifact.

output_type priority: (1) user query explicit format (2) preferred_output_format UI (3) profile (4) chat.

express=true ONLY for simple Q&A with NO files, NO links, NO transforms.

When files are attached, express=false. You may return steps=[] — LOMA will expand multi-source and deliverable pipelines.

roles: general_answer, summarizer, translator, selective_translator, writer, editor, tone_rewriter,
data_analyst, extractor, outliner, synthesizer, slide_author, deck_planner,
bullet_formatter, image_prompt_author, transcriber

When the user asks to transcribe / show the raw transcript / 轉錄 / 逐字稿: use role
transcriber (verbatim passthrough). Do NOT use extractor for that — extractor summarizes
into bullets/facts and hides the raw transcript.

Do NOT plan per-file summarizer mutation for "create presentation from all sources".
For presentation generation from sources: source_strategy=combined, mode=generation, steps=[] or high-level intents only.
"""

_CREATE_DELIVERABLE = re.compile(
    r"\b(?:do|make|build|create|prepare|produce|generate|write)\b.*\b(?:presentation|deck|slides?|pptx)\b"
    r"|\b(?:presentation|deck)\s+on\b",
    re.I,
)
_IN_PLACE_EDIT = re.compile(
    r"\b(?:translate|edit|update|change|modify|mutate|rewrite|rephrase|fix|correct|replace|"
    r"revise|amend|adjust)\b",
    re.I,
)
_ROLE_REQUIRES_OUTPUT = {
    "deck_planner": "presentation",
    "slide_author": "presentation",
}
_PRES_PIPELINE = frozenset({"deck_planner", "slide_author", "synthesizer"})


def _text_digests(bundle: Any) -> list:
    return [
        d
        for d in (getattr(bundle, "source_digests", None) or [])
        if getattr(d, "kind", "") != "image" and (getattr(d, "full_text", None) or "").strip()
    ]


def _parse_worker_json(raw: str) -> dict | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def planner_worker_context(
    metadata: InputMetadata,
    profile: dict | None,
    bundle: Any | None = None,
) -> str:
    files = ", ".join(
        f"{f.get('name', '?')} ({f.get('type', '?')})" for f in (metadata.files or [])
    ) or "none"
    prof = profile or {}
    base = (
        f"User query: {metadata.query}\n"
        f"preferred_output_format (UI): {metadata.preferred_output_format}\n"
        f"profile_output_hint: {(prof.get('OUTPUT') or {}).get('format', '')}\n"
        f"files ({metadata.file_count or 0}): {files}\n"
        f"links: {metadata.link_count}\n"
        f"has_preview_selection: {metadata.has_valid_preview_selection}\n"
        f"has_image: {metadata.has_image}\n"
    )
    if bundle is None:
        return base
    digests = _text_digests(bundle)
    extra = f"text_sources: {len(digests)}\n"
    for d in digests[:8]:
        preview = (getattr(d, "full_text", "") or "")[:280].replace("\n", " ")
        extra += f"  - {getattr(d, 'name', '?')} ({getattr(d, 'kind', '?')}): {preview}…\n"
    index_md = getattr(bundle, "digest_index_md", "") or ""
    if index_md:
        extra += f"\n{index_md}\n"
    return base + extra


def plan_with_worker(
    metadata: InputMetadata,
    *,
    profile: dict | None = None,
    model: str,
    has_charts: bool = False,
    log_fn=None,
    bundle: Any | None = None,
) -> Any | None:
    """Planner Worker LLM call; returns DirectPlan or None."""
    _lazy_imports()
    import time
    from services import llm_bridge as chat_client
    from services.resource_governor import ResourceGovernor

    user_ctx = planner_worker_context(metadata, profile, bundle)
    try:
        from services.inference.ollama_chat import build_planner_chat_request

        t0 = time.perf_counter()
        with ResourceGovernor.acquire("llm_chat"):
            resp = chat_client.chat(
                **build_planner_chat_request(
                    profile,
                    model=model,
                    messages=[
                        {"role": "system", "content": PLANNER_WORKER_SYSTEM},
                        {"role": "user", "content": user_ctx},
                    ],
                )
            )
        if log_fn:
            log_fn(f"Planner worker LLM: {round((time.perf_counter() - t0) * 1000)}ms")
        raw = (resp.get("message") or {}).get("content") or ""
    except Exception as exc:
        if log_fn:
            log_fn(f"Planner worker failed: {exc}")
        return None

    data = _parse_worker_json(raw)
    if not data:
        if log_fn:
            log_fn("Planner worker: could not parse JSON")
        return None

    reason = str(data.get("reason") or "").strip()
    if reason and log_fn:
        log_fn(f"Planner worker: {reason}")

    # An explicit format named in the query (e.g. "as a document") must win even when the
    # planner LLM's own JSON guesses a different output_type — small models misclassify this
    # more often than the regex-based query check does.
    output_type = normalize_output_type(
        infer_format_from_query(metadata.query)
        or data.get("output_type")
        or _RESOLVE_OUTPUT(metadata.query, metadata.preferred_output_format, profile)
    )
    mode = str(data.get("mode") or "generation").strip().lower()
    if output_type == "chat":
        mode = "generation"
    elif mode not in ("generation", "mutation"):
        mode = "generation"

    source_strategy = str(data.get("source_strategy") or "auto").strip().lower()
    if source_strategy not in ("single", "per_source", "combined", "auto"):
        source_strategy = "auto"

    if bool(data.get("express")) and can_use_express_lane(metadata):
        return _DIRECT_PLAN(
            express=True,
            step_count=1,
            mode="generation",
            output_type="chat",
            steps=[],
            source_strategy="single",
        )

    steps: list = []
    for raw_step in data.get("steps") or []:
        if not isinstance(raw_step, dict):
            continue
        intent = str(raw_step.get("intent") or metadata.query or "").strip()
        roles = _NORMALIZE_ROLES(list(raw_step.get("roles") or []))
        sc = raw_step.get("scope") or {}
        scope = _STEP_SCOPE(
            slides=[int(x) for x in (sc.get("slides") or []) if str(x).isdigit()],
            sheets=[int(x) for x in (sc.get("sheets") or []) if str(x).isdigit()],
            columns=[str(c).upper() for c in (sc.get("columns") or [])],
            selection=bool(sc.get("selection")),
        )
        cid = str(raw_step.get("output_constraint_id") or "").strip()
        if not cid:
            cid = _DEFAULT_CONSTRAINT(output_type, mode, roles[0])
        steps.append(
            _PLANNED_STEP(
                intent=intent,
                roles=roles,
                output_constraint_id=cid,
                scope=scope,
            )
        )

    # A role tied to one deliverable (deck_planner/slide_author -> presentation)
    # is nonsense once output_type has been corrected to
    # something else — e.g. the planner hallucinating a presentation-building step for a plain
    # "generate a document on X" request. Drop the bad steps so the pipeline's own default-step
    # fallback (a plain generation role matching output_type) runs instead of a mismatched one.
    if steps and any(
        _ROLE_REQUIRES_OUTPUT.get(r) not in (None, output_type)
        for s in steps
        for r in s.roles
    ):
        steps = []

    return _DIRECT_PLAN(
        express=False,
        step_count=len(steps) or 1,
        mode=mode,
        output_type=output_type,
        steps=steps,
        source_strategy=source_strategy,
    )


def resolve_source_strategy(
    plan: Any,
    metadata: InputMetadata,
    bundle: Any | None,
) -> str:
    _lazy_imports()
    digests = _text_digests(bundle) if bundle else []
    n = len(digests)
    ss = str(getattr(plan, "source_strategy", "auto") or "auto").lower()
    q = metadata.query or ""

    if n < 2:
        return "single"
    if _WANTS_EXTRACT_EACH_COMBINE(q, n):
        return "combined"
    if _WANTS_PER_SOURCE(q, n):
        return "per_source"
    if _WANTS_COMBINED(q, n):
        return "combined"
    if ss in ("combined", "per_source", "single"):
        return ss
    if plan.output_type in ("presentation", "document") and plan.mode == "generation":
        return "combined"
    if getattr(bundle, "context_strategy", "") == "per_source" and not _WANTS_COMBINED(q, n):
        return "per_source"
    return "combined"


def apply_plan_guardrails(
    plan: Any,
    metadata: InputMetadata,
    bundle: Any | None = None,
    *,
    log_fn=None,
) -> None:
    """Hard safety rules — override planner when clearly wrong."""
    _lazy_imports()
    q = (metadata.query or "").strip()
    ot = normalize_output_type(plan.output_type or "chat")

    if ot == "chat":
        plan.mode = "generation"

    if metadata.has_valid_preview_selection:
        plan.mode = "mutation"
        if log_fn:
            log_fn("Guardrail: mutation (preview selection)")

    # PLANNER_WORKER_SYSTEM's own "mode" rules only ever describe mutation in terms of
    # Office documents ("edit the uploaded Office file IN PLACE") — it never mentions
    # image editing at all, so the planner LLM has no basis to classify an uploaded-photo
    # edit ("change the frog to a toad") as mutation even when it correctly understands
    # the intent (visible in its own `reason` text) — it just falls back to the
    # mode="generation" default. should_route_image_mutation()'s InputRouter-side fix
    # doesn't reach this separate Direct-pipeline planner at all, so mirror it here.
    if ot == "image" and metadata.has_image:
        from pipeline.routing_helpers import query_requests_image_mutation

        if query_requests_image_mutation(q):
            plan.mode = "mutation"
            if log_fn:
                log_fn("Guardrail: mutation (image edit intent + attached image)")

    file_count = metadata.file_count or len(metadata.files or [])
    digests = _text_digests(bundle) if bundle else []

    if ot == "presentation" and plan.mode == "mutation":
        if _CREATE_DELIVERABLE.search(q) or file_count > 1 or len(digests) > 1:
            plan.mode = "generation"
            if log_fn:
                log_fn("Guardrail: generation (new presentation from sources)")

    if ot == "presentation" and not _IN_PLACE_EDIT.search(q):
        if file_count >= 1 or len(digests) >= 1:
            if not metadata.has_valid_preview_selection:
                plan.mode = "generation"
                if log_fn:
                    log_fn("Guardrail: generation (presentation deliverable)")

    if file_count > 1 or len(digests) > 1:
        if ot in ("presentation", "document", "chat") and not metadata.has_valid_preview_selection:
            if not (_IN_PLACE_EDIT.search(q) and ot == "document"):
                plan.mode = "generation"
                if log_fn:
                    log_fn("Guardrail: generation (multi-source)")

    if metadata_has_office_artifact(metadata) and ot in ("document", "presentation"):
        from pipeline.direct.mode_resolver import _is_whole_document_transform

        if _is_whole_document_transform(q, metadata) and not metadata.has_valid_preview_selection:
            plan.mode = "generation"
            if log_fn:
                log_fn("Guardrail: generation (whole-document translate/summarize)")

    # Explicit unit scope (slide N / cell X / column Y …) on a single uploaded office file
    # is a partial edit — honor it deterministically as mutation rather than trusting the
    # planner LLM. Multi-source stays generation (handled above); preview selection already
    # forced mutation earlier.
    if (
        metadata_has_office_artifact(metadata)
        and ot in ("document", "presentation")
        and file_count == 1
        and len(digests) <= 1
        and not metadata.has_valid_preview_selection
    ):
        from pipeline.direct.mode_resolver import has_explicit_unit_scope

        if has_explicit_unit_scope(q):
            plan.mode = "mutation"
            if log_fn:
                log_fn("Guardrail: mutation (explicit unit scope — partial edit)")

    if metadata_has_office_artifact(metadata) and ot == "document":
        from pipeline.direct.mode_resolver import _source_output_mismatch

        if _source_output_mismatch(metadata, ot) and not _IN_PLACE_EDIT.search(q):
            plan.mode = "generation"

    explicit = infer_format_from_query(q)
    if explicit:
        plan.output_type = explicit

    if _WANTS_EXTRACT_EACH_COMBINE(q, len(digests) or file_count):
        plan.source_strategy = "combined"
    elif _WANTS_COMBINED(q, len(digests) or file_count):
        plan.source_strategy = "combined"
    elif _WANTS_PER_SOURCE(q, len(digests) or file_count):
        plan.source_strategy = "per_source"

    if (
        ot == "chat"
        and plan.mode == "generation"
        and not plan.express
        and not (metadata.file_count or metadata.link_count)
        and not digests
    ):
        from pipeline.direct.query_planner import _split_intents

        # Source-free single-intent query: always force the step's intent to the literal
        # user query. The planner LLM's own step (even a single one) can't be trusted here —
        # small models sometimes echo the JSON schema's example "intent" placeholder text
        # verbatim instead of restating the task, which then gets sent to the writer/answer
        # role as its prompt instead of the actual topic.
        if len(_split_intents(q)) <= 1:
            plan.steps = [
                _BUILD_STEP(
                    q,
                    "chat",
                    "generation",
                    metadata,
                    roles=["general_answer"],
                )
            ]
            plan.step_count = 1
            if log_fn:
                log_fn("Guardrail: chat-only query → general_answer (single step)")

    if (
        ot == "image"
        and plan.mode == "generation"
        and not plan.express
        and not (metadata.file_count or metadata.link_count)
        and not digests
    ):
        from pipeline.direct.query_planner import _split_intents

        # Same guardrail as the chat/document cases above: a source-free image request
        # is exactly the case where the planner LLM sometimes echoes the JSON schema's
        # generic "intent" placeholder (e.g. "generate image prompt") instead of the
        # actual subject — image_prompt_author then has nothing to work with and
        # invents an unrelated scene. Force the literal user query through instead.
        if len(_split_intents(q)) <= 1:
            plan.steps = [
                _BUILD_STEP(
                    q,
                    "image",
                    "generation",
                    metadata,
                    roles=["image_prompt_author"],
                )
            ]
            plan.step_count = 1
            if log_fn:
                log_fn("Guardrail: image-only query → image_prompt_author (single step)")

    if (
        ot == "document"
        and plan.mode == "generation"
        and not plan.express
        and not (metadata.file_count or metadata.link_count)
        and not digests
    ):
        from pipeline.direct.query_planner import _split_intents

        if len(_split_intents(q)) <= 1:
            plan.steps = [
                _BUILD_STEP(
                    q,
                    "document",
                    "generation",
                    metadata,
                    roles=["writer"],
                    constraint_id="document_markdown",
                )
            ]
            plan.step_count = 1
            if log_fn:
                log_fn("Guardrail: source-free document → writer (single step)")

    from pipeline.routing_helpers import query_requests_transcription

    if (
        not plan.express
        and ot in ("chat", "document")
        and query_requests_transcription(q, ot)
        and (file_count >= 1 or len(digests) >= 1 or metadata.link_count)
    ):
        lower_q = q.lower()
        wants_raw = any(
            p in lower_q or p in q
            for p in (
                "raw",
                "verbatim",
                "just show",
                "only show",
                "just the transcript",
                "only the transcript",
                "transcribe",
                "transcript",
                "transcription",
                "轉錄",
                "转录",
                "逐字稿",
            )
        )
        wants_summary = any(
            w in lower_q for w in ("summar", "bullet", "key point", "摘要", "總結", "总结")
        )
        if wants_raw and not wants_summary:
            plan.mode = "generation"
            plan.steps = [
                _BUILD_STEP(
                    q or "Show the raw transcript",
                    ot,
                    "generation",
                    metadata,
                    roles=["transcriber"],
                    constraint_id="chat_transcript",
                )
            ]
            plan.step_count = 1
            if log_fn:
                log_fn("Guardrail: transcription → transcriber (raw transcript)")


def apply_source_strategy_steps(
    plan: Any,
    bundle: Any,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    """Expand multi-source plans (replaces regex expanders)."""
    _lazy_imports()
    if plan.express or plan.mode == "mutation":
        return
    digests = _text_digests(bundle)
    if len(digests) < 2:
        return

    strategy = resolve_source_strategy(plan, metadata, bundle)
    plan.source_strategy = strategy
    q = (metadata.query or "").strip()
    ot = plan.output_type

    roles_flat = {r for s in plan.steps for r in (s.roles or [])}
    if strategy == "single":
        return
    if strategy == "per_source" and roles_flat <= {"summarizer"} and len(plan.steps) == len(digests):
        return
    if strategy == "combined" and "extractor" in roles_flat and (
        "synthesizer" in roles_flat or roles_flat & _PRES_PIPELINE
    ):
        if len([s for s in plan.steps if s.roles == ["extractor"]]) == len(digests):
            return

    if strategy == "per_source":
        # Honor compound requests per file: "transcribe and translate each file" →
        # a transcriber step AND a translator step FOR EACH source. Each step targets one
        # named source (digest_text_for_step matches the filename) and runs in isolation.
        from pipeline.direct.query_planner import _split_intents

        intents = [i for i in (_split_intents(q) if q else []) if i.strip()] or [""]
        steps = []
        for d in digests:
            for intent in intents:
                label = f"{intent} — {d.name}" if intent else f"Summarize {d.name}"
                steps.append(_BUILD_STEP(label, ot, plan.mode, metadata))
        plan.steps = steps
        if log_fn:
            suffix = f", {len(intents)} per file" if len(intents) > 1 else ""
            log_fn(f"Plan: per-source ({len(steps)} steps{suffix})")
    elif strategy == "combined":
        from pipeline.direct.query_planner import _LANG_PAIR
        from pipeline.query_intent_i18n import matches as _qi_matches

        steps = [
            _BUILD_STEP(
                f"Extract key points from {d.name}",
                ot,
                "generation",
                metadata,
                roles=["extractor"],
            )
            for d in digests
        ]
        synth_intent = "Produce a single summary from all extracted main points"
        if re.search(r"\bsummary\b", q, re.I):
            parts = re.split(r"\bthen\b", q, maxsplit=1, flags=re.I)
            synth_intent = parts[0].strip() or synth_intent
        steps.append(
            _BUILD_STEP(
                synth_intent,
                ot,
                "generation",
                metadata,
                roles=["synthesizer"],
            )
        )
        lower = q.lower()
        wants_translate = _qi_matches(lower, "verb_translate") or bool(_LANG_PAIR.search(q))
        if wants_translate:
            translate_intent = "Translate the combined summary to English"
            if "then" in lower:
                tail = re.split(r"\bthen\b", q, maxsplit=1, flags=re.I)[-1].strip()
                if tail:
                    translate_intent = tail
            steps.append(
                _BUILD_STEP(
                    translate_intent,
                    ot,
                    "generation",
                    metadata,
                    roles=["translator"],
                )
            )
        plan.steps = steps
        if log_fn:
            log_fn(f"Plan: combined multi-source ({len(steps)} steps)")
    plan.step_count = len(plan.steps)


_PRES_PIPELINE_ROLES = ("deck_planner", "slide_author")


def _apply_pres_intermediate_constraints(plan: Any) -> None:
    """Keep prep-step chat constraints; deck_planner uses deck spec, slide_author uses markdown."""
    from pipeline.direct.output_constraints import default_constraint_id

    if len(plan.steps) <= 1:
        return
    for i, step in enumerate(plan.steps[:-1]):
        role = (step.roles or ["general_answer"])[0]
        cid = default_constraint_id(plan.output_type or "chat", plan.mode, role)
        if step.output_constraint_id == cid:
            continue
        plan.steps[i] = _PLANNED_STEP(
            intent=step.intent,
            roles=step.roles,
            output_constraint_id=cid,
            scope=step.scope,
        )


def ensure_presentation_pipeline(
    plan: Any,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    """deck_planner → slide_author (after optional source prep / synthesizer)."""
    _lazy_imports()
    if plan.output_type != "presentation" or plan.express or plan.mode == "mutation":
        return
    roles = [(s.roles or [""])[0] for s in (plan.steps or [])]
    if "deck_planner" in roles and "slide_author" in roles:
        deduped: list[Any] = []
        seen: set[str] = set()
        for step in plan.steps or []:
            r = (step.roles or [""])[0]
            if r in _PRES_PIPELINE_ROLES:
                if r in seen:
                    continue
                seen.add(r)
            deduped.append(step)
        plan.steps = deduped
        plan.step_count = len(plan.steps)
        _apply_pres_intermediate_constraints(plan)
        return
    # Any completed task step (translate/summarize/analyze/rewrite/extract/plain-answer) is
    # valid raw material for a deck — the format-shaping stage (deck_planner -> slide_author
    # below) reflows it into slides. Without "translator"/"data_analyst"/etc. here, a step
    # whose role wasn't extractor/summarizer/outliner fell straight through with no reflow
    # stage at all: e.g. "translate this and make it a pptx" left the plan as a single
    # translator step, output_type=presentation, with nothing to add the required
    # --- Slide N --- structure — the compiler had nothing slide-shaped to work with.
    prep_roles = frozenset(
        {
            "extractor",
            "summarizer",
            "outliner",
            "translator",
            "selective_translator",
            "data_analyst",
            "editor",
            "tone_rewriter",
            "general_answer",
        }
    )
    prep = [s for s in plan.steps if (s.roles or [""])[0] in prep_roles]
    q = (metadata.query or "").strip() or "Create presentation"
    format_steps: list[Any] = []
    if len(prep) > 1:
        format_steps.append(
            _BUILD_STEP(
                "Synthesize all source material for the presentation",
                "presentation",
                "generation",
                metadata,
                roles=["synthesizer"],
                constraint_id="deliverable_synthesis",
            )
        )
    format_steps.extend(
        [
            _BUILD_STEP(
                f"Plan deck structure: {q[:120]}",
                "presentation",
                "generation",
                metadata,
                roles=["deck_planner"],
                constraint_id="presentation_deck_spec",
            ),
            _BUILD_STEP(
                q,
                "presentation",
                "generation",
                metadata,
                roles=["slide_author"],
                constraint_id="presentation_markdown",
            ),
        ]
    )
    plan.steps = prep + format_steps
    plan.step_count = len(plan.steps)
    _apply_pres_intermediate_constraints(plan)
    if log_fn:
        log_fn("Plan: deck planner → slide author")




def append_presentation_pipeline(
    plan: Any,
    metadata: InputMetadata,
    *,
    log_fn=None,
) -> None:
    ensure_presentation_pipeline(plan, metadata, log_fn=log_fn)
