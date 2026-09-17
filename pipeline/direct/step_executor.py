# -*- coding: utf-8 -*-
"""Execute direct pipeline steps: generation, mutation, compile."""
from __future__ import annotations

import os
import re
from typing import Any

from pipeline.capability_runtime.chat_runner import (
    build_chat_contract_text,
    build_merged_roles_system_instruction,
    generate_text_sync,
    stream_chat_response,
    validate_or_repair_prompt,
)
from pipeline.capability_runtime.role_pipeline import RolePipelineConfig, run_role_pipeline
from pipeline.direct.input_slicer import build_step_input_payload, slice_mutation_units
from pipeline.direct.query_planner import DirectPlan, PlannedStep
from pipeline.direct.task_contracts import get_task_contract_for_role
from pipeline.direct.task_roles import get_task_role, mutation_role_ids
from pipeline.direct.output_constraints import build_constraint_text
from pipeline.direct.prompt_hygiene import looks_like_refusal, user_step_content
from pipeline.output_format import deliverable_display_name, EXTENSION_BY_TYPE, normalize_output_type

CAPABILITY_ID = "direct_pipeline"

# Cap on web-search grounding context injected into a presentation's system prompt
# (see the deck_planner block below) — small enough to leave headroom for the brief
# + style bias on a local model with a limited (and not reliably auto-widened)
# context window; see that block's comment for the real overflow this fixed.
_MAX_PRESENTATION_GROUND_CTX_CHARS = 1800

# Deck-style-picker bias text appended to deck_planner's system prompt (see
# pipeline/deliverables/presentation_theme.py's PRESENTATION_STYLES for the
# matching picker options + forced palette). Empty/unknown style = no bias,
# i.e. today's unchanged default behavior.
PRESENTATION_STYLE_PROMPT_BIAS = {
    "minimal": (
        "Deck style: Minimal. Keep every slide plain content (title/agenda/content/closing) — "
        "avoid 'section' and 'quote' layout slides entirely unless the deck is genuinely long "
        "(10+ slides). Use a visual only where it's truly essential, never decoratively. Keep "
        "bullets short and few."
    ),
    "bold": (
        "Deck style: Bold. Prefer full-bleed, high-impact visuals wherever a visual is used (a "
        "strong photo or scene, not a small illustrative one) — the compiler automatically varies "
        "the treatment slide to slide (full overlay, bottom-band, split-hero), so just describe a "
        "strong, specific scene each time. Include at least one 'quote' layout slide for a "
        "standout stat or quote if the material actually contains one quotable line or number — "
        "never invent one. For a longer deck (6+ slides), use 'section' divider slides between "
        "major parts."
    ),
    "insight": (
        "Deck style: Insight. Whenever a slide's content has any numbers, trends, a process, or a "
        "comparison between two or more things, phrase that slide's visual description as a "
        "chart/diagram/comparison request (see the visual-description guidance above) instead of a "
        "generic photo — prefer this far more aggressively than usual. Include a 'quote' layout "
        "slide for the single most striking statistic in the material, if one exists. The compiler "
        "automatically gives charts/diagrams the full slide width and keeps comparisons/photos in "
        "a side column, so just focus on requesting the right visual type per slide — keep the "
        "accent bar visible and text dense (short label + supporting line per point, not long "
        "prose bullets)."
    ),
}

# Fallback bias for styles whose default text assumes real source data to chart (only
# "insight" needs one today) — used when the deck has no parsed_sources to draw
# numbers from, so the planner isn't pushed into inventing statistics/charts (see
# PresentationBrief.to_system_block()'s has_source_data rule, which forbids this too —
# this is the style-specific half of the same guarantee).
PRESENTATION_STYLE_PROMPT_BIAS_NO_SOURCE = {
    "insight": (
        "Deck style: Insight, but no source documents/datasets were provided — do not invent "
        "numbers or request chart/stat visuals (see the Data integrity rule above). Keep the same "
        "dense text and accent bar, but phrase visuals as process/comparison diagrams (flow, "
        "before/after, categories) built from qualitative structure in the content, never numeric "
        "charts. Include a 'quote' layout slide only for an actual quotable line in the material, "
        "never a fabricated statistic."
    ),
}

_OFFICE_DEP_HINT = {
    "document": "python-docx",
    "presentation": "python-pptx",
}

from pipeline.direct.highlight_excerpt import extract_highlight_excerpt, is_highlight_query


def _highlight_excerpt_from_query(query: str) -> str:
    return extract_highlight_excerpt(query)


def _is_highlight_query(query: str) -> bool:
    return is_highlight_query(query)


# Per-source transform roles: each operates on ONE named source in isolation and its
# output is accumulated under that source's heading (never chained into the next file).
# Kept only as documentation of the common case now — _per_source_isolated_step no longer
# gates on this list (see its docstring for why: a hardcoded role whitelist silently broke
# for any role not on it, e.g. general_answer, which is exactly the bug this replaced).
_PER_SOURCE_ISOLATE_ROLES = frozenset(
    {
        "summarizer",
        "transcriber",
        "translator",
        "selective_translator",
        "editor",
        "tone_rewriter",
        "bullet_formatter",
    }
)


def _per_source_isolated_step(
    step: PlannedStep,
    bundle: Any,
    *,
    output_type: str,
    steps: list,
) -> bool:
    """One-source-per-step in a multi-step plan (summarize/transcribe/translate each file).

    Gated structurally — "did this step resolve to exactly one file's real text?" — rather
    than by role_id whitelist. A whitelist has to be kept in sync by hand every time a new
    role or query intent shows up in a per-file-expanded plan; missing one meant later steps
    silently fell back to thin, all-files-mixed preview text and only the last file's answer
    ever reached the user (see e.g. a 3-document "differences" query routed through
    general_answer). synthesizer/writer/slide_author stay excluded: they are final aggregator
    steps that consume the accumulated working_text, not a per-file transform, and must never
    have their working_text reset to "".

    output_type is deliberately NOT part of the gate below (it used to require "chat",
    which is unrelated to whether this step resolved one file's real text — it just meant
    a document/presentation per-file step never got the "translate/summarize ONLY this
    file, don't merge or repeat other sources" instruction and never got its working_text
    reset between files, so multi-file document/presentation requests silently blended and
    duplicated content across sources in a way the equivalent chat request never did).
    """
    if len(steps) < 2:
        return False
    role = step.roles[0] if step.roles else ""
    if role in _FINAL_COMBINED_ROLES:
        return False
    from pipeline.direct.input_slicer import digest_text_for_step

    return bool(digest_text_for_step(step, bundle))


_PRES_PIPELINE = frozenset()
_PREP_ROLES = frozenset({"extractor", "summarizer"})
_FINAL_COMBINED_ROLES = frozenset({"synthesizer", "writer", "slide_author"})


def _combined_multi_source_plan(steps: list) -> bool:
    if len(steps) < 2:
        return False
    roles = [(s.roles[0] if s.roles else "") for s in steps]
    prep = [i for i, r in enumerate(roles) if r in _PREP_ROLES]
    if not prep:
        return False
    tail = roles[max(prep) + 1 :]
    if not tail:
        return False
    if tail[-1] in _FINAL_COMBINED_ROLES:
        return True
    return bool(_PRES_PIPELINE.intersection(tail))


def _combined_synthesis_plan(steps: list) -> bool:
    return _combined_multi_source_plan(steps)


def _combined_prep_step(role_id: str, steps: list, step_idx: int) -> bool:
    if role_id not in _PREP_ROLES or not _combined_multi_source_plan(steps):
        return False
    roles = [(s.roles[0] if s.roles else "") for s in steps]
    last_prep = max(i for i, r in enumerate(roles) if r in _PREP_ROLES)
    return step_idx <= last_prep


def _combined_merge_step(role_id: str, steps: list, step_idx: int) -> bool:
    """First synthesizer/writer/slide_author after per-source prep — merges all extractions."""
    if role_id not in _FINAL_COMBINED_ROLES or not _combined_multi_source_plan(steps):
        return False
    roles = [(s.roles[0] if s.roles else "") for s in steps]
    prep = [i for i, r in enumerate(roles) if r in _PREP_ROLES]
    if not prep:
        return False
    last_prep = max(prep)
    for i in range(last_prep + 1, len(roles)):
        if roles[i] in _FINAL_COMBINED_ROLES:
            return step_idx == i and roles[i] == role_id
    return False


def _source_step_heading(step: PlannedStep, bundle: Any) -> str:
    heading = (step.intent or "").strip()
    heading_lower = heading.lower()
    digests = getattr(bundle, "source_digests", None) or []
    # 0) Per-file compound label "{op} — {name}": keep the operation so multiple steps on the
    #    same file (transcribe/translate/summarize each file) get distinct, descriptive headings
    #    instead of repeating the bare filename.
    if " — " in heading:
        op, _, tail = heading.rpartition(" — ")
        op, tail = op.strip(), tail.strip()
        for d in digests:
            if d.name and d.name.strip().lower() == tail.lower():
                return f"{d.name} — {op[:48]}" if op else d.name
    # 1) Exact full-name match anywhere in the intent — unambiguous, robust to any phrasing
    #    ("Summarize X", "transcribe … — X", "translate each file — X").
    for d in digests:
        name = d.name or ""
        if name and name.lower() in heading_lower:
            return name
    # 2) Structured "Summarize X" / "Extract … from X" intents.
    m = re.match(r"^Summarize\s+(.+)$", heading, re.I)
    if m:
        return m.group(1).strip()
    m = re.match(r"^Extract(?:\s+\w+)*\s+from\s+(.+)$", heading, re.I)
    if m:
        return m.group(1).strip()
    # 3) Fuzzy base-name fallback (last, to avoid prefix-overlap mis-assignment).
    for d in digests:
        name = d.name or ""
        base = os.path.splitext(name.lower())[0]
        if name and base and len(base) > 3 and base in heading_lower:
            return name
    return heading


def _image_paths_from_bundle(bundle: Any) -> list[str]:
    from pipeline.image_sources import image_paths_from_bundle

    return image_paths_from_bundle(bundle)


