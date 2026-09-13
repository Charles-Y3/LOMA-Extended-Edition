# -*- coding: utf-8 -*-
"""Viewer highlight Q&A — direct pipeline path."""
from __future__ import annotations

import re
import threading
from typing import Any

from pipeline.context_builder import build_context_bundle, build_request
from pipeline.input_metadata import build_input_metadata
from pipeline.input_router import InputRouter
from pipeline.workflow import build_pipeline_config, build_pipeline_inputs
from services.session import prompt_memory
from services.session import state
from ui.themes.sink import NiceGUIStateSink

_VIEWER_LABELS = {
    "document_editor": "Document editor",
    "web_viewer": "Web viewer",
    "email_assistant": "Email assistant",
}

from pipeline.direct.highlight_excerpt import extract_highlight_excerpt, is_highlight_query


def start_viewer_highlight_workflow(
    full_query: str,
    instruction: str,
    viewer_id: str = "document_editor",
) -> None:
    prompt_memory.remember_typed_prompt(instruction)
    state.active_workflow_instruction = (full_query or "").strip()
    threading.Thread(
        target=_run_highlight_workflow,
        args=(full_query, instruction, viewer_id),
        daemon=True,
    ).start()


def _run_highlight_workflow(full_query: str, instruction: str, viewer_id: str) -> None:
    from services.session.workflow_control import begin_workflow, end_workflow

    label = _VIEWER_LABELS.get(viewer_id, viewer_id.replace("_", " "))
    sink = NiceGUIStateSink()
    begin_workflow()
    try:
        sink.log(f"{label} highlight | direct answer path")
        _run_lite_highlight(full_query, instruction, sink, viewer_id, label)
    except Exception as exc:
        sink.log(f"{label} highlight workflow error: {exc}")
        from pipeline.progress_stages import on_workflow_failed

        on_workflow_failed(sink)
    finally:
        state.active_workflow_instruction = ""
        end_workflow()


def _run_highlight_answer(
    full_query: str,
    instruction: str,
    sink: Any,
    prof: dict,
    gen_model: str,
    label: str,
) -> None:
    """Direct Q&A on highlighted excerpt — bypass planner so the model answers, not echoes."""
    from pipeline.capability_runtime.chat_runner import (
        build_merged_roles_system_instruction,
        stream_chat_response,
    )
    from pipeline.contracts.chat_contracts import get_chat_contract
    from pipeline.direct.task_roles import get_task_role
    from pipeline.progress_stages import (
        on_intent_complete,
        on_synthesis_active,
        on_workflow_failed,
        on_workflow_succeeded,
    )
    from services.model_router import resolve_general_model
    from services.session.workflow_control import is_cancelled

    excerpt = extract_highlight_excerpt(full_query)
    task = (instruction or "").strip()
    if not excerpt or not task:
        sink.set_assistant_content("No excerpt or instruction to process.")
        sink.refresh_chat()
        on_workflow_failed(sink)
        return

    from pipeline.query_intent_i18n import matches

    lower = task.lower()
    # A message that merely mentions "translation" isn't always a request to translate —
    # "is this translation good?", "suggest a better translation", "other suggestion
    # translation to X" are all asking for judgment/discussion of an existing translation,
    # not a fresh one. The Translator role's system prompt explicitly forbids commentary
    # ("no commentary or English analysis blocks"), so routing those there made the model
    # just re-emit translation-shaped text instead of answering the actual question —
    # visible as near-verbatim echoes of the excerpt. Route anything with evaluative/
    # discussion language to general_answer instead, regardless of whether it also
    # contains "translate"/"translation". Word lists live in
    # pipeline/query_intent_i18n.py's CONCEPTS — see CLAUDE.md section 8.
    wants_translation = matches(lower, "verb_translate") and not matches(lower, "discussion_signals")
    if wants_translation:
        role_id = "translator"
        contract_id = "chat_translation"
    elif matches(lower, "verb_summarize"):
        role_id = "summarizer"
        contract_id = "chat_summary"
    elif matches(lower, "verb_extract"):
        role_id = "extractor"
        contract_id = "chat_extract"
    elif re.search(r"rewrite|rephrase|paraphrase", lower) or matches(lower, "verb_rewrite_narrow"):
        role_id = "editor"
        contract_id = "chat_rewrite"
    else:
        role_id = "general_answer"
        contract_id = "chat_default"

    role = get_task_role(role_id)
    contract = get_chat_contract(contract_id)
    model = gen_model or resolve_general_model(prof)
    system = build_merged_roles_system_instruction("", [role], [contract])
    user_msg = (
        f'Excerpt:\n""{excerpt}""\n\n'
        f"Task: {task}\n\n"
        "Respond to the task using the excerpt above. "
        "Output the result only — do not repeat the task instruction."
    )

    sink.log(f"{label} highlight → direct {role_id}")
    on_intent_complete(sink, "lite")
    sink.ensure_assistant_message()
    sink.set_assistant_content("")
    sink.refresh_chat()
    on_synthesis_active(sink)

    if is_cancelled():
        on_workflow_failed(sink)
        return

    # A highlighted excerpt can be a short DOM selection or an entire scraped web page —
    # unlike express/grounded/warm-up's short prompts, this can't rely on the fixed
    # Settings num_ctx. Size it to the actual prompt the same way batch_processor/
    # translator already do for long documents, capped at this machine's hardware ceiling.
    from pipeline.direct.batch_budget import fit_budget_to_prompt, llm_extra_options, resolve_batch_budget

    budget = resolve_batch_budget(prof, model)
    budget = fit_budget_to_prompt(budget, len(system) + len(user_msg), profile=prof)
    extra_options = llm_extra_options(budget)

    try:
        stream_chat_response(
            profile=prof,
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ],
            sink=sink,
            is_cancelled=is_cancelled,
            disable_thinking=True,
            extra_options=extra_options,
        )
    except Exception as exc:
        # Without this, a mid-stream failure (context overflow on a large scraped
        # page, model timeout, connection reset) leaves the bubble permanently
        # blank — set_assistant_content("") above already cleared it and nothing
        # downstream ever repopulates it. Surface the real error instead.
        sink.log(f"{label} highlight answer failed: {exc}")
        sink.set_assistant_content(f"⚠️ Could not generate a response: {exc}")
        sink.refresh_chat()
        on_workflow_failed(sink)
        return
    if not is_cancelled():
        on_workflow_succeeded(sink, "lite")


