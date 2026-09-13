# -*- coding: utf-8 -*-
"""LOMA main workflow pipeline — service-first routing, direct and agentic execution."""
from __future__ import annotations

import os
import re
import time

_BARE_URL_RE = re.compile(r"https?://\S+")


def _scope_context_files_to_input(
    user_input: str, context_files: list[dict], current_turn: int
) -> list[dict]:
    """Narrow context_files to what this turn is actually about. active_context_files
    is the live basket for the CURRENT turn (capped at 5) — workflow_control.end_workflow()
    clears it after every completed turn so an old upload doesn't silently ride along as
    "context" for a later, unrelated request. (The full session's upload/link history,
    independent of this per-turn basket, lives in state.session_sources_log instead —
    used by chat_summarize.py, never by request-building.) Within a single turn, this
    function still narrows further: a fresh multi-file upload can attach up to 5 files
    at once, and not all of them are necessarily what this message is about.

    Two independent, additive signals decide what's in scope — neither depends on
    guessing intent from wording, which is what made two earlier attempts at this
    both wrong in opposite directions (matching only a literally-named file dumped
    the whole basket on any vaguer phrasing like "output as .docx"; defaulting the
    fallback to "just the most recent file" then broke "summarize all three" for a
    fresh multi-file upload — neither failure is about *how many* files are meant,
    it's about *which* files are actually part of this message):

    1. Explicitly named file(s) in this message — always included.
    2. Every file whose _attached_turn stamp equals current_turn — i.e. every file
       attached since the previous message was sent, whether that's one file or
       all five. This is what makes "attach 3 files, then say 'summarize'" include
       all 3: they're not being distinguished by name at all, they're distinguished
       by having just been added in this same compose action.

    Only once neither signal produced anything (a follow-up naming nothing new and
    attaching nothing new — "output as .docx" right after "translate this pdf") do
    we fall back to the single most-recently-attached file, rather than the whole
    session-long basket.
    """
    if not context_files:
        return context_files
    text = (user_input or "").lower()
    named = []
    fresh = []
    for f in context_files:
        name = (f.get("filename") or "").strip()
        stem = os.path.splitext(name)[0].strip().lower() if name else ""
        if text and name and (name.lower() in text or (stem and stem in text)):
            named.append(f)
        elif f.get("_attached_turn") == current_turn:
            fresh.append(f)
    in_scope = named + [f for f in fresh if f not in named]
    if in_scope:
        return in_scope
    return context_files[-1:]

from pipeline.context_builder import build_context_bundle, build_request
from pipeline.schemas.task_schema import ContextBundle, RoutingDecision
from services.session import draft as draft_sync
from pipeline.state_machine import UISink
from ui.themes.sink import NiceGUIStateSink
from services.model_router import resolve_general_model
from pipeline.base.profile_pack import default_profile
from services.session.artifact import save_preview_to_artifact
from pipeline.base import profile_pack as profile_manager
from services.web_fetch import scrape_website_text
from pipeline.input_metadata import build_input_metadata
from pipeline.input_router import InputRouter