def _run_spreadsheet_query_step(*, step: PlannedStep, cfg: RolePipelineConfig, bundle: Any) -> str:
    """Compute a precise answer via generated-and-executed pandas code; fall back to a
    profile-based estimate (clearly labeled as such) if the generated code never runs cleanly."""
    from pipeline.capability_runtime.chat_runner import generate_text_sync
    from services.spreadsheet_query.query import answer_spreadsheet_question

    question = (step.intent or cfg.user_query or "").strip()
    parsed_sources = getattr(bundle, "parsed_sources", None)

    def _generate(messages: list[dict[str, str]]) -> str:
        return generate_text_sync(cfg.profile, cfg.model, messages, disable_thinking=True)

    answer = answer_spreadsheet_question(
        question,
        parsed_sources=parsed_sources,
        generate_text=_generate,
        log_fn=cfg.sink.log,
    )
    if answer:
        return answer

    from services.graph_generation.dataset import resolve_tabular_paths
    from services.graph_generation.profile import profile_dataset

    tabular = resolve_tabular_paths(parsed_sources)
    if not tabular:
        from pipeline.i18n import t as tr

        return tr("chat.spreadsheet_query_no_data")

    profiles_md = "\n\n".join(profile_dataset(path, name=name).summary_md for name, path in tabular)
    fallback_messages = [
        {
            "role": "system",
            "content": (
                "Role: Data Analyst (fallback).\n"
                "A precise computed answer wasn't available for this specific question. "
                "Using only the dataset profile below, give your best reasoned answer, "
                "and say plainly that it's an estimate from summary statistics, not an "
                "exact computed value."
            ),
        },
        {"role": "user", "content": f"{profiles_md}\n\nQuestion: {question}"},
    ]
    return _generate(fallback_messages)


def _generation_user_content(
    *,
    role_id: str,
    step: PlannedStep,
    cfg: RolePipelineConfig,
    step_body: str,
    working_text: str,
    context_text: str,
) -> str:
    """Build user message — highlight/excerpt queries must include source text."""
    isolated = (
        role_id in _PER_SOURCE_ISOLATE_ROLES
        and step_body
        and "Source text (this file only):" in step_body
    )
    if working_text and not isolated:
        return user_step_content(
            user_query=step.intent or cfg.user_query,
            step_body=step_body,
            working_text=working_text,
        )
    if _is_highlight_query(cfg.user_query):
        return (cfg.user_query or "").strip()
    transform_roles = (
        "translator",
        "selective_translator",
        "summarizer",
        "extractor",
        "editor",
        "writer",
        "tone_rewriter",
    )
    if role_id in transform_roles:
        source = context_text or _highlight_excerpt_from_query(cfg.user_query)
        if source:
            from pipeline.direct.batch_processor import (
                _translation_user_body,
                strip_context_source_headers,
            )

            clean = strip_context_source_headers(source)
            if role_id == "translator":
                return _translation_user_body(step.intent or cfg.user_query, clean)
            intent = (step.intent or "").strip()
            if intent:
                return f"{intent}\n\nText:\n{clean}"
            return clean
    return user_step_content(
        user_query=step.intent or cfg.user_query,
        step_body=step_body,
        working_text="",
    )


