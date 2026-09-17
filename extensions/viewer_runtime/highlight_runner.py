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
    *,
    use_kv: bool = False,
    kv_mode: str = "ask",
    kv_scope: str = "all",
    kv_library_id: str | None = None,
) -> None:
    prompt_memory.remember_typed_prompt(instruction)
    state.active_workflow_instruction = (full_query or "").strip()
    threading.Thread(
        target=_run_highlight_workflow,
        args=(full_query, instruction, viewer_id, use_kv, kv_mode, kv_scope, kv_library_id),
        daemon=True,
    ).start()


def _run_highlight_workflow(
    full_query: str,
    instruction: str,
    viewer_id: str,
    use_kv: bool = False,
    kv_mode: str = "ask",
    kv_scope: str = "all",
    kv_library_id: str | None = None,
) -> None:
    from services.session.workflow_control import begin_workflow, end_workflow

    label = _VIEWER_LABELS.get(viewer_id, viewer_id.replace("_", " "))
    sink = NiceGUIStateSink()
    begin_workflow()
    try:
        sink.log(f"{label} highlight | direct answer path")
        _run_lite_highlight(
            full_query, instruction, sink, viewer_id, label,
            use_kv=use_kv, kv_mode=kv_mode, kv_scope=kv_scope, kv_library_id=kv_library_id,
        )
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
    # An empty base here left the role/contract wording (singular "the translation",
    # "the target language") as the strongest instruction in the prompt, so an explicit
    # user request like "give me three translations" was silently collapsed to one —
    # normal chat avoids this because its base system instruction always carries this
    # same priority preamble ahead of the role/contract text. Apply it here too so the
    # user's explicit wording (count, format, etc.) outranks the role's default framing.
    from pipeline.instruction_priority import apply_instruction_priority

    base = apply_instruction_priority("", user_query=task, profile=prof)
    system = build_merged_roles_system_instruction(base, [role], [contract])
    user_msg = (
        f'Excerpt:\n""{excerpt}""\n\n'
        f"Task: {task}\n\n"
        "Respond to the task using the excerpt above. "
        "Output the result only — do not repeat the task instruction."
    )
    # Only add the multi-item framing when the task actually asks for more than one —
    # earlier this appended unconditionally to every task, which primed even unrelated
    # requests (e.g. "what does this mean") toward a bare numbered-list echo instead of
    # an actual answer. Gate it on a quantity signal (digit, number-word, or vague
    # quantifier like "a few"/"some"/"several") plus a plural-item noun. Word lists
    # live in pipeline/query_intent_i18n.py's CONCEPTS — see CLAUDE.md section 8.
    _wants_multiple = (
        re.search(r"\d", lower) or matches(lower, "quantity_words")
    ) and matches(lower, "plural_item_nouns")
    if _wants_multiple:
        user_msg += (
            " If the task asks for a specific number of translations, versions, options, "
            "or alternatives, provide exactly that many, each clearly separated (e.g. numbered). "
            "If it asks for a vague plural quantity instead (e.g. 'a few', 'some', 'several'), "
            "provide at least 3, each clearly separated (e.g. numbered) "
            "— do not collapse multiple requested items into a single blended answer."
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


def _run_kv_answer(
    full_query: str,
    instruction: str,
    sink: Any,
    kv_mode: str,
    kv_scope: str,
    kv_library_id: str | None,
) -> None:
    """Answer a highlighted excerpt using Knowledge Vault's own retrieval +
    synthesis actions instead of a bare LLM call — grounds the answer in the user's
    indexed catalog/library when they've opted into it from the Ask LOMA popup."""
    from extensions.knowledge_vault.agent.loop import run_agent
    from extensions.knowledge_vault.corpus.backend_resolver import resolve_kv_backend
    from extensions.knowledge_vault.retrieval.analyze_action import run_analyze
    from extensions.knowledge_vault.retrieval.ask_action import run_ask
    from extensions.knowledge_vault.retrieval.search_action import format_search_chat, run_search
    from pipeline.i18n import t as tr
    from pipeline.progress_stages import (
        on_intent_complete,
        on_synthesis_active,
        on_workflow_failed,
        on_workflow_succeeded,
    )
    from services.session.workflow_control import is_cancelled

    excerpt = extract_highlight_excerpt(full_query)
    task = (instruction or "").strip()
    if not excerpt or not task:
        sink.set_assistant_content("No excerpt or instruction to process.")
        sink.refresh_chat()
        on_workflow_failed(sink)
        return

    # include_workspace=False — Document Editor's "Ask LOMA" popup no longer offers
    # "workspace" as a selectable scope (session data stays inside Knowledge Vault's
    # own tabs only), so "all" here must not silently fall back to session content.
    backend = resolve_kv_backend(kv_scope, kv_library_id, include_workspace=False)
    # backend.chunks (not backend.lexical.chunks) — a "all"-scope MultiCorpusBackend
    # exposes .chunks directly but its .lexical (a fan-out-and-merge wrapper, not a
    # real LexicalIndex) has no .chunks attribute of its own.
    if backend is None or not getattr(backend, "lexical", None) or not getattr(backend, "chunks", None):
        sink.set_assistant_content(tr("viewer.highlight_kv_not_ready"))
        sink.refresh_chat()
        on_workflow_failed(sink)
        return

    # When the dialog fell back to the highlighted text itself (no separate instruction
    # typed), task and excerpt are the same string — don't duplicate it in the query.
    has_real_task = task.strip() != excerpt.strip()
    query = excerpt if not has_real_task else f"{task}\n\n{excerpt}"

    from pipeline.instruction_priority import resolve_response_locale

    kv_locale = resolve_response_locale(task if has_real_task else excerpt)

    # Ground retrieval in the highlighted excerpt's own source document when one can
    # be identified with high confidence — otherwise Deep/Analyse mode's query
    # expansion can end up searching only fragments of the typed instruction and
    # never reach the excerpt's own tokens at all.
    kv_branch = ""
    if has_real_task:
        excerpt_hits = backend.lexical.search(excerpt, limit=1)
        if excerpt_hits and excerpt_hits[0].score >= 0.35:
            kv_branch = excerpt_hits[0].chunk.vault_path or ""
    kv_cutoff_override = 0.0 if kv_branch else None

    from extensions.knowledge_vault.translation import (
        extract_target_language,
        format_translation_result,
        is_translation_query,
        target_language_matches,
    )

    if is_translation_query(task):
        # Always the Translation Vault's own store here (not any one catalogue's
        # live index) — so editing/deleting a pair in the Translation Vault tab is
        # immediately reflected in this popup too.
        from extensions.knowledge_vault.translation_vault_store import (
            vault_translation_index,
        )

        trans_idx = vault_translation_index()
        target_lang = extract_target_language(task)
        pairs = [
            p for p in trans_idx.lookup(excerpt) if target_language_matches(p.target_text, target_lang)
        ]
        if pairs:
            on_intent_complete(sink, "lite")
            sink.ensure_assistant_message()
            sink.set_assistant_content(format_translation_result(query, pairs))
            sink.refresh_chat()
            on_workflow_succeeded(sink, "lite")
            return

    on_intent_complete(sink, "lite")
    sink.ensure_assistant_message()
    sink.set_assistant_content("")
    sink.refresh_chat()
    on_synthesis_active(sink)

    if is_cancelled():
        on_workflow_failed(sink)
        return

    def _on_chunk(text: str) -> None:
        sink.set_assistant_content(text)
        sink.refresh_chat()

    from extensions.knowledge_vault.ui.constants import mode_options

    mode = (kv_mode or "ask").strip().lower()
    mode_text = mode_options().get(mode, mode)

    if mode == "search":
        rows = run_search(backend, query, branch=kv_branch)
        final = format_search_chat(query, rows)
    elif mode in ("analyse", "deep"):
        from extensions.knowledge_vault.corpus.types import ReasoningMode

        response = run_analyze(
            backend, query,
            branch=kv_branch,
            mode=ReasoningMode.DEEP,
            deep_extras=(mode == "deep"),
            on_chunk=_on_chunk,
            locale=kv_locale,
            cutoff_override=kv_cutoff_override,
        )
        final = tr("knowledge_vault.result_header", mode=mode_text, response=response)
    elif mode == "agentic":
        final = run_agent(
            backend, query, branch=kv_branch, should_stop=is_cancelled, on_chunk=_on_chunk,
            locale=kv_locale, cutoff_override=kv_cutoff_override,
        )
    else:
        response = run_ask(
            backend, query, branch=kv_branch, on_chunk=_on_chunk,
            locale=kv_locale, cutoff_override=kv_cutoff_override,
        )
        final = tr("knowledge_vault.result_header", mode=mode_text, response=response)

    sink.set_assistant_content(final)
    from services.session import state as _loma_state

    if _loma_state.messages:
        _loma_state.messages[-1]["kv_mode"] = mode
    sink.refresh_chat()

    if not is_cancelled():
        on_workflow_succeeded(sink, "lite")


def _run_lite_highlight(
    full_query: str,
    instruction: str,
    sink: Any,
    viewer_id: str,
    label: str,
    *,
    use_kv: bool = False,
    kv_mode: str = "ask",
    kv_scope: str = "all",
    kv_library_id: str | None = None,
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
        if use_kv:
            _run_kv_answer(full_query, instruction, sink, kv_mode, kv_scope, kv_library_id)
        else:
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