def handle_workflow_exception(sink: UISink, exc: Exception) -> None:
    """Translate a workflow-ending exception into a friendly, localized chat
    message and reset progress/capability state.

    Shared by run_workflow()'s own try/except below AND
    ui/components/style_picker.py::run_generation_on_client() — the
    style-picker resume path calls generation directly rather than through
    run_workflow(), and previously bypassed this handling entirely, meaning
    any exception during a picker-driven poster/presentation generation
    (context-length exceeded, model not found, ...) failed completely
    silently: no chat message, just a stuck empty assistant bubble, with the
    real error visible only in the terminal."""
    from services.session import state

    err = str(exc)
    sink.log(f"Workflow Error: {err}")
    from config import get_installed_models
    from pipeline.i18n import t as tr
    from pipeline.progress_stages import on_workflow_failed

    low = err.lower()
    if "exceed_context_size_error" in low or "exceeds the available context" in low:
        has_source = bool(getattr(state, "active_context_files", None))
        msg = tr("chat.context_exceeded" if has_source else "chat.context_exceeded_no_source")
    elif "context_resize_stalled" in low or "timed out" in low or "timeout" in low:
        # ollama_provider.OllamaProvider._chat_with_resize_guard already tried
        # to recover by retrying at the currently-loaded context size before
        # giving up and raising — this branch is what the user sees if even
        # that fallback failed, so it must not look like a silent hang or a
        # generic error: it names the actual cause and the permanent fix.
        msg = tr("chat.context_resize_failed")
    elif "not found" in low or "404" in low:
        msg = tr("chat.model_not_found", error=err)
    elif not get_installed_models():
        msg = tr("chat.no_models_installed")
    else:
        msg = tr("chat.workflow_error", error=err)
    sink.set_assistant_content(msg)
    sink.refresh_chat()
    try:
        on_workflow_failed(sink)
    except Exception:
        sink.set_progress("Intent", "🔴")
        sink.set_progress("Planner", "🔴")
        sink.set_progress("Execution", "🔴")
        sink.set_progress("Synthesis", "🔴")
    for cap in state.LOMA_CAPABILITIES:
        if cap["status"] == "processing":
            cap["status"] = "idle"
    sink.refresh_capabilities()