def _run_generation_step(
    *,
    step: PlannedStep,
    plan: DirectPlan,
    cfg: RolePipelineConfig,
    working_text: str,
    bundle: Any,
    stream: bool,
    stream_base: str = "",
    step_index: int = 0,
) -> str:
    role_id = step.roles[0] if step.roles else "general_answer"
    if role_id == "spreadsheet_query":
        return _run_spreadsheet_query_step(step=step, cfg=cfg, bundle=bundle)
    role = get_task_role(role_id)
    contract = get_task_contract_for_role(
        role_id,
        output_type=plan.output_type,
        mode=plan.mode,
        constraint_id=step.output_constraint_id,
    )
    step_body = build_step_input_payload(
        step=step,
        mode=plan.mode,
        output_type=plan.output_type,
        bundle=bundle,
        working_text=working_text,
    )
    user_content = _generation_user_content(
        role_id=role_id,
        step=step,
        cfg=cfg,
        step_body=step_body,
        working_text=working_text,
        context_text="",
    )
    system = build_merged_roles_system_instruction(
        cfg.base_system_instruction, [role], [contract]
    )
    if plan.output_type in ("document", "presentation") and role_id in (
        "synthesizer", "data_analyst", "writer", "general_answer",
    ):
        from pipeline.direct.input_slicer import digest_text_for_step

        has_source = bool(digest_text_for_step(step, bundle)) or bool(getattr(bundle, "unified_text", None))
        if not has_source:
            from pipeline.base.grounding import resolve_generation_context
            from services.session import state as loma_state

            ground_ctx, _ground_sources = resolve_generation_context(
                cfg.user_query, settings=loma_state.current_settings, broad_trigger=True, log_fn=cfg.sink.log,
            )
            if ground_ctx:
                system += (
                    "\n\nGround your facts in this material — do not state a number "
                    f"or date that isn't supported by it:\n{ground_ctx}"
                )
    if role_id == "translator":
        from pipeline.direct.batch_processor import _translator_system

        system = _translator_system(system, step.intent or cfg.user_query)
    charts = list(getattr(bundle, "chart_artifacts", None) or [])
    if charts and role_id in ("synthesizer", "data_analyst", "writer", "general_answer"):
        from services.graph_generation.report_layout import analysis_hint_for_chart

        hints = "\n".join(
            f"- Figure {i}: {analysis_hint_for_chart(c)} ({getattr(c, 'title', '')})"
            for i, c in enumerate(charts, start=1)
        )
        values_note = (
            "\n\nOnly cite the numbers given above ('Actual values') for each figure — "
            "do not invent, estimate, or restate different figures than what's listed."
        )
        if plan.output_type == "chat":
            system += (
                "\n\nGenerated charts are available and will be shown to the user after your "
                "reply — do not add '## Figure' headings or any 'Figures' section yourself. "
                "Weave each one into your prose using its real number, e.g. 'Figure 1 shows…', "
                "'as seen in Figure 2…':\n" + hints + values_note
            )
        else:
            system += (
                "\n\nGenerated charts are available. Your analysis MUST reference each figure "
                "by its real number (Figure 1, Figure 2, …) — never the literal placeholder "
                "'N' — in prose, and include one `## Figure <number>:` section per chart, "
                "e.g. `## Figure 1: ...`, `## Figure 2: ...`:\n" + hints + values_note
            )
    messages = [{"role": "system", "content": system}]
    isolated_summary = _per_source_isolated_step(
        step, bundle, output_type=plan.output_type, steps=plan.steps or []
    )
    combined_prep = _combined_prep_step(role_id, plan.steps or [], step_index)
    combined_merge = _combined_merge_step(role_id, plan.steps or [], step_index)
    if combined_prep or combined_merge:
        cfg.sink.set_assistant_content("")
        cfg.sink.refresh_chat()
    if combined_merge:
        system += (
            "\n\nYou are synthesizing ONE combined summary or report across ALL listed sources. "
            "Use every ## source section in the extractions below; dedicate coverage to each file. "
            "Do not focus on a single source or omit any file."
        )
        intent_text = (step.intent or cfg.user_query or "").strip()
        if re.search(r"\btranslat", intent_text, re.I):
            from pipeline.direct.batch_processor import _target_language

            lang = _target_language(intent_text)
            system += f"\n\nWrite the entire synthesized output in {lang}."
        elif re.search(r"\bsummar", intent_text, re.I):
            system += (
                "\n\nProduce a concise combined summary across all sources — "
                "do not dump raw source text verbatim."
            )
        messages[0] = {"role": "system", "content": system}
    if isolated_summary:
        if role_id == "summarizer":
            isolate_note = "Summarize ONLY the single source file in the user message. "
        elif role_id in ("translator", "selective_translator"):
            isolate_note = "Translate ONLY the single source file in the user message. "
        elif role_id == "transcriber":
            isolate_note = "Clean up / transcribe ONLY the single source in the user message. "
        else:
            isolate_note = "Process ONLY the single source file in the user message. "
        system += (
            f"\n\n{isolate_note}"
            "Do not repeat or merge content from other files or prior chat turns."
        )
        messages[0] = {"role": "system", "content": system}
    elif not (combined_prep or combined_merge):
        for m in cfg.messages[1:]:
            if m.get("role") != "system":
                messages.append(m)
    if combined_merge and (working_text or "").strip():
        intent = (step.intent or cfg.user_query or "").strip()
        user_content = (
            f"{intent}\n\nSource extractions (use every section):\n{working_text.strip()}"
        )
    if role_id == "deck_planner" and plan.output_type == "presentation":
        from pipeline.deliverables.presentation_brief import build_presentation_brief

        has_source_data = bool(getattr(bundle, "parsed_sources", None))
        ground_ctx = ""
        if not has_source_data:
            # Same grounding _run_generation_step already does for document roles
            # (synthesizer/data_analyst/writer/general_answer, above) — decks never
            # got it before, so a plain topic query always fell back to the model's
            # unverified knowledge even with the web-grounding toggle on.
            from pipeline.base.grounding import resolve_generation_context
            from services.session import state as loma_state

            ground_ctx, _ground_sources = resolve_generation_context(
                cfg.user_query, settings=loma_state.current_settings, broad_trigger=True,
                log_fn=cfg.sink.log,
            )
            # gather_grounded_context can return several page snippets (up to ~3500
            # chars each) — fine for a document's comparatively light system prompt,
            # but presentations already carry the brief + style bias on top, and a
            # real run overflowed the local model's context window even after fixing
            # the double-grounding bug above. Cap it here rather than relying on
            # "usually small enough".
            ground_ctx = ground_ctx[:_MAX_PRESENTATION_GROUND_CTX_CHARS]
            has_source_data = bool(ground_ctx)
        cfg.presentation_grounded = has_source_data
        system += "\n\n" + build_presentation_brief(
            cfg.user_query, has_source_data=has_source_data
        ).to_system_block()
        if ground_ctx:
            system += (
                "\n\nGround your facts in this material — do not state a number "
                f"or date that isn't supported by it:\n{ground_ctx}"
            )
        style_bias = PRESENTATION_STYLE_PROMPT_BIAS.get(cfg.presentation_style, "")
        if style_bias and (has_source_data or cfg.presentation_style != "insight"):
            system += "\n\n" + style_bias
        elif cfg.presentation_style == "insight":
            # No source data to chart — insight still gets its denser-layout bias,
            # just without the "phrase visuals as charts/stats" instruction that would
            # otherwise push the planner into inventing numbers to fill them.
            system += "\n\n" + PRESENTATION_STYLE_PROMPT_BIAS_NO_SOURCE["insight"]
        messages[0] = {"role": "system", "content": system}
    if role_id == "slide_author" and plan.output_type == "presentation":
        from pipeline.deliverables.presentation_brief import build_presentation_brief

        # No separate web search here — deck_planner (which runs first for every
        # presentation) already resolved grounding and set cfg.presentation_grounded;
        # slide_author only expands that blueprint's own bullets into full sentences,
        # it doesn't need its own fresh search. Re-running the same search here (as an
        # earlier version of this fix did) injected the same large web-search context
        # into the system prompt TWICE, which overflowed the local model's context
        # window on a real run ("too large for the model's context window").
        has_source_data = bool(getattr(bundle, "parsed_sources", None)) or cfg.presentation_grounded
        system += "\n\n" + build_presentation_brief(
            cfg.user_query, has_source_data=has_source_data
        ).to_system_block()
        if (working_text or "").strip().lstrip().startswith("{"):
            system += (
                "\n\nThe user message includes the deck JSON blueprint from Deck Planner. "
                "Follow it exactly: same slide count, titles, and order; expand bullets into full lines."
            )
        messages[0] = {"role": "system", "content": system}
    images = list(getattr(bundle, "images", None) or [])
    use_model = cfg.model
    if images:
        from services.model_router import resolve_chat_model

        chat_model, vision_error = resolve_chat_model(cfg.profile, "text", images)
        if vision_error:
            return vision_error
        use_model = chat_model or use_model
        stream = False

    user_msg: dict[str, Any] = {"role": "user", "content": user_content}
    if images:
        user_msg["images"] = images
    messages.append(user_msg)

    from pipeline.direct.batch_processor import maybe_batched_transform
    from pipeline.context.digest_store import join_digest_plain_text
    from pipeline.direct.input_slicer import digest_text_for_step

    strategy = getattr(bundle, "context_strategy", "fit") or "fit"
    context_text = (getattr(bundle, "unified_text", None) or "").strip()

    digests = getattr(bundle, "source_digests", None) or []
    plain_source = join_digest_plain_text(digests) if digests else ""
    per_source_text = digest_text_for_step(step, bundle)

    if isolated_summary:
        context_text = per_source_text
    elif role_id in ("translator", "summarizer", "selective_translator", "extractor"):
        if per_source_text:
            context_text = per_source_text
        elif strategy == "per_source":
            context_text = per_source_text
        elif plain_source:
            context_text = plain_source
        elif role_id == "translator":
            from services.source_parser import primary_office_source

            office = primary_office_source(getattr(bundle, "parsed_sources", None) or [])
            units = list(getattr(office, "mutation_units", None) or []) if office else []
            if units:
                context_text = "\n\n".join(
                    (u.get("text") or u.get("value") or "").strip()
                    for u in units
                    if (u.get("text") or u.get("value") or "").strip()
                )
        if not context_text:
            excerpt = _highlight_excerpt_from_query(cfg.user_query)
            if excerpt:
                context_text = excerpt
            else:
                for ps in getattr(bundle, "parsed_sources", None) or []:
                    if hasattr(ps, "context_text"):
                        t = (ps.context_text() or "").strip()
                        if len(t) > len(context_text):
                            context_text = t
        if role_id == "translator" and len(context_text) > 2_500:
            strategy = "map_reduce"
    elif not working_text and strategy == "map_reduce":
        context_text = plain_source or context_text
    elif not working_text and per_source_text:
        context_text = per_source_text

    if charts and role_id in ("data_analyst", "writer", "synthesizer"):
        from services.graph_generation.report_layout import build_ordered_report_context

        chart_ctx = build_ordered_report_context([], charts, query=step.intent or cfg.user_query)
        if chart_ctx:
            context_text = f"{chart_ctx}\n\n{context_text}".strip() if context_text else chart_ctx

    # working_text (a prior step's full output, e.g. a translator's whole translated
    # document handed to a following synthesizer/writer step) can be just as large as
    # context_text (raw source content) — both need the same batching/hardware guards.
    # Gating on `not working_text` (as this used to) meant a second step inheriting a
    # huge working_text skipped both checks entirely and went straight to an unbatched
    # single-shot call, which is exactly the "too large for context window" failure
    # this is meant to prevent.
    batch_source = working_text or context_text
    batch_roles = (
        "translator",
        "summarizer",
        "extractor",
        "writer",
        "editor",
        "synthesizer",
        # cross-document compare/synthesize queries (see context/strategy.py's
        # _CROSS_SOURCE_COMPARE → "map_reduce") land here as general_answer — without this,
        # a large combined multi-document context goes to the model in one unbatched call
        # and can silently exceed its context window instead of being chunked and merged.
        "general_answer",
    )
    if batch_source and role_id in batch_roles:
        from pipeline.direct.batch_processor import strip_context_source_headers

        batch_ctx = strip_context_source_headers(batch_source)
        source_segments = None
        if not working_text:
            # working_text means this step's input is a prior step's already-merged output,
            # not the raw per-document set — segmenting only applies to genuine multi-document
            # source content, so it's skipped once a plan has chained past the first step.
            digests = getattr(bundle, "source_digests", None) or []
            if len(digests) >= 2:
                from pipeline.context.digest_store import per_digest_bodies

                source_segments = per_digest_bodies(digests)
        out = maybe_batched_transform(
            batch_ctx,
            role_id=role_id,
            step_intent=step.intent or cfg.user_query,
            profile=cfg.profile,
            model=use_model,
            system=system,
            sink=cfg.sink,
            is_cancelled=cfg.is_cancelled,
            stream=True,
            source_segments=source_segments,
        )
        if out:
            return out.strip()

    if role_id in _FINAL_COMBINED_ROLES and contract.max_chars:
        from pipeline.direct.batch_budget import output_exceeds_hardware, resolve_batch_budget

        probe_chars = len(system) + len(batch_source or "") + len(step.intent or cfg.user_query or "")
        if output_exceeds_hardware(resolve_batch_budget(cfg.profile, use_model), probe_chars, contract.max_chars):
            from pipeline.direct.batch_processor import generate_sectioned_writer

            out = generate_sectioned_writer(
                step_intent=step.intent or cfg.user_query,
                context_text=(batch_source or "")[:4000],
                profile=cfg.profile,
                model=use_model,
                system=system,
                target_output_chars=contract.max_chars,
                sink=cfg.sink,
                is_cancelled=cfg.is_cancelled,
                stream=stream,
            )
            if out:
                return out.strip()

    user_content = _generation_user_content(
        role_id=role_id,
        step=step,
        cfg=cfg,
        step_body=step_body,
        working_text=working_text,
        context_text=context_text,
    )
    user_msg["content"] = user_content
    messages[-1] = user_msg

    from pipeline.direct.batch_budget import (
        fit_budget_to_prompt,
        llm_extra_options,
        resolve_batch_budget,
    )

    prompt_chars = sum(len(str(m.get("content") or "")) for m in messages)
    # Every role resolves to a contract with a max_chars estimate (chat_contracts.py,
    # document_contracts.py, etc.) — widening num_predict from it isn't just for the
    # from-scratch writer roles; a long review/critique/translation reply needs the
    # same headroom or it silently hits the token cap mid-answer.
    expected_output_chars = contract.max_chars or 0
    budget = fit_budget_to_prompt(
        resolve_batch_budget(cfg.profile, use_model),
        prompt_chars,
        expected_output_chars,
        profile=cfg.profile,
    )
    llm_opts = llm_extra_options(budget)
    if plan.output_type == "presentation" and role_id in (
        "slide_author",
        "deck_planner",
    ):
        llm_opts["num_predict"] = max(int(llm_opts.get("num_predict", 2048)), 6144)
    if stream:
        streamed = stream_chat_response(
            profile=cfg.profile,
            model=use_model,
            messages=messages,
            sink=cfg.sink,
            is_cancelled=cfg.is_cancelled,
            disable_thinking=True,
            extra_options=llm_opts,
            stream_base=stream_base if isolated_summary else "",
        )
        from services.session import state

        if streamed:
            out = streamed
        elif role_id == "image_prompt_author":
            # Internal step — its result only ever feeds prepare_image_prompt(body or
            # user_query) downstream, which already falls back safely to the raw user
            # query when body is empty. state.messages[-1] is the user-facing chat
            # bubble, not this internal step's own output — reading it here risked
            # picking up unrelated leftover content instead of a clean empty fallback.
            out = ""
        else:
            out = (state.messages[-1].get("content") or "").strip() if state.messages else ""
        if role_id == "translator" and out:
            from pipeline.direct.batch_processor import finalize_translation_output

            out = finalize_translation_output(out)
            if state.messages and state.messages[-1].get("role") == "assistant":
                state.messages[-1]["content"] = out
            cfg.sink.set_assistant_content(out)
            cfg.sink.refresh_chat()
    else:
        out = generate_text_sync(
            cfg.profile,
            use_model,
            messages,
            disable_thinking=True,
            sink=cfg.sink,
            extra_options=llm_opts,
        )

    valid, hint = validate_or_repair_prompt(out, contract)
    if not valid and out and not cfg.is_cancelled():
        repair = (
            f"Repair output for role '{role_id}'.\n{hint}\n\nCurrent output:\n{out}"
        )
        out = generate_text_sync(
            cfg.profile,
            use_model,
            messages + [{"role": "user", "content": repair}],
        )
    from pipeline.instruction_priority import strip_priority_preamble_echo

    return strip_priority_preamble_echo((out or "").strip())


def _run_mutation_step(
    *,
    step: PlannedStep,
    units: list[dict],
    instruction: str,
    model: str,
    profile_hint: str,
    selection_text: str,
    all_units: list[dict] | None = None,
) -> dict[str, str]:
    sliced = slice_mutation_units(units, step.scope, selection_text=selection_text)
    role_ids = mutation_role_ids(step.roles)
    from services.office_mutation.plan import plan_mutation_map

    return plan_mutation_map(
        sliced,
        instruction or step.intent,
        model,
        profile_hint=profile_hint,
        capability_id=CAPABILITY_ID,
        role_ids=role_ids,
        all_units=all_units or units,
    )