def _run_lite_highlight(
    full_query: str,
    instruction: str,
    sink: Any,
    viewer_id: str,
    label: str,
) -> None:
    from pipeline.base import profile_pack as profile_manager
    from pipeline.base.profile_pack import default_profile
    from pipeline.direct.entry import run_direct
    from pipeline.progress_stages import (
        on_intent_complete,
        on_synthesis_active,
        on_workflow_begin,
        on_workflow_failed,
        on_workflow_succeeded,
    )
    from services.model_router import resolve_general_model
    from services.session.workflow_control import is_cancelled

    on_workflow_begin(sink)

    profile_id = profile_manager.resolve_active_profile_id(
        (state.current_settings or {}).get("active_profile")
    )
    if profile_manager.is_no_profile(profile_id):
        prof = default_profile("none")
        sink.log("No profile selected — using base defaults")
    else:
        prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
    gen_model = prof.get("MODEL", {}).get("preferred_llm") or resolve_general_model(prof)

    if is_highlight_query(full_query):
        _run_highlight_answer(full_query, instruction, sink, prof, gen_model, label)
        return

    meta = build_input_metadata(state)
    router = InputRouter()
    decision = router.route(meta)

    request = build_request(
        user_input=full_query,
        profile_id=meta.profile_id,
        messages=state.messages,
        context_files=state.active_context_files,
        web_links=state.active_web_links,
        settings=state.current_settings,
    )

    bundle = build_context_bundle(request, prof, [])

    sink.log(f"{label} highlight → direct | output={decision.output_type.upper()}")
    on_intent_complete(sink, "lite")

    if is_cancelled():
        sink.log("Workflow stopped before execution.")
        return

    inputs = build_pipeline_inputs(
        state,
        sink,
        request,
        bundle,
        prof,
        profile_id,
        decision,
        gen_model,
        full_query,
    )
    config = build_pipeline_config(prof, profile_id, decision, full_query)
    config["execution_mode"] = "lite"
    config["chat_execution_mode"] = "lite"

    on_synthesis_active(sink)
    try:
        run_direct(inputs, config)
        if not is_cancelled():
            on_workflow_succeeded(sink, "lite")
    except Exception:
        on_workflow_failed(sink)
        raise