def run_workflow(user_input: str, sink: UISink | None = None) -> None:
    sink = sink or NiceGUIStateSink()
    from services.session import state
    from services.session.workflow_control import (
        WorkflowCancelled,
        begin_workflow,
        end_workflow,
        is_cancelled,
    )

    begin_workflow()
    _wf_t0 = time.perf_counter()
    try:
        from pipeline.capability_runtime.execution_config import execution_mode_from_settings
        from pipeline.progress_stages import (
            on_intent_complete,
            on_synthesis_active,
            on_workflow_begin,
            on_workflow_succeeded,
        )

        on_workflow_begin(sink)

        _t_meta = time.perf_counter()
        meta = build_input_metadata(state)
        sink.log(f"Input metadata built in {round((time.perf_counter() - _t_meta) * 1000)}ms")
        profile_id = meta.profile_id
        file_count = meta.file_count
        link_count = meta.link_count

        workflow_instruction = (user_input or "").strip()
        state.active_workflow_instruction = workflow_instruction
        # last_user_instruction is set at UI entry (workspace / highlight prompt), not here —
        # workflow user_input may include excerpt wrappers sent to the model.
        state.mutation_map = None
        state.mutation_span_map = None
        state.artifact_ready = False
        state.preview_dirty = False
        state.last_image_diffusion_prompt = ""
        state.last_image_user_query = ""
        state.last_image_generation_meta = {}

        draft_sync.sync_editor_to_state()

        _t_route = time.perf_counter()
        router = InputRouter()
        decision = router.route(meta)
        sink.log(
            f"Routing decided in {round((time.perf_counter() - _t_route) * 1000)}ms "
            f"(model={router.model or '(unresolved)'})"
        )

        # Materialize full request after routing (heavy I/O).
        _t_req = time.perf_counter()
        scoped_context_files = _scope_context_files_to_input(
            user_input, state.active_context_files, state.context_attach_turn
        )
        if len(scoped_context_files) < len(state.active_context_files):
            dropped = len(state.active_context_files) - len(scoped_context_files)
            scoped_names = ", ".join(f.get("filename", "?") for f in scoped_context_files)
            sink.log(
                f"Context scoped to [{scoped_names}] "
                f"({dropped} other active source(s) not named or attached this turn excluded)"
            )
        request = build_request(
            user_input=user_input,
            profile_id=profile_id,
            messages=state.messages,
            context_files=scoped_context_files,
            web_links=state.active_web_links,
            settings=state.current_settings,
        )
        sink.log(f"Request materialized in {round((time.perf_counter() - _t_req) * 1000)}ms")

        _t_prof_resolve = time.perf_counter()
        resolved_profile_id = profile_manager.resolve_active_profile_id(profile_id)
        sink.log(
            f"Active profile id resolved in "
            f"{round((time.perf_counter() - _t_prof_resolve) * 1000)}ms"
        )
        if resolved_profile_id != profile_id:
            state.current_settings["active_profile"] = resolved_profile_id
            profile_id = resolved_profile_id

        if profile_manager.is_no_profile(profile_id):
            prof = default_profile("none")
            sink.log(
                f"No profile selected — using base defaults "
                f"(workflow prep {round((time.perf_counter() - _wf_t0) * 1000)}ms)"
            )
        else:
            prof = profile_manager.load_profile(profile_id)
            if prof:
                sink.log(f"Loaded profile '{profile_id}'")
            else:
                sink.log(f"Profile '{profile_id}' unavailable — using defaults")
                prof = default_profile(profile_id)

        web_blocks: list[tuple[str, str]] = []
        if meta.link_count and "web_fetch" in decision.required_services:
            web_blocks = _fetch_web_context(request.web_links, sink)
        _t_bundle = time.perf_counter()
        bundle = build_context_bundle(request, prof, web_blocks)
        sink.log(f"Context bundle built in {round((time.perf_counter() - _t_bundle) * 1000)}ms")

        # A URL typed directly into the chat message is never fetched in this edition
        # (only files added as Sources are read) — chat_roles.py's general_answer
        # system prompt already tells the model not to fabricate a page summary, but
        # that instruction is buried in a long merged system prompt and small local
        # models frequently ignore it. Injecting the same note into the context bundle
        # puts it right next to the user's actual query instead, where compliance is
        # far more reliable.
        fetched_urls = {url for url, _ in web_blocks}
        unfetched_urls = [
            u for u in _BARE_URL_RE.findall(request.user_input or "") if u not in fetched_urls
        ]
        if unfetched_urls:
            note = (
                "[SYSTEM NOTE: The message above contains a URL, but this edition does not "
                "fetch or read links typed into chat — no page content was retrieved. You have "
                "NOT seen that page. If asked what it's about, say plainly that you cannot "
                "access it; you may offer a guess based only on the URL text itself (domain, "
                "slug, date), but must clearly label it as a guess, never as a summary of the "
                "actual page.]"
            )
            bundle.unified_text = f"{note}\n\n{bundle.unified_text}".strip() if bundle.unified_text else note
        for ps in getattr(bundle, "parsed_sources", None) or []:
            kind = getattr(ps, "kind", "?")
            name = getattr(ps, "name", "?")
            sink.log(f"  · source {name} ({kind})")
        if getattr(bundle, "images", None):
            sink.log(f"  · vision: {len(bundle.images)} image(s) attached for model")

        from pipeline.routing_helpers import should_enrich_with_graph_analysis

        if should_enrich_with_graph_analysis(meta, deliverable=decision.output_type):
            from services.graph_generation import enrich_context_with_graph_analysis

            graph_result = enrich_context_with_graph_analysis(
                bundle,
                request.user_input,
                log_fn=sink.log,
                layout_mode="chat" if decision.output_type == "chat" else "report",
            )
            if graph_result and graph_result.charts:
                sink.log(f"Graph service: {len(graph_result.charts)} chart(s) generated")
            state.chart_artifacts = list(getattr(bundle, "chart_artifacts", None) or [])
            profiles = list(getattr(graph_result, "profiles", None) or [])
            state.dataset_overview_md = "\n\n".join(p.overview_md for p in profiles if p.overview_md)
            state.dataset_computed_stats = [p.computed_stats for p in profiles if p.computed_stats]
        else:
            state.chart_artifacts = []
            state.dataset_overview_md = ""
            state.dataset_computed_stats = []

        sink.log(f"Query with {file_count} file(s), {link_count} web link(s)")
        if bundle.context_was_retrieved:
            sink.log(
                f"Context retrieval ({bundle.context_selection_mode or 'auto'}): "
                f"selected passages from large sources"
            )
        elif getattr(bundle, "context_strategy", "fit") not in ("fit", ""):
            sink.log(
                f"Context strategy: {bundle.context_strategy} | "
                f"{getattr(bundle, 'total_source_chars', 0):,} source chars | "
                f"budget ~{getattr(bundle, 'context_char_budget', 0):,}"
            )
        elif meta.needs_context_retrieval and meta.total_source_chars:
            sink.log(
                f"Sources: {meta.total_source_chars:,} characters "
                f"(within model context budget)"
            )

        state.pending_output_type = decision.output_type

        from services.capability.gap_handler import image_gen_gap, offer_image_gen_installer

        if decision.output_type == "image" and image_gen_gap(decision.output_type):

            def _resume_image_workflow() -> None:
                import threading

                threading.Thread(
                    target=start_loma_workflow,
                    args=(workflow_instruction,),
                    daemon=True,
                ).start()

            offer_image_gen_installer(_resume_image_workflow)
            sink.ensure_assistant_message()
            from pipeline.i18n import t as tr

            sink.set_assistant_content(tr("gap.image_gen_retry"))
            sink.refresh_chat()
            return

        execution_mode = execution_mode_from_settings(state.current_settings)
        try:
            from ui.components.ux_guidance import publish_routing_summary

            publish_routing_summary(
                execution_mode=execution_mode,
                output_type=decision.output_type,
                reason=decision.reason or "",
                log_fn=sink.log,
            )
        except Exception:
            sink.log(
                f"Routing → output={decision.output_type.upper()} | "
                f"services={','.join(decision.required_services)}"
            )
        if execution_mode != "direct":
            sink.log(
                f"Routing → output={decision.output_type.upper()} | "
                f"services={','.join(decision.required_services)}"
            )
        on_intent_complete(sink, execution_mode)
        try:
            from services.session.workflow_control import schedule_on_ui

            def _refresh_progress() -> None:
                ui_mod = state.get_ui_module()
                if hasattr(ui_mod, "render_progress") and hasattr(ui_mod.render_progress, "refresh"):
                    ui_mod.render_progress.refresh()

            schedule_on_ui(_refresh_progress)
        except Exception:
            pass

        if is_cancelled():
            sink.log("Workflow stopped before execution.")
            return

        gen_model = resolve_general_model(prof)

        from services.grounded_chat import (
            grounding_skip_reason,
            should_use_grounded_chat,
            web_grounding_enabled,
        )

        if should_use_grounded_chat(
            request.user_input,
            state.current_settings,
            output_type=decision.output_type,
            has_attachments=bool(meta.file_count or meta.link_count),
        ):
            from pipeline.direct.grounded_runner import run_grounded_chat

            sink.log("Grounded chat (web search)")
            inputs = build_pipeline_inputs(
                state,
                sink,
                request,
                bundle,
                prof,
                profile_id,
                decision,
                gen_model,
                workflow_instruction,
            )
            config = build_pipeline_config(prof, profile_id, decision, request.user_input)
            run_grounded_chat(inputs, config)
            if not is_cancelled():
                on_workflow_succeeded(sink, execution_mode_from_settings(state.current_settings))
            return

        if web_grounding_enabled(state.current_settings):
            skip = grounding_skip_reason(request.user_input)
            if skip:
                sink.log(f"Web grounding skipped: {skip}")

        from pipeline.direct.entry import run_direct

        inputs = build_pipeline_inputs(
            state, sink, request, bundle, prof, profile_id, decision, gen_model, workflow_instruction
        )
        config = build_pipeline_config(prof, profile_id, decision, request.user_input)
        on_synthesis_active(sink)
        run_direct(inputs, config)
        if not is_cancelled():
            on_workflow_succeeded(sink, execution_mode)

    except WorkflowCancelled:
        # A blocking LLM call (e.g. plan-mode orchestration) was interrupted mid-flight
        # by run_cancellable() — same user-requested stop as the is_cancelled() early
        # returns above, just caught here instead of checked cooperatively beforehand.
        sink.log("Workflow stopped by user.")
        for cap in state.LOMA_CAPABILITIES:
            if cap["status"] == "processing":
                cap["status"] = "idle"
        sink.refresh_capabilities()

    except Exception as e:
        handle_workflow_exception(sink, e)
    finally:
        from services.session import state as st

        st.pending_output_type = None
        st.active_workflow_instruction = ""
        # Any file attached from here on is "for the next message", not this one —
        # see _scope_context_files_to_input and the _attached_turn stamp it reads.
        st.context_attach_turn += 1
        end_workflow()
        sink.refresh_progress()