def _apply_mutation_map_to_units(units: list[dict], text_map: dict[str, str]) -> list[dict]:
    updated = []
    for u in units:
        uid = u["id"]
        nu = dict(u)
        if uid in text_map:
            val = text_map[uid]
            nu["text"] = val
            nu["value"] = val
        updated.append(nu)
    return updated


def _compile_deliverable(
    *,
    body: str,
    output_type: str,
    mode: str,
    bundle: Any,
    model: str,
    prof: dict,
    sink: Any,
    state: Any,
    chart_artifacts: list | None = None,
    planned_deck_spec: Any = None,
    presentation_style: str = "",
    presentation_grounded: bool = False,
) -> str | None:
    from services.session import draft as draft_sync
    from services.session.artifact import save_preview_to_artifact

    ot = normalize_output_type(output_type)
    from pipeline.instruction_priority import strip_priority_preamble_echo

    body = strip_priority_preamble_echo((body or "").strip())
    draft_sync.set_draft(body, ot, mode)
    state.live_workspace_output_type = ot
    state.live_workspace_mode = mode

    if ot == "chat":
        return None

    if looks_like_refusal(body):
        from pipeline.i18n import t as tr

        sink.log("Model returned a refusal/non-answer instead of the document body — not compiling it.")
        sink.set_assistant_content(tr("chat.artifact_refusal", label=deliverable_display_name(ot)))
        sink.refresh_chat()
        return None

    from services.session.artifact import resolve_generated_stem

    source_names = [
        getattr(ps, "name", "")
        for ps in (getattr(bundle, "parsed_sources", None) or [])
        if getattr(ps, "name", "")
    ]
    query = (getattr(state, "last_user_instruction", None) or "").strip()
    stem = resolve_generated_stem(
        query=query,
        source_names=source_names or None,
        output_type=ot,
        content=body,
    )
    orig_name = stem or getattr(bundle, "original_filename", None) or "output"
    if ot == "presentation":
        from pipeline.deliverables.presentation_direct import prepare_direct_presentation
        from pipeline.deliverables.presentation_compile import compile_agentic_presentation
        from services.session import draft as draft_sync

        query = (getattr(state, "last_user_instruction", None) or "").strip()
        from services.presentation_markdown import extract_compile_markdown

        body = extract_compile_markdown(body)
        prep = prepare_direct_presentation(
            body,
            query=query,
        )
        body = prep.markdown
        if planned_deck_spec is not None:
            from services.presentation_markdown import split_presentation_slides
            from pipeline.deliverables.presentation_reattach import (
                reattach_notes_and_visuals,
            )

            expanded_count = len(split_presentation_slides(body))
            if expanded_count != len(planned_deck_spec.slides):
                # slide_author dropped the required --- Slide N --- structure or
                # changed slide count — position-based reattachment would misalign.
                # Fall back to the deck planner's own (validated, complete-sentence)
                # copy rather than compile a structurally broken deck.
                sink.log(
                    "Slide author output didn't match planned structure "
                    f"({expanded_count} vs {len(planned_deck_spec.slides)} slides) — "
                    "using deck planner copy instead."
                )
                from pipeline.deliverables.presentation_theme import theme_meta_block

                deck_md = planned_deck_spec.to_markdown()
                body = f"{theme_meta_block(prep.theme)}\n\n{deck_md}" if prep.theme else deck_md
            else:
                body = reattach_notes_and_visuals(body, planned_deck_spec)
        draft_sync.set_draft(body, ot, mode)
        # `orig_name` here is a bare stem (from resolve_generated_stem), never
        # meant to carry an extension — compile_agentic_presentation() strips
        # any extension and appends "_LOMA.pptx" itself. A stray "must already
        # end in .pptx" check here used to discard every real query-derived
        # stem and silently fall back to the generic name below, every time —
        # only replace it when it's actually a generic placeholder.
        stem = os.path.splitext(orig_name)[0].lower()
        if not orig_name or stem in ("output", "document"):
            orig_name = "presentation.pptx"
        generated_dir = os.path.join("data", "generated")
        os.makedirs(generated_dir, exist_ok=True)
        from services.session import state as loma_state

        path = compile_agentic_presentation(
            body,
            orig_name,
            prep.theme,
            query=query,
            include_images=prep.include_images,
            slide_visuals=prep.slide_visuals,
            log_fn=sink.log,
            # Without these, services.marker_visual.resolve_marker_visual()
            # always falls straight to a plain diffusion photo (its
            # diagram/chart/infographic branch requires both) — so a marker
            # that explicitly describes a flowchart/bar chart/comparison
            # diagram still rendered as an SD photo, and diffusion models
            # can't spell: the result is a garbled fake-diagram image with
            # nonsense text baked in instead of a real, legible rendered one.
            prof=prof,
            model=model,
            presentation_style=presentation_style,
            presentation_grounded=presentation_grounded,
            settings=loma_state.current_settings,
        )
        if path and os.path.isfile(path):
            state.last_generated_file_path = path
            state.artifact_ready = True
            sink.log(f"Presentation compiled → {path}")
            return path
        from services.presentation_markdown import ensure_deck_structure, normalize_presentation_markdown

        body = ensure_deck_structure(normalize_presentation_markdown(body), deck_title=query[:80])
        draft_sync.set_draft(body, ot, mode)
    path = save_preview_to_artifact(
        output_type=ot,
        original_filename=orig_name,
        gen_model=model,
        mode=mode,
        profile=prof,
        log_fn=sink.log,
        use_editor=False,
    )
    return path


def _run_image_composite(
    *,
    user_query: str,
    bundle: Any,
    sink: Any,
    state: Any,
) -> dict[str, Any]:
    from services.image_composite import run_composite_edit

    paths = _image_paths_from_bundle(bundle)
    if len(paths) < 2:
        return {"content": "", "path": "", "output_type": "image", "error": "need two images"}

    target_path, donor_path = paths[0], paths[1]
    out_dir = os.path.join("data", "generated", "images")
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(target_path))[0]
    out_path = os.path.join(out_dir, f"{stem}_composite.png")
    sink.log("Compositing subjects from attached images…")
    try:
        result = run_composite_edit(
            source_path=target_path,
            donor_path=donor_path,
            instruction=user_query,
            output_path=out_path,
        )
    except Exception as ex:
        sink.log(f"Image composite failed: {ex}")
        sink.set_assistant_content(f"Image composite failed: {ex}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": str(ex)}

    path = (result.path or "") if result else ""
    if path and os.path.exists(path):
        from services.session import draft as draft_sync

        state.last_generated_file_path = path
        state.artifact_ready = True
        state.preview_dirty = False
        state.live_workspace_output_type = "image"
        draft_sync.set_draft(user_query or "", "image", "generation")
        sink.notify_artifact_ready(path)
        sink.log(f"Image saved → {path}")
        brief = (user_query or "").strip()
        if len(brief) > 220:
            brief = brief[:217] + "…"
        msg = (
            f"**Composite:** {brief}\n\n"
            f"✅ **Image ready** — `{os.path.basename(path)}`."
        )
        sink.set_assistant_content(msg)
        if state.messages and state.messages[-1].get("role") == "assistant":
            state.messages[-1]["content"] = msg
        sink.refresh_chat()
        try:
            from services.session.workflow_control import schedule_on_ui
            from ui.components.preview_workspace import refresh_preview_panel

            schedule_on_ui(refresh_preview_panel)
        except Exception:
            pass
    return {"content": user_query, "path": path, "output_type": "image"}


def _make_diffusion_progress_cb(sink: Any):
    """Progress callback factory for diffusion-backed generation paths other than
    plain photo generation (chat image edit/mutation, poster) — drives the same
    live step/percent process-indicator UI _run_image_generation's own inline
    callback already uses, so these paths stop looking "stuck" while a
    multi-second diffusion run is in progress."""
    from services.image_generation import ImageGenerationCancelled
    from services.session.workflow_control import is_cancelled

    def _on_progress(step: int, total: int) -> None:
        if is_cancelled():
            raise ImageGenerationCancelled()
        if total > 0:
            pct = round(100 * step / total)
            sink.log(f"Generating… step {step}/{total} ({pct}%)")
            from pipeline.i18n import t as tr
            from services.session import state as _state
            from services.session.workflow_control import schedule_on_ui

            _state.progress_detail = tr(
                "chat.image_generating_progress", step=step, total=total, pct=pct
            )

            def _refresh() -> None:
                from ui.components.process_indicator import render_progress

                render_progress.refresh()

            schedule_on_ui(_refresh)

    return _on_progress


def _set_generating_status(key: str) -> None:
    """Show a simple localized 'Generating…' status in the process-indicator UI
    for render paths (diagram/infographic/chart) that have no per-step diffusion
    progress to report — so they show *some* live feedback instead of nothing
    until the whole render finishes."""
    from pipeline.i18n import t as tr
    from services.session import state as _state
    from services.session.workflow_control import schedule_on_ui

    _state.progress_detail = tr(key)

    def _refresh() -> None:
        from ui.components.process_indicator import render_progress

        render_progress.refresh()

    schedule_on_ui(_refresh)


def _run_image_mutation(
    *,
    user_query: str,
    bundle: Any,
    sink: Any,
    state: Any,
) -> dict[str, Any]:
    """Edit an attached image in place (img2img / inpaint / recolor / composite)."""
    from pipeline.image_mutation_dispatch import dispatch_image_mutation

    paths = _image_paths_from_bundle(bundle)
    if not paths:
        sink.set_assistant_content("No source image found to edit — attach an image first.")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": "no source image"}

    from services.image_generation import GENERATED_IMAGE_DIR, resolve_image_presets, unique_output_path
    from services.image_model_prefs import get_image_model_prefs
    from services.model_router import get_default_image_model_from_settings

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    out_path = unique_output_path(paths[0], "edit")
    sink.log("Editing attached image…")

    # Respect the user's saved default model + quality preference here too — this
    # path (chat "Edit: ..." mutation) previously always used each diffusion
    # function's own hardcoded strength/steps defaults regardless of settings, the
    # same bug already fixed for poster generation.
    image_model_id = get_default_image_model_from_settings()
    prefs = get_image_model_prefs(image_model_id)
    quality_mode = prefs.get("quality_mode", "")
    preset_steps, _preset_width, _preset_height = resolve_image_presets(
        image_model_id, quality_mode=quality_mode, resolution_preset=prefs.get("resolution_preset", ""),
    )
    progress_cb = _make_diffusion_progress_cb(sink)

    try:
        result = dispatch_image_mutation(
            source_paths=paths,
            instruction=user_query,
            output_path=out_path,
            steps=preset_steps,
            model_id=image_model_id,
            progress_cb=progress_cb,
        )
    except Exception as ex:
        sink.log(f"Image edit failed: {ex}")
        sink.set_assistant_content(f"Image edit failed: {ex}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": str(ex)}

    if result is not None and getattr(result, "fallback", False):
        msg = result.error or "Couldn't complete this edit."
        sink.log(msg)
        sink.set_assistant_content(f"⚠️ {msg}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": msg}

    path = (result.path or "") if result else ""
    if path and os.path.exists(path):
        from services.session import draft as draft_sync

        state.last_generated_file_path = path
        state.artifact_ready = True
        state.preview_dirty = False
        state.live_workspace_output_type = "image"
        draft_sync.set_draft(user_query or "", "image", "mutation")
        sink.notify_artifact_ready(path)
        sink.log(f"Image saved → {path}")
        brief = (user_query or "").strip()
        if len(brief) > 220:
            brief = brief[:217] + "…"
        msg = (
            f"**Edit:** {brief}\n\n"
            f"✅ **Image ready** — `{os.path.basename(path)}`."
        )
        sink.set_assistant_content(msg)
        if state.messages and state.messages[-1].get("role") == "assistant":
            state.messages[-1]["content"] = msg
        sink.refresh_chat()
        try:
            from services.session.workflow_control import schedule_on_ui
            from ui.components.preview_workspace import refresh_preview_panel

            schedule_on_ui(refresh_preview_panel)
        except Exception:
            pass
    return {"content": user_query, "path": path, "output_type": "image"}


def _finish_rendered_image(
    *, user_query: str, path: str, sink: Any, state: Any, message: str
) -> dict[str, Any]:
    """Shared success handler for poster/diagram results — mirrors the pattern in
    _run_image_mutation/_run_image_generation (state bookkeeping, chat message,
    preview refresh) so posters/diagrams look and behave the same as any other
    generated image once produced."""
    from services.session import draft as draft_sync

    state.last_generated_file_path = path
    state.artifact_ready = True
    state.preview_dirty = False
    state.live_workspace_output_type = "image"
    draft_sync.set_draft(user_query or "", "image", "generation")
    sink.notify_artifact_ready(path)
    sink.log(f"Image saved → {path}")
    sink.set_assistant_content(message)
    if state.messages and state.messages[-1].get("role") == "assistant":
        state.messages[-1]["content"] = message
    sink.refresh_chat()
    try:
        from services.session.workflow_control import schedule_on_ui
        from ui.components.preview_workspace import refresh_preview_panel

        schedule_on_ui(refresh_preview_panel)
    except Exception:
        pass
    return {"content": user_query, "path": path, "output_type": "image"}


def _run_poster_style_picker(
    *, user_query: str, prof: dict, config: dict, sink: Any, state: Any, bundle: Any = None,
) -> dict[str, Any]:
    """Show the poster template picker instead of generating immediately. The
    click handler resumes generation with the exact same locals
    _run_poster_generation would have received directly, plus the user's
    chosen template — via run_generation_on_client so progress, streaming,
    the busy send-button state, and the final ready notification all still
    work (see that function's docstring for why a bare background thread
    silently breaks all of that)."""
    import uuid

    from pipeline.i18n import t as tr
    from ui.components.style_picker import run_generation_on_client, show_poster_style_picker
    from ui.themes import registry

    token = uuid.uuid4().hex
    msg_ref: dict | None = None

    def _clear_reopen_button() -> None:
        registry.style_picker_reopeners.pop(token, None)
        if msg_ref is not None and msg_ref.get("style_picker_token"):
            msg_ref["style_picker_token"] = None
            sink.refresh_chat()

    def _on_select(template_id: str) -> None:
        _clear_reopen_button()
        run_generation_on_client(
            lambda: _run_poster_generation(
                user_query=user_query, prof=prof, config=config, sink=sink, state=state,
                bundle=bundle, template=template_id,
            ),
            sink=sink,
        )

    def _reopen() -> None:
        show_poster_style_picker(on_select=_on_select)

    registry.style_picker_reopeners[token] = _reopen
    show_poster_style_picker(on_select=_on_select)
    sink.set_assistant_content(tr("poster.style_picker.chat_prompt"))
    # Tags this message so chat_message.py can render a "reopen picker"
    # button — see the matching comment in pipeline/direct/entry.py's
    # presentation picker for why.
    if state.messages:
        msg_ref = state.messages[-1]
        msg_ref["style_picker_token"] = token
    sink.refresh_chat()
    return {"content": user_query, "path": "", "output_type": "image"}


def _run_poster_generation(
    *, user_query: str, prof: dict, config: dict, sink: Any, state: Any, bundle: Any = None,
    template: str | None = None,
) -> dict[str, Any]:
    from services.model_router import any_image_model_installed, image_generation_deps_available

    deps_ok, _ = image_generation_deps_available()
    if not deps_ok:
        from services.capability.gap_handler import offer_image_gen_installer

        offer_image_gen_installer()
        sink.set_assistant_content(
            "Image generation requires Diffusers and PyTorch. Use the installer dialog."
        )
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image"}

    if not any_image_model_installed():
        from pipeline.i18n import t as tr

        sink.set_assistant_content(tr("chat.image_no_model_installed"))
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image"}

    sink.log("Authoring poster background + text…")
    _set_generating_status("chat.poster_generating")
    from services.model_router import resolve_general_model
    from pipeline.base.grounding import resolve_generation_context

    model = resolve_general_model(prof)
    context, _sources = resolve_generation_context(
        user_query, bundle=bundle, settings=state.current_settings, broad_trigger=True, log_fn=sink.log,
    )
    try:
        from services.poster_generation import generate_poster

        result = generate_poster(
            user_query, prof=prof, model=model, context=context,
            progress_cb=_make_diffusion_progress_cb(sink), template=template,
        )
    except Exception as ex:
        sink.log(f"Poster generation failed: {ex}")
        sink.set_assistant_content(f"Poster generation failed: {ex}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": str(ex)}

    if result.fallback or not os.path.exists(result.path):
        sink.set_assistant_content(f"⚠️ Couldn't generate the poster: {result.error or 'unknown error'}")
        sink.refresh_chat()
        return {"content": user_query, "path": result.path, "output_type": "image", "error": result.error}

    msg = f"✅ **Poster ready** — `{os.path.basename(result.path)}`."
    return _finish_rendered_image(user_query=user_query, path=result.path, sink=sink, state=state, message=msg)


def _run_diagram_generation(
    *, user_query: str, prof: dict, config: dict, sink: Any, state: Any, bundle: Any = None,
) -> dict[str, Any]:
    sink.log("Authoring diagram steps…")
    _set_generating_status("chat.diagram_generating")
    from services.model_router import resolve_general_model
    from pipeline.base.grounding import resolve_generation_context

    model = resolve_general_model(prof)
    # broad_trigger=False: most diagrams visualize the user's own process/workflow,
    # not an external fact that needs verifying — grounded only when a source is
    # attached, or the request phrasing itself clearly needs current web facts.
    context, _sources = resolve_generation_context(
        user_query, bundle=bundle, settings=state.current_settings, broad_trigger=False, log_fn=sink.log,
    )
    try:
        from services.diagram_generation import generate_diagram

        result, limitation_note = generate_diagram(user_query, prof=prof, model=model, context=context)
    except Exception as ex:
        sink.log(f"Diagram generation failed: {ex}")
        sink.set_assistant_content(f"Diagram generation failed: {ex}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": str(ex)}

    if not os.path.exists(result.path):
        sink.set_assistant_content("⚠️ Couldn't generate the diagram.")
        sink.refresh_chat()
        return {"content": user_query, "path": result.path, "output_type": "image", "error": "render failed"}

    msg = f"✅ **Diagram ready** — `{os.path.basename(result.path)}`."
    if limitation_note:
        msg += f"\n\n_{limitation_note}_"
    return _finish_rendered_image(user_query=user_query, path=result.path, sink=sink, state=state, message=msg)


def _run_infographic_generation(
    *, user_query: str, kind: str, prof: dict, config: dict, sink: Any, state: Any, bundle: Any = None,
) -> dict[str, Any]:
    """kind is "infographic_stat", "infographic_timeline", or
    "infographic_comparison" — the three layouts share this wrapper
    (author → render → _finish_rendered_image) just like
    _run_poster_generation/_run_diagram_generation."""
    from pipeline.i18n import t as tr

    labels = {"infographic_stat": "stat grid", "infographic_timeline": "timeline", "infographic_comparison": "comparison"}
    sink.log(f"Authoring {labels.get(kind, 'infographic')}…")
    _set_generating_status("chat.infographic_generating")
    from services.model_router import resolve_general_model
    from pipeline.base.grounding import resolve_generation_context

    model = resolve_general_model(prof)
    # broad_trigger=True: an infographic's entire purpose is presenting real facts,
    # so ground by default (attached source, else a web search) rather than only
    # when the phrasing itself screams "current event".
    context, _sources = resolve_generation_context(
        user_query, bundle=bundle, settings=state.current_settings, broad_trigger=True, log_fn=sink.log,
    )
    try:
        if kind == "infographic_stat":
            from services.infographic_generation import generate_stat_grid as generate_fn
        elif kind == "infographic_comparison":
            from services.infographic_generation import generate_comparison as generate_fn
        else:
            from services.infographic_generation import generate_timeline as generate_fn

        result = generate_fn(user_query, prof=prof, model=model, context=context)
    except Exception as ex:
        sink.log(f"Infographic generation failed: {ex}")
        sink.set_assistant_content(f"Infographic generation failed: {ex}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": str(ex)}

    if result.fallback or not os.path.exists(result.path):
        sink.set_assistant_content(f"⚠️ Couldn't generate the infographic: {result.error or 'unknown error'}")
        sink.refresh_chat()
        return {"content": user_query, "path": result.path, "output_type": "image", "error": result.error}

    msg = f"✅ **Infographic ready** — `{os.path.basename(result.path)}`."
    if kind == "infographic_stat":
        # Only the stat-grid layout uses the fixed built-in icon set — the
        # comparison table and timeline don't draw icons, so the note doesn't apply.
        msg += f"\n\n_{tr('infographic.limitation_icons')}_"
    return _finish_rendered_image(user_query=user_query, path=result.path, sink=sink, state=state, message=msg)