def _fetch_web_context(links: list, sink: UISink) -> list[tuple[str, str]]:
    if not links:
        return []

    from services.web_context_cache import get_cached, scrape_text_from_payload, set_cached

    blocks = []
    needs_fetch = []
    for link in links:
        hit = get_cached(link)
        if hit is not None and scrape_text_from_payload(hit):
            blocks.append((link, hit))
        else:
            needs_fetch.append(link)

    if not needs_fetch:
        sink.log("Using cached web context (no re-scrape).")
        return blocks

    sink.set_capability("Web Retrieval", "processing")
    sink.refresh_capabilities()

    for link in needs_fetch:
        if any(l == link for l, _ in blocks):
            continue
        sink.log(f"Fetching: {link}")
        content = scrape_website_text(link)
        if isinstance(content, dict) and content.get("error"):
            sink.log((content.get("content") or content.get("simple_text") or "Fetch blocked.")[:200])
        set_cached(link, content)
        blocks.append((link, content))

    sink.set_capability("Web Retrieval", "success")
    sink.refresh_capabilities()
    return blocks


def _workflow_stale(expected_instruction: str) -> bool:
    from services.session import state as st

    expected = (expected_instruction or "").strip()
    if not expected:
        return False
    mission = (getattr(st, "active_workflow_instruction", "") or "").strip()
    if mission and mission == expected:
        return False
    current = (st.last_user_instruction or "").strip()
    if current == expected:
        return False
    # Short typed prompt is often the tail of an excerpt-wrapped mission.
    if current and expected.endswith(current):
        return False
    return True