def _run_chart_generation(
    *, user_query: str, prof: dict, config: dict, sink: Any, state: Any, bundle: Any = None,
) -> dict[str, Any]:
    sink.log("Authoring chart data…")
    _set_generating_status("chat.chart_generating")
    from services.model_router import resolve_general_model
    from pipeline.base.grounding import resolve_generation_context

    model = resolve_general_model(prof)
    # broad_trigger=True: a chart states real data by definition, same policy as
    # the infographic layouts.
    context, sources = resolve_generation_context(
        user_query, bundle=bundle, settings=state.current_settings, broad_trigger=True, log_fn=sink.log,
    )
    try:
        from services.chart_generation import generate_chart

        result = generate_chart(user_query, prof=prof, model=model, context=context, sources=sources)
    except Exception as ex:
        sink.log(f"Chart generation failed: {ex}")
        sink.set_assistant_content(f"Chart generation failed: {ex}")
        sink.refresh_chat()
        return {"content": user_query, "path": "", "output_type": "image", "error": str(ex)}

    if not os.path.exists(result.path):
        sink.set_assistant_content("⚠️ Couldn't generate the chart.")
        sink.refresh_chat()
        return {"content": user_query, "path": result.path, "output_type": "image", "error": "render failed"}

    msg = f"✅ **Chart ready** — `{os.path.basename(result.path)}`."
    return _finish_rendered_image(user_query=user_query, path=result.path, sink=sink, state=state, message=msg)


_VARIATION_COUNT_RE = re.compile(r"\b([2-4])\b")


def _requested_variation_count(user_query: str) -> int | None:
    """None when the user just wants one image (today's behavior, unchanged).
    Returns 2-4 when they asked for multiple options/variations — an explicit
    count in the query wins ("give me 3 options"), otherwise a vague "a few"
    defaults to 3."""
    from pipeline.query_intent_i18n import matches

    if not matches(user_query, "wants_variations"):
        return None
    m = _VARIATION_COUNT_RE.search(user_query or "")
    return int(m.group(1)) if m else 3


def _run_image_generation(
    *,
    body: str,
    user_query: str,
    bundle: Any,
    prof: dict,
    config: dict,
    sink: Any,
    state: Any,
) -> dict[str, Any]:
    from services.image_generation import generate_image_verified, prepare_image_prompt
    from services.model_router import any_image_model_installed, image_generation_deps_available

    deps_ok, _ = image_generation_deps_available()
    if not deps_ok:
        from services.capability.gap_handler import offer_image_gen_installer

        offer_image_gen_installer()
        sink.set_assistant_content(
            "Image generation requires Diffusers and PyTorch. Use the installer dialog."
        )
        sink.refresh_chat()
        return {"content": body, "path": "", "output_type": "image"}

    if not any_image_model_installed():
        from pipeline.i18n import t as tr

        sink.set_assistant_content(tr("chat.image_no_model_installed"))
        sink.refresh_chat()
        return {"content": body, "path": "", "output_type": "image"}

    prompt = prepare_image_prompt(body or user_query)
    from pipeline.output_format import infer_image_format_from_query

    image_format = infer_image_format_from_query(user_query) or "png"

    from services.image_generation import ImageGenerationCancelled
    from services.session.workflow_control import is_cancelled

    def _on_progress(step: int, total: int) -> None:
        if is_cancelled():
            # Raised from inside the diffusers per-step callback (see
            # run_pipe_with_progress) — the only way to actually interrupt an
            # in-flight generation; the Stop button previously had no effect once
            # diffusion started because nothing checked cancellation mid-loop.
            raise ImageGenerationCancelled()
        if total > 0:
            pct = round(100 * step / total)
            sink.log(f"Generating image… step {step}/{total} ({pct}%)")
            from pipeline.i18n import t as tr
            from services.session import state as _state
            from services.session.workflow_control import schedule_on_ui

            _state.progress_detail = tr(
                "chat.image_generating_progress", step=step, total=total, pct=pct
            )

            def _refresh() -> None:
                from ui.components.process_indicator import render_progress

                render_progress.refresh()

            schedule_on_ui(_refresh)

    from services.image_generation import resolve_image_presets
    from services.image_model_prefs import get_image_model_prefs
    from services.model_router import get_default_image_model_from_settings

    image_model_id = config.get("image_model") or get_default_image_model_from_settings()
    prefs = get_image_model_prefs(image_model_id)
    quality_mode = prefs.get("quality_mode", "")
    preset_steps, preset_width, preset_height = resolve_image_presets(
        image_model_id,
        quality_mode=quality_mode,
        resolution_preset=prefs.get("resolution_preset", ""),
    )

    variation_count = _requested_variation_count(user_query)
    try:
        if variation_count:
            from services.image_variations import generate_variations_contact_sheet

            sink.log(f"Generating {variation_count} variations…")
            result = generate_variations_contact_sheet(
                prompt,
                variation_count,
                width=config.get("width") or preset_width,
                height=config.get("height") or preset_height,
                steps=config.get("steps") or preset_steps,
                guidance_scale=config.get("guidance") or config.get("guidance_scale"),
                model_id=image_model_id,
                quality_mode=quality_mode,
                progress_cb=_on_progress,
            )
        else:
            # A standalone request can't come back with "no image" the way a pptx/docx
            # slide can (see generate_image_verified()'s docstring) — max_reseeds=2
            # means up to 3 total generations before falling back to mutate_remove_text()
            # (inpaint over the detected text region) as the last resort, rather than
            # either giving up or shipping a garbled image outright.
            result, still_has_text = generate_image_verified(
                prompt,
                max_reseeds=2,
                width=config.get("width") or preset_width,
                height=config.get("height") or preset_height,
                steps=config.get("steps") or preset_steps,
                guidance_scale=config.get("guidance") or config.get("guidance_scale"),
                model_id=image_model_id,
                quality_mode=quality_mode,
                output_format=image_format,
                name_hint=user_query,
                progress_cb=_on_progress,
            )
            if still_has_text and result.path and os.path.exists(result.path):
                from services.image_generation import mutate_remove_text

                sink.log("Reseeding didn't clear up garbled text — repainting the affected region…")
                mutated = mutate_remove_text(result.path, prompt, model_id=image_model_id)
                if not mutated.fallback:
                    result = mutated
    except ImageGenerationCancelled:
        from pipeline.i18n import t as tr

        sink.log("Image generation cancelled by user.")
        sink.set_assistant_content(tr("chat.image_generation_cancelled"))
        sink.refresh_chat()
        return {"content": prompt, "path": "", "output_type": "image", "cancelled": True}
    except Exception as ex:
        sink.log(f"Image generation failed: {ex}")
        sink.set_assistant_content(f"Image generation failed: {ex}")
        sink.refresh_chat()
        return {"content": prompt, "path": "", "output_type": "image", "error": str(ex)}

    path = (result.path or "") if result else ""
    if path and os.path.exists(path):
        from services.session import draft as draft_sync

        state.last_generated_file_path = path
        state.artifact_ready = True
        state.preview_dirty = False
        state.live_workspace_output_type = "image"
        state.last_image_diffusion_prompt = prompt
        draft_sync.set_draft(prompt, "image", "generation")
        sink.notify_artifact_ready(path)
        sink.log(f"Image saved → {path}")

        from services.image_generation import looks_like_multi_subject
        from services.image_model_prefs import build_image_regen_hint

        regen_hint = build_image_regen_hint(image_model_id, quality_mode)
        caveat = ""
        # SD family (CLIP text conditioning) has documented weak compositional/attribute
        # binding on two-named-subject prompts — FLUX's Qwen3 text encoder doesn't share
        # CLIP's specific failure mode here, so the caveat would be misleading for it.
        from config.model_catalog import image_pipeline_kind

        if image_pipeline_kind(image_model_id) != "flux_klein_gguf" and looks_like_multi_subject(
            user_query
        ):
            from pipeline.i18n import t as tr

            caveat = f"\n\n{tr('chat.image_multi_subject_caveat')}"
        msg = (
            f"**Image prompt:**\n{prompt}\n\n"
            f"✅ **Image ready** — `{os.path.basename(path)}`.\n\n"
            f"{regen_hint}{caveat}"
        )
        sink.set_assistant_content(msg)
        if state.messages and state.messages[-1].get("role") == "assistant":
            state.messages[-1]["content"] = msg
        sink.refresh_chat()
        try:
            from services.session.workflow_control import schedule_on_ui
            from ui.components.preview_workspace import refresh_preview_panel

            schedule_on_ui(refresh_preview_panel)
        except Exception:
            pass
    return {"content": prompt, "path": path, "output_type": "image"}


def run_direct_pipeline(
    inputs: dict[str, Any],
    config: dict[str, Any],
    plan: DirectPlan,
) -> dict[str, Any]:
    """Execute a DirectPlan end-to-end."""
    state = inputs["state"]
    sink = inputs["sink"]
    request = inputs["request"]
    bundle = inputs["bundle"]
    prof = inputs.get("prof") or {}
    profile_id = inputs.get("profile_id") or ""
    gen_model = inputs.get("gen_model") or config.get("model") or ""
    workflow_instruction = inputs.get("workflow_instruction") or ""

    from services.session.workflow_control import is_cancelled
    from services.model_router import resolve_general_model, needs_vision, resolve_chat_model
    from pipeline.base.profile_pack import build_system_instruction, build_mutation_hint
    from pipeline.capability_runtime.execution_config import execution_mode_from_config

    output_type = normalize_output_type(plan.output_type or config.get("output_type") or "chat")
    mode = plan.mode or config.get("mode") or "generation"
    execution_mode = execution_mode_from_config(config)

    class _RoutingOutput:
        def __init__(self, ot: str, m: str | None):
            self.output_type = ot
            self.mode = m

    system_instruction = build_system_instruction(
        prof,
        profile_id,
        _RoutingOutput(output_type, mode),
        user_query=request.user_input,
    )

    if output_type == "presentation" and mode == "generation":
        from pipeline.deliverables.presentation_brief import build_presentation_brief

        system_instruction += f"\n\n{build_presentation_brief(request.user_input).to_system_block()}"

    sink.ensure_assistant_message()
    sink.set_assistant_content("")
    sink.refresh_chat()

    llm_type = (config.get("llm_type") or "text").strip().lower()
    bundle_images = list(getattr(bundle, "images", None) or [])
    chat_model, vision_error = resolve_chat_model(
        prof, llm_type, bundle_images, context_files=getattr(request, "context_files", None) or []
    )
    if vision_error and bundle_images:
        sink.set_assistant_content(vision_error)
        sink.refresh_chat()
        return {"content": "", "output_type": "chat"}
    model = chat_model or gen_model or resolve_general_model(prof)

    user_message = build_step_input_payload(
        step=plan.steps[0] if plan.steps else PlannedStep("", ["general_answer"], "chat_default"),
        mode=mode,
        output_type=output_type,
        bundle=bundle,
    )
    if not user_message:
        user_message = f"User Request:\n{request.user_input}"

    user_entry: dict[str, Any] = {"role": "user", "content": user_message}
    if bundle_images:
        user_entry["images"] = bundle_images

    pipeline_cfg = RolePipelineConfig(
        mode=execution_mode,
        base_system_instruction=system_instruction,
        profile=prof,
        model=model,
        user_query=request.user_input,
        role_ids=[],
        messages=[
            {"role": "system", "content": system_instruction},
            user_entry,
        ],
        sink=sink,
        is_cancelled=is_cancelled,
        stream_final=False,
        capability_id=CAPABILITY_ID,
        presentation_style=str(config.get("presentation_style") or ""),
    )

    working_text = ""
    mutation_units: list[dict] = []
    selection_text = (getattr(state, "preview_selection", None) or "").strip()

    if mode == "mutation":
        from services.source_parser import primary_mutation_source

        office_source = primary_mutation_source(getattr(bundle, "parsed_sources", None) or [])
        if office_source and office_source.mutation_units:
            mutation_units = [dict(u) for u in office_source.mutation_units]
        elif office_source and office_source.path:
            from services.office_mutation.extract import extract_units

            mutation_units = extract_units(office_source.path)

    steps = plan.steps or [
        PlannedStep(request.user_input, ["general_answer"], "chat_default")
    ]

    if mode == "mutation" and mutation_units:
        import copy

        original_mutation_units = copy.deepcopy(mutation_units)
        merged_map: dict[str, str] = {
            u["id"]: u.get("text") or u.get("value") or "" for u in mutation_units
        }
        profile_hint = build_mutation_hint(prof)
        from services.office_mutation.unit_filter import (
            affected_slide_numbers,
            filter_mutation_units,
            group_units_by_slide,
        )

        for idx, step in enumerate(steps):
            if is_cancelled():
                break
            instruction = (step.intent or request.user_input).strip()
            sink.log(f"Direct mutation step {idx + 1}/{len(steps)}: {instruction[:80]}")
            target_units = filter_mutation_units(
                mutation_units, instruction, scope=step.scope
            )
            selective_step = step.roles and step.roles[0] in (
                "selective_translator",
                "mutation_selective_translator",
            )
            if output_type == "presentation":
                from services.office_mutation.unit_filter import count_spans_in_units

                slide_nums = affected_slide_numbers(target_units, instruction)
                if slide_nums:
                    sink.log(f"  slides to change: {', '.join(str(n) for n in slide_nums)}")
                if selective_step:
                    sink.log(
                        f"  selective spans to translate: "
                        f"{count_spans_in_units(target_units, instruction)}"
                    )
            step_map: dict[str, str] = {}
            if (
                output_type == "presentation"
                and len(target_units) > 8
                and not selective_step
            ):
                for slide_idx, slide_units in group_units_by_slide(target_units).items():
                    if is_cancelled():
                        break
                    label = slide_idx + 1 if slide_idx >= 0 else "?"
                    sink.log(f"  slide {label}: {len(slide_units)} fragment(s)")
                    step_map.update(
                        _run_mutation_step(
                            step=step,
                            units=slide_units,
                            instruction=instruction,
                            model=model,
                            profile_hint=profile_hint,
                            selection_text=selection_text,
                            all_units=mutation_units,
                        )
                    )
            else:
                step_map = _run_mutation_step(
                    step=step,
                    units=target_units,
                    instruction=instruction,
                    model=model,
                    profile_hint=profile_hint,
                    selection_text=selection_text,
                    all_units=mutation_units,
                )
            for uid, val in step_map.items():
                merged_map[uid] = val
            mutation_units = _apply_mutation_map_to_units(mutation_units, merged_map)

        from services.office_mutation.mutate import mutate_office_file
        from services.office_mutation.paths import resolve_upload_path
        from services.source_parser import primary_mutation_source

        office_source = primary_mutation_source(getattr(bundle, "parsed_sources", None) or [])
        original = (office_source.name if office_source else None) or bundle.original_filename or ""
        src_path = (office_source.path if office_source else None) or resolve_upload_path(original)
        from services.office_mutation.paths import source_format

        deliverable_ot = output_type
        if deliverable_ot == "chat":
            deliverable_ot = source_format(src_path) or "document"

        instruction = (steps[-1].intent if steps else request.user_input) or ""
        is_translate = bool(re.search(r"\btranslat", instruction, re.I))
        preview = ""
        if is_translate:
            preview = "\n\n".join(
                (merged_map.get(u["id"]) or u.get("text") or u.get("value") or "").strip()
                for u in original_mutation_units
                if (merged_map.get(u["id"]) or u.get("text") or u.get("value") or "").strip()
            )
        if preview:
            sink.set_assistant_content(preview)
            sink.refresh_chat()
        else:
            sink.set_assistant_content(
                "⏳ **Processing your file** — applying changes. See Preview for live progress."
            )
        from services.session import draft as draft_sync

        draft_sync.set_draft(preview or "⏳ Processing…", deliverable_ot, "mutation")
        state.mutation_in_progress = True
        sink.sync_preview()

        def on_unit(slide: int, total: int, dest_path: str) -> None:
            sink.set_mutation_progress(slide, total)

        path = mutate_office_file(
            deliverable_ot,
            original,
            model,
            instruction=instruction,
            profile_hint=profile_hint,
            text_map=merged_map,
            replan=False,
            on_unit_applied=on_unit,
            capability_id=CAPABILITY_ID,
            mutation_role_ids=mutation_role_ids(steps[-1].roles if steps else []),
            src_path=src_path,
            units=original_mutation_units,
        )
        state.mutation_in_progress = False
        if path and os.path.exists(path):
            state.last_generated_file_path = path
            state.artifact_ready = True
            from services.session.artifact import sync_preview_from_artifact

            sync_preview_from_artifact(path, deliverable_ot, "mutation")
            name = os.path.basename(path)
            footer = f"✅ **Done** — `{name}` is ready."
            sink.set_assistant_content(
                f"{preview}\n\n---\n\n{footer}" if preview else footer
            )
            sink.notify_artifact_ready(path)
        sink.set_progress("Synthesis", "🟢")
        sink.refresh_chat()
        return {"content": "", "path": path or "", "output_type": output_type}

    if output_type == "image" and mode == "mutation":
        return _run_image_mutation(
            user_query=request.user_input,
            bundle=bundle,
            sink=sink,
            state=state,
        )

    if output_type == "image" and mode == "generation":
        from services.image_composite import is_multi_source_create

        image_paths = _image_paths_from_bundle(bundle)
        if len(image_paths) >= 2 and is_multi_source_create(request.user_input):
            return _run_image_composite(
                user_query=request.user_input,
                bundle=bundle,
                sink=sink,
                state=state,
            )
        from pipeline.direct.image_intent import classify_image_request

        image_intent = classify_image_request(request.user_input)
        if image_intent == "poster":
            from services.poster_generation import resolve_explicit_poster_template

            explicit_template = resolve_explicit_poster_template(request.user_input)
            if explicit_template is None:
                return _run_poster_style_picker(
                    user_query=request.user_input, prof=prof, config=config, sink=sink, state=state,
                    bundle=bundle,
                )
            return _run_poster_generation(
                user_query=request.user_input, prof=prof, config=config, sink=sink, state=state,
                bundle=bundle, template=explicit_template,
            )
        if image_intent == "diagram":
            return _run_diagram_generation(
                user_query=request.user_input, prof=prof, config=config, sink=sink, state=state, bundle=bundle,
            )
        if image_intent in ("infographic_stat", "infographic_timeline", "infographic_comparison"):
            return _run_infographic_generation(
                user_query=request.user_input, kind=image_intent, prof=prof, config=config, sink=sink,
                state=state, bundle=bundle,
            )
        if image_intent == "chart":
            return _run_chart_generation(
                user_query=request.user_input, prof=prof, config=config, sink=sink, state=state, bundle=bundle,
            )

        role_id = steps[-1].roles[0] if steps and steps[-1].roles else "image_prompt_author"
        step = steps[-1]
        body = _run_generation_step(
            step=step,
            plan=plan,
            cfg=pipeline_cfg,
            working_text=working_text,
            bundle=bundle,
            stream=True,
        )
        # Diagnostic for a reported bug where this step's output sometimes describes an
        # unrelated subject — persists to the log file (Settings > About > Open log
        # file) so a failing run can be inspected after the fact without needing to
        # catch it live.
        sink.log(
            f"[diag] image prompt author — user_query={request.user_input!r} "
            f"intent={step.intent!r} model={pipeline_cfg.model!r} raw_output={body!r}"
        )
        # Self-correcting second pass: the user's own wording classified as plain
        # "photo" (that's the only way this code is reached), but the author LLM
        # was free to describe whatever visual actually fits the request — and
        # often naturally writes "a bar chart showing…"/"a flowchart of…" even
        # when the user's original wording didn't say so explicitly. Re-running
        # that authored text through the SAME classifier catches those cases
        # instead of committing to a plain diffusion image (which cannot draw
        # real chart/diagram/infographic content — only a hallucinated
        # approximation with illegible fake text) whenever the user's own
        # phrasing didn't happen to match a known trigger word up front.
        second_pass_intent = classify_image_request(body)
        if second_pass_intent != "photo" and second_pass_intent != "poster":
            sink.log(
                f"[diag] image prompt author's own text reclassified as "
                f"{second_pass_intent!r} (was 'photo' from the original wording) — "
                "routing to the matching renderer instead of plain diffusion."
            )
            if second_pass_intent == "diagram":
                return _run_diagram_generation(
                    user_query=request.user_input, prof=prof, config=config, sink=sink,
                    state=state, bundle=bundle,
                )
            if second_pass_intent in ("infographic_stat", "infographic_timeline", "infographic_comparison"):
                return _run_infographic_generation(
                    user_query=request.user_input, kind=second_pass_intent, prof=prof, config=config,
                    sink=sink, state=state, bundle=bundle,
                )
            if second_pass_intent == "chart":
                return _run_chart_generation(
                    user_query=request.user_input, prof=prof, config=config, sink=sink, state=state,
                    bundle=bundle,
                )
        return _run_image_generation(
            body=body,
            user_query=request.user_input,
            bundle=bundle,
            prof=prof,
            config=config,
            sink=sink,
            state=state,
        )

    planned_deck_spec = None
    for idx, step in enumerate(steps):
        if is_cancelled():
            break
        sink.log(f"Direct step {idx + 1}/{len(steps)}: {', '.join(step.roles)}")
        is_final = idx == len(steps) - 1
        step_plan = plan
        if len(steps) > 1 and not is_final and output_type == "chat":
            from pipeline.direct.query_planner import DirectPlan as _DP

            step_plan = _DP(
                express=False,
                step_count=plan.step_count,
                mode="generation",
                output_type="chat",
                steps=plan.steps,
            )
        role_id = step.roles[0] if step.roles else ""
        combined_prep = _combined_prep_step(role_id, steps, idx)
        skip_llm = False
        step_out = ""
        new_pipeline_spec = None
        if output_type == "presentation" and role_id == "deck_planner":
            # Structured pipeline (extract → group → per-slide author+verify →
            # translate image prompts) replaces the old single freeform
            # "invent the whole deck" call — see pipeline/deliverables/
            # deck_pipeline.py's module docstring for why. Falls back to the
            # legacy path below only on total failure (e.g. no LLM at all).
            from pipeline.deliverables.deck_pipeline import build_deck_via_pipeline
            from pipeline.deliverables.specs import infer_slide_count
            from services.session import state as loma_state

            target = infer_slide_count(request.user_input) or 8
            new_pipeline_spec, spec_grounded = build_deck_via_pipeline(
                query=request.user_input,
                bundle=bundle,
                settings=loma_state.current_settings,
                prof=prof,
                model=model,
                target_slides=target,
                log_fn=sink.log,
            )
            if new_pipeline_spec is not None and new_pipeline_spec.slides:
                pipeline_cfg.presentation_grounded = spec_grounded
                step_out = new_pipeline_spec.to_markdown()
                skip_llm = True
            else:
                new_pipeline_spec = None
        if (
            output_type == "presentation"
            and role_id == "slide_author"
            and (working_text or "").strip()
        ):
            from pipeline.deliverables.presentation_finalize import is_compile_ready_presentation

            if is_compile_ready_presentation(working_text):
                step_out = working_text
                skip_llm = True
        if not skip_llm:
            use_stream = not (
                output_type == "presentation" and role_id == "deck_planner"
            )
            step_out = _run_generation_step(
                step=step,
                plan=step_plan,
                cfg=pipeline_cfg,
                working_text=(
                    ""
                    if _per_source_isolated_step(
                        step, bundle, output_type=output_type, steps=steps
                    )
                    or combined_prep
                    else working_text
                ),
                bundle=bundle,
                stream=use_stream,
                stream_base=(
                    working_text
                    if _per_source_isolated_step(
                        step, bundle, output_type=output_type, steps=steps
                    )
                    else ""
                ),
                step_index=idx,
            )
        role_id = step.roles[0] if step.roles else ""
        if (
            len(steps) > 1
            and output_type == "chat"
            and (step_out or "").strip()
            and (
                role_id in ("summarizer", "extractor")
                or _per_source_isolated_step(step, bundle, output_type=output_type, steps=steps)
            )
            and not _combined_prep_step(role_id, steps, idx)
        ):
            heading = _source_step_heading(step, bundle)
            block = f"## {heading}\n\n{step_out.strip()}" if heading else step_out.strip()
            working_text = (
                f"{working_text.rstrip()}\n\n{block}" if (working_text or "").strip() else block
            )
            sink.set_assistant_content(working_text)
            sink.refresh_chat()
        elif (
            _combined_prep_step(role_id, steps, idx)
            and (step_out or "").strip()
        ):
            heading = _source_step_heading(step, bundle)
            block = f"## {heading}\n\n{step_out.strip()}" if heading else step_out.strip()
            working_text = (
                f"{working_text.rstrip()}\n\n{block}" if (working_text or "").strip() else block
            )
        elif (
            role_id in _FINAL_COMBINED_ROLES
            and (step_out or "").strip()
            and len(steps) > 1
            and _combined_merge_step(role_id, steps, idx)
        ):
            if _combined_synthesis_plan(steps):
                working_text = step_out.strip()
            elif (working_text or "").strip():
                working_text = f"{working_text.rstrip()}\n\n---\n\n{step_out.strip()}"
            else:
                working_text = step_out.strip()
            if output_type == "chat":
                sink.set_assistant_content(working_text)
                sink.refresh_chat()
        else:
            working_text = step_out
        if output_type == "presentation" and role_id == "deck_planner" and (step_out or "").strip():
            from pipeline.deliverables.presentation_theme import resolve_theme, theme_meta_block

            if new_pipeline_spec is not None:
                spec = new_pipeline_spec
            else:
                # Legacy fallback — only reached when build_deck_via_pipeline
                # itself failed outright (see its own except-and-log).
                from pipeline.deliverables.presentation_deck import (
                    parse_deck_spec,
                    repair_deck_spec,
                    validate_deck_spec,
                )
                from pipeline.deliverables.specs import infer_slide_count

                spec, _ = parse_deck_spec(step_out)
                if spec and spec.slides:
                    target = infer_slide_count(request.user_input) or 8
                    spec = repair_deck_spec(spec, query=request.user_input, target_slides=target)
                    v = validate_deck_spec(spec, slide_count=target)
                    if not (v.valid or len(spec.slides) >= 3):
                        spec = None
            if spec and spec.slides:
                planned_deck_spec = spec
                style_id = str(config.get("presentation_style") or "")
                if style_id:
                    from pipeline.deliverables.presentation_theme import resolve_style_palette

                    forced_palette = resolve_style_palette(style_id)
                    if forced_palette:
                        spec.design = {**(spec.design or {}), "palette": forced_palette}
                theme = resolve_theme(query=request.user_input, design=spec.design)
                working_text = theme_meta_block(theme) + "\n\n" + spec.to_markdown()
                step_out = working_text
                sink.set_assistant_content(
                    f"📋 **Deck planned** — {len(spec.slides)} slides. Compiling presentation…"
                )
                sink.refresh_chat()
        if (
            output_type == "presentation"
            and role_id == "slide_author"
            and (step_out or "").strip()
        ):
            from services.presentation_markdown import extract_compile_markdown

            working_text = extract_compile_markdown(step_out)
        if is_final and state.messages and output_type not in ("presentation",):
            from pipeline.instruction_priority import strip_priority_preamble_echo

            working_text = strip_priority_preamble_echo(working_text)
            state.messages[-1]["content"] = working_text
            if is_final and output_type == "chat":
                sink.set_assistant_content(working_text)
                sink.refresh_chat()

    chart_artifacts = list(getattr(bundle, "chart_artifacts", None) or [])
    if chart_artifacts and working_text:
        from pipeline.direct.chart_appendix import apply_chart_appendix

        working_text = apply_chart_appendix(
            content=working_text,
            charts=chart_artifacts,
            output_type=output_type,
            state=state,
            sink=sink if output_type == "chat" else None,
        )

    path = None
    if output_type != "chat" and working_text:
        path = _compile_deliverable(
            body=working_text,
            output_type=output_type,
            mode=mode,
            bundle=bundle,
            model=model,
            prof=prof,
            sink=sink,
            state=state,
            chart_artifacts=chart_artifacts,
            planned_deck_spec=planned_deck_spec,
            presentation_style=pipeline_cfg.presentation_style,
            presentation_grounded=pipeline_cfg.presentation_grounded,
        )
        if path and not os.path.exists(path):
            from pipeline.i18n import t as tr

            sink.set_assistant_content(tr("chat.artifact_failed", label=deliverable_display_name(output_type)))
            sink.refresh_chat()
            path = None
        elif path:
            label = deliverable_display_name(output_type)
            name = os.path.basename(path)
            expected_ext = EXTENSION_BY_TYPE.get(output_type, "")
            if expected_ext and not name.lower().endswith(expected_ext.lower()):
                from pipeline.i18n import t as tr

                sink.set_assistant_content(
                    tr(
                        "chat.artifact_fallback",
                        ext=expected_ext,
                        pkg=_OFFICE_DEP_HINT.get(output_type, "a required package"),
                        name=name,
                        label=label,
                    )
                )
            elif output_type == "presentation" and (working_text or "").strip():
                from pipeline.deliverables.presentation_finalize import presentation_chat_summary
                from services.session import draft as draft_sync

                summary_src = (draft_sync.get_draft() or "").strip()
                sink.set_assistant_content(
                    presentation_chat_summary(summary_src, artifact_name=name)
                )
            else:
                sink.set_assistant_content(
                    f"✅ **{label} ready** — `{name}`."
                )
            sink.notify_artifact_ready(path)
            sink.sync_preview()

    return {"content": working_text, "path": path, "output_type": output_type}