def _deliver_output(
    state,
    sink: UISink,
    decision: RoutingDecision,
    content: str,
    original_filename: str,
    gen_model: str,
    *,
    workflow_instruction: str = "",
) -> None:
    """Persist non-chat drafts to Preview/files; chat stays in workspace only."""
    if _workflow_stale(workflow_instruction):
        sink.log("Skipped stale output delivery (newer user message).")
        return

    if decision.output_type == "chat":
        if not content or not str(content).strip():
            return
        body = draft_sync.strip_export_content(content.strip())
        draft_sync.set_draft("", "chat", None)
        state.live_workspace_output_type = "chat"
        state.live_workspace_mode = None
        state.last_generated_file_path = None
        state.artifact_ready = False
        state.preview_dirty = False
        state.last_image_diffusion_prompt = ""
        state.last_image_user_query = ""
        state.last_image_generation_meta = {}
        sink.set_assistant_content(body)
        sink.refresh_chat()
        sink.scroll_chat()
        try:
            from ui.components.preview_workspace import refresh_preview_panel, update_preview_status_label

            update_preview_status_label("chat")
            refresh_preview_panel()
        except Exception:
            pass
        return

    if not content or not str(content).strip():
        return

    body = draft_sync.strip_export_content(content.strip())
    draft_sync.set_draft(body, decision.output_type, decision.mode)

    presentation_ready = False
    if decision.output_type == "presentation":
        from pipeline.deliverables.presentation_finalize import is_compile_ready_presentation

        presentation_ready = is_compile_ready_presentation(body)
        if not presentation_ready:
            alt = getattr(state, "agentic_presentation_source", None) or ""
            if is_compile_ready_presentation(alt):
                body = alt
                presentation_ready = True
                draft_sync.set_draft(body, decision.output_type, decision.mode)

    if (
        decision.output_type == "presentation"
        and getattr(state, "last_generated_file_path", None)
        and os.path.exists(state.last_generated_file_path)
        and state.artifact_ready
    ):
        result = state.last_generated_file_path
        sink.log(f"Using agentic-compiled presentation → {os.path.basename(result)}")
    else:
        sink.log("Compiling artifact from draft…")
        result = save_preview_to_artifact(
            output_type=decision.output_type,
            original_filename=original_filename or "output",
            gen_model=gen_model,
            mode=decision.mode,
            log_fn=sink.log,
            use_editor=False,
        )
    if result and os.path.exists(result):
        name = os.path.basename(result)
        state.last_generated_file_path = result
        state.artifact_ready = True
        state.preview_dirty = False
        current = ""
        if state.messages and state.messages[-1].get("role") == "assistant":
            current = state.messages[-1].get("content") or ""
        if decision.output_type == "presentation" and presentation_ready:
            from pipeline.deliverables.presentation_finalize import presentation_chat_summary

            story = presentation_chat_summary(body, artifact_name=name)
        else:
            story = draft_sync.strip_export_content(current) or body
        sink.set_assistant_content(story)
        sink.log(f"Artifact ready → data/generated/{name}")
        sink.notify_artifact_ready(result)
        # #region agent log
        try:
            import json as _json
            with open("debug-6f9f14.log", "a", encoding="utf-8") as _lf:
                _lf.write(_json.dumps({"sessionId": "6f9f14", "hypothesisId": "GEN", "location": "engine.py:_deliver_output", "message": "auto compile done", "data": {"artifact": name, "mode": decision.mode}, "timestamp": int(time.time() * 1000)}) + "\n")
        except Exception:
            pass
        # #endregion
    else:
        sink.log(f"Draft saved ({len(body):,} chars) — compile failed; shown as chat text instead.")
        _append_generation_chat_body(state, sink, body)

    sink.refresh_chat()
    sink.scroll_chat()
    sink.sync_preview()
    try:
        from ui.components.preview_workspace import refresh_preview_panel

        refresh_preview_panel()
    except Exception:
        pass


def _append_generation_chat_body(state, sink, body: str) -> None:
    current = ""
    if state.messages and state.messages[-1].get("role") == "assistant":
        current = state.messages[-1].get("content") or ""
    if body not in current:
        sink.set_assistant_content(body)
    sink.notify_preview_ready()


def build_pipeline_config(prof, profile_id, decision: RoutingDecision, user_input: str = "") -> dict:
    from pipeline.i18n import get_locale, infer_language_from_query

    locale = infer_language_from_query(user_input) or get_locale()
    from services.session import state

    from pipeline.capability_runtime.execution_config import execution_mode_from_settings

    execution_mode = execution_mode_from_settings(state.current_settings)
    return {
        "output_type": decision.output_type,
        "mode": decision.mode,
        "llm_type": decision.llm_type,
        "language": locale,
        "execution_mode": execution_mode,
        "chat_execution_mode": execution_mode,
        "profile_id": profile_id,
        "model": prof.get("MODEL", {}).get("preferred_llm"),
        "format": prof.get("OUTPUT", {}).get("format"),
        "style": prof.get("OUTPUT", {}).get("style_rules"),
        "page_breaks": prof.get("OUTPUT", {}).get("page_breaks"),
    }


def build_pipeline_inputs(
    state,
    sink,
    request,
    bundle,
    prof,
    profile_id,
    decision: RoutingDecision,
    gen_model: str,
    workflow_instruction: str,
    *,
    content: str = "",
) -> dict:
    from pipeline.input_metadata import build_input_metadata

    return {
        "state": state,
        "sink": sink,
        "request": request,
        "bundle": bundle,
        "prof": prof,
        "profile_id": profile_id,
        "decision": decision,
        "metadata": build_input_metadata(state),
        "gen_model": gen_model,
        "workflow_instruction": workflow_instruction,
        "user_input": request.user_input,
        "content": content,
        "output_type": decision.output_type,
        "original_filename": bundle.original_filename,
    }


build_capability_config = build_pipeline_config
build_capability_inputs = build_pipeline_inputs


def start_loma_workflow(user_input: str) -> None:
    run_workflow(user_input)
