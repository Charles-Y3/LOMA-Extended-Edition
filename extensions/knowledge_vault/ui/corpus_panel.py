# -*- coding: utf-8 -*-
"""Shared corpus interaction panel."""
from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable

from nicegui import ui

from pipeline.i18n import t as tr
from extensions.knowledge_vault.agent.loop import run_agent
from extensions.knowledge_vault.corpus.types import Mode, ReasoningMode
from extensions.knowledge_vault.retrieval.analyze_action import run_analyze
from extensions.knowledge_vault.retrieval.ask_action import run_ask
from extensions.knowledge_vault.retrieval.cpu_budget import is_cpu_only
from extensions.knowledge_vault.retrieval.search_action import run_search
from extensions.knowledge_vault.retrieval.search_format import format_search_chat
from extensions.knowledge_vault.settings import load_settings
from extensions.knowledge_vault.ui.constants import mode_label, mode_options, query_placeholder
from extensions.knowledge_vault.ui.fs_tree import _tree_parts
from extensions.knowledge_vault.ui.path_links import resolve_abs_path
from extensions.knowledge_vault.ui.tree import path_str, render_root_tree
from extensions.ludicity_shared.panel import BODY_CLS
from services.platform_paths import open_file_in_os
from services.session.chat_post import finish_assistant_message, post_processing_message

_DOC_PANEL_CLS = "w-full flex-1 min-h-0 flex flex-col gap-0 min-w-0 overflow-hidden"
_DOC_FOOTER_CLS = "w-full shrink-0 flex flex-nowrap justify-end gap-2 mt-auto pt-1 pb-0"
_STREAM_THROTTLE_S = 0.35
_ESTIMATED_OUTPUT_TOKENS = 800


def render_corpus_panel(
    *,
    get_backend: Callable[[], Any],
    get_tree: Callable[[], dict],
    library_id: str | None = None,
    semantic_ready: Callable[[], bool] | None = None,
    on_refresh_tree: Callable[[Callable[[], None]], None] | None = None,
    show_expand_collapse: bool = True,
    show_tree: bool = True,
) -> None:
    state: dict[str, Any] = {
        "selected_branch": "",
        "expanded": None,
        "busy": False,
        "agent_stop": False,
    }
    branch_lbl_ref: dict[str, Any] = {"el": None}
    rebuild_cb: dict[str, Any] = {"fn": lambda: None}
    query_in_ref: dict[str, Any] = {"el": None}

    async def _run() -> None:
        if state.get("busy"):
            return
        query_el = query_in_ref.get("el")
        query = ((query_el.value if query_el else "") or "").strip()
        if len(query) < 2:
            ui.notify(tr("knowledge_vault.notify_enter_query"), color="warning")
            return
        backend = get_backend()
        if not backend or not getattr(backend, "lexical", None):
            ui.notify(tr("knowledge_vault.notify_index_not_ready"), color="warning")
            return
        if getattr(backend, "indexing", False):
            ui.notify(tr("knowledge_vault.notify_indexing"), color="info")
            return
        # backend.chunks (not backend.lexical.chunks) — an "all"-scope MultiCorpusBackend
        # exposes .chunks directly but its .lexical (a fan-out-and-merge wrapper, not a
        # real LexicalIndex) has no .chunks attribute of its own; the AttributeError this
        # used to raise here happened before any ui.notify, so clicking Run with "All
        # catalogues" selected silently did nothing. See _run_kv_answer in
        # viewer_runtime/highlight_runner.py, which already had this right.
        if not getattr(backend, "chunks", None):
            ui.notify(tr("knowledge_vault.notify_index_not_ready"), color="warning")
            return
        state["busy"] = True
        state["agent_stop"] = False
        # Library/Session tabs call run_search/run_ask/run_analyze/run_agent directly,
        # bypassing the main chat pipeline entirely — unlike Document Editor's "Ask
        # LOMA" highlight popup, which goes through viewer_runtime/highlight_runner.py's
        # begin_workflow()/end_workflow() + set_progress_detail() calls. Without this,
        # the send button never flips from "▶" to the red stop icon and the
        # "Preparing output…" line above the chat never appears for a query run from
        # here — confirmed the exact same query via Document Editor showed both, via
        # this panel showed neither.
        from services.session.workflow_control import begin_workflow, end_workflow, is_cancelled
        from ui.components.ux_guidance import set_progress_detail

        begin_workflow()
        set_progress_detail("Synthesis")
        branch = state.get("selected_branch") or ""
        # Same deterministic (script-based, not model-guessed) language detection
        # Document Editor's highlight flow uses — without this, synthesize() falls
        # back to asking the model to infer the reply language from the query text
        # itself, which the model doesn't always get right.
        from pipeline.instruction_priority import resolve_response_locale

        kv_locale = resolve_response_locale(query)
        selected_mode = Mode((mode_sel.value or "search").lower())
        mode_text = mode_options().get(selected_mode.value, selected_mode.value)
        cfg = load_settings()
        answer_model = str(cfg.get("answer_model") or "")
        sem = semantic_ready() if semantic_ready else False
        lib_id = library_id
        sem_suggest = bool(lib_id and semantic_ready is not None and not sem)
        proc_label = {
            Mode.SEARCH: tr("knowledge_vault.proc_search"),
            Mode.ASK: tr("knowledge_vault.proc_ask"),
            Mode.ANALYSE: tr("knowledge_vault.proc_analyse"),
            Mode.DEEP: tr("knowledge_vault.proc_deep"),
            Mode.AGENTIC: tr("knowledge_vault.proc_agentic"),
        }.get(selected_mode, tr("knowledge_vault.processing"))
        post_processing_message(tr("knowledge_vault.status_line", proc=proc_label, mode=mode_text))

        if is_cpu_only() and selected_mode in (Mode.DEEP, Mode.AGENTIC):
            from services.system.benchmark import benchmark_tokens_per_second
            from services.system.eta import format_eta

            tps = await asyncio.to_thread(benchmark_tokens_per_second, answer_model)
            if tps:
                eta = format_eta(_ESTIMATED_OUTPUT_TOKENS / tps)
                ui.notify(
                    tr("knowledge_vault.cpu_slow_warning", mode=mode_text, eta=eta), color="warning"
                )

        _last_stream_at = {"t": 0.0}

        def _on_chunk(text: str) -> None:
            now = time.monotonic()
            if now - _last_stream_at["t"] < _STREAM_THROTTLE_S:
                return
            _last_stream_at["t"] = now
            finish_assistant_message(text)

        try:
            from extensions.knowledge_vault.translation import (
                extract_phrase_for_translation,
                extract_target_language,
                format_translation_result,
                is_translation_query,
                target_language_matches,
            )

            if is_translation_query(query):
                # Always the Translation Vault's own store here (not any one
                # catalogue's live index) — so editing/deleting a pair in the
                # Translation Vault tab is immediately reflected in this query box,
                # regardless of which catalogue/scope is currently selected above.
                from extensions.knowledge_vault.translation_vault_store import (
                    vault_translation_index,
                )

                trans_idx = await asyncio.to_thread(vault_translation_index)
                phrase = extract_phrase_for_translation(query)
                target_lang = extract_target_language(query)
                pairs = [
                    p
                    for p in await asyncio.to_thread(trans_idx.lookup, phrase)
                    if target_language_matches(p.target_text, target_lang)
                ]
                if pairs:
                    finish_assistant_message(format_translation_result(query, pairs))
                    from ui.themes.assets import schedule_scroll_chat

                    schedule_scroll_chat()
                    ui.notify(tr("knowledge_vault.translation_posted"), color="positive")
                    return
            from pipeline.state_machine import extension_synthesizing

            with extension_synthesizing():
                if selected_mode == Mode.SEARCH:
                    rows = await asyncio.to_thread(
                        run_search,
                        backend,
                        query,
                        branch=branch,
                        library_id=lib_id,
                        semantic_ready=sem,
                    )
                    finish_assistant_message(
                        format_search_chat(
                            query,
                            rows,
                            mode=selected_mode.value,
                            semantic_suggest=sem_suggest,
                        )
                    )
                elif selected_mode == Mode.AGENTIC:
                    response = await asyncio.to_thread(
                        run_agent,
                        backend,
                        query,
                        branch=branch,
                        library_id=lib_id,
                        semantic_ready=sem,
                        should_stop=lambda: state.get("agent_stop") or is_cancelled(),
                        on_chunk=_on_chunk,
                        locale=kv_locale,
                    )
                    finish_assistant_message(response)
                elif selected_mode == Mode.ASK:
                    response = await asyncio.to_thread(
                        run_ask,
                        backend,
                        query,
                        branch=branch,
                        mode=ReasoningMode.FAST,
                        library_id=lib_id,
                        semantic_ready=sem,
                        semantic_suggest=sem_suggest,
                        on_chunk=_on_chunk,
                        locale=kv_locale,
                    )
                    finish_assistant_message(
                        tr("knowledge_vault.result_header", mode=mode_text, response=response)
                    )
                else:
                    response = await asyncio.to_thread(
                        run_analyze,
                        backend,
                        query,
                        branch=branch,
                        mode=ReasoningMode.DEEP,
                        library_id=lib_id,
                        semantic_ready=sem,
                        semantic_suggest=sem_suggest,
                        deep_extras=selected_mode == Mode.DEEP,
                        on_chunk=_on_chunk,
                        locale=kv_locale,
                    )
                    finish_assistant_message(
                        tr("knowledge_vault.result_header", mode=mode_text, response=response)
                    )
            from ui.themes.assets import schedule_scroll_chat

            schedule_scroll_chat()
            ui.notify(tr("knowledge_vault.results_posted"), color="positive")
        except Exception as exc:
            finish_assistant_message(tr("knowledge_vault.failed_header", error=exc))
            ui.notify(tr("knowledge_vault.failed", error=exc), color="negative")
        finally:
            state["busy"] = False
            set_progress_detail(None)
            end_workflow()

    with ui.column().classes(_DOC_PANEL_CLS):
        with ui.column().classes(BODY_CLS):
            if show_tree:
                with ui.column().classes(
                    "w-full flex-1 min-h-0 gap-1 border border-white/10 rounded-lg p-2 flex flex-col"
                ):
                    with ui.row().classes("w-full items-center justify-between gap-2 shrink-0"):
                        branch_lbl = ui.label(tr("knowledge_vault.all_documents")).classes(
                            "text-[11px] text-gray-400 shrink-0 truncate min-w-0 flex-1"
                        )
                        branch_lbl_ref["el"] = branch_lbl
                        if show_expand_collapse:
                            from extensions.knowledge_vault.ui.fs_tree import collect_folder_paths

                            def _expand_all() -> None:
                                tree = get_tree()
                                exp: set[str] = {""}
                                exp.update(collect_folder_paths(tree, prefix=[]))
                                state["expanded"] = exp
                                rebuild_cb["fn"]()

                            def _collapse_all() -> None:
                                state["expanded"] = set()
                                rebuild_cb["fn"]()

                            with ui.row().classes("gap-1 shrink-0"):
                                ui.button(tr("knowledge_vault.expand_all"), on_click=_expand_all).props(
                                    "dense flat"
                                ).classes("text-[10px] text-gray-400")
                                ui.button(tr("knowledge_vault.collapse_all"), on_click=_collapse_all).props(
                                    "dense flat"
                                ).classes("text-[10px] text-gray-400")

                    tree_scroll = ui.column().classes(
                        "w-full flex-1 min-h-0 overflow-y-auto loma-scroll gap-0 items-stretch"
                    )

                    def _tree_file_chunk(fpath: str):
                        backend = get_backend()
                        for ch in getattr(backend, "chunks", None) or []:
                            if path_str(_tree_parts(ch.ingest_root, ch.vault_path)) == fpath:
                                return ch
                        return None

                    def _open_tree_file(fpath: str) -> None:
                        print(f"[Knowledge Vault] open file requested: {fpath!r}")
                        ch = _tree_file_chunk(fpath)
                        if ch is None:
                            # _tree_file_chunk matches by exact string equality between the
                            # tree's displayed path and each chunk's reconstructed path —
                            # if those two ever disagree (encoding quirks, stray whitespace
                            # in a folder name, a stale tree snapshot), this silently did
                            # nothing at all: no error, no notify, indistinguishable from
                            # the click not registering in the first place.
                            print(f"[Knowledge Vault]   no chunk matched {fpath!r}")
                            ui.notify(tr("knowledge_vault.file_open_not_found", path=fpath), color="negative")
                            return
                        abs_path = resolve_abs_path(ch.file_path)
                        print(f"[Knowledge Vault]   opening: {abs_path}")
                        try:
                            open_file_in_os(abs_path)
                        except Exception as exc:
                            print(f"[Knowledge Vault]   failed to open: {exc}")
                            ui.notify(str(exc)[:200], color="negative")
                        else:
                            ui.notify(tr("knowledge_vault.file_opened", name=os.path.basename(abs_path)), color="positive")

                    def _delete_tree_file(fpath: str) -> None:
                        backend = get_backend()
                        if not hasattr(backend, "delete_file"):
                            return
                        ch = _tree_file_chunk(fpath)
                        if ch is None:
                            return
                        fname = fpath.rsplit("/", 1)[-1]
                        abs_path = resolve_abs_path(ch.file_path)

                        def _do_delete() -> None:
                            dlg.close()
                            if backend.delete_file(abs_path):
                                if state.get("selected_branch") == fpath:
                                    state["selected_branch"] = ""
                                ui.notify(tr("knowledge_vault.file_deleted", name=fname), color="positive")
                                rebuild_cb["fn"]()

                        with ui.dialog() as dlg, ui.card().classes("gap-3 p-4 min-w-[280px]"):
                            ui.label(tr("knowledge_vault.file_delete_title", name=fname)).classes(
                                "text-sm font-medium"
                            )
                            ui.label(tr("knowledge_vault.file_delete_body")).classes(
                                "text-[11px] text-gray-400"
                            )
                            with ui.row().classes("w-full justify-end gap-2"):
                                ui.button(tr("common.cancel"), on_click=dlg.close).props(
                                    "dense flat"
                                )
                                ui.button(tr("common.delete"), on_click=_do_delete).props(
                                    "dense"
                                ).classes("bg-red-800 text-xs")
                        dlg.open()

                    def _toggle_exclude_tree_file(fpath: str, excluded: bool) -> None:
                        backend = get_backend()
                        if not hasattr(backend, "set_retrieval_excluded"):
                            return
                        ch = _tree_file_chunk(fpath)
                        if ch is None:
                            return
                        fname = fpath.rsplit("/", 1)[-1]
                        abs_path = resolve_abs_path(ch.file_path)
                        if backend.set_retrieval_excluded(abs_path, excluded):
                            ui.notify(
                                tr("knowledge_vault.file_excluded", name=fname)
                                if excluded
                                else tr("knowledge_vault.file_included", name=fname),
                                color="info",
                            )
                            rebuild_cb["fn"]()

                    def _rebuild_tree() -> None:
                        tree_scroll.clear()
                        with tree_scroll:
                            render_root_tree(
                                tree_scroll,
                                get_tree(),
                                state=state,
                                on_rebuild=_rebuild_tree,
                                branch_lbl_ref=branch_lbl_ref,
                                on_open_file=_open_tree_file,
                                on_delete_file=(
                                    _delete_tree_file
                                    if hasattr(get_backend(), "delete_file")
                                    else None
                                ),
                                on_toggle_exclude=(
                                    _toggle_exclude_tree_file
                                    if hasattr(get_backend(), "set_retrieval_excluded")
                                    else None
                                ),
                            )

                    rebuild_cb["fn"] = _rebuild_tree
                    _rebuild_tree()
                    if on_refresh_tree:
                        on_refresh_tree(_rebuild_tree)

            with ui.column().classes(
                "w-full shrink-0 grow-0 flex-none gap-1.5 border border-white/10 "
                "rounded-lg p-2 flex flex-col overflow-visible"
            ):
                with ui.row().classes("w-full gap-2 items-start shrink-0"):
                    mode_sel = ui.select(
                        mode_options(),
                        value="search",
                        label=mode_label(),
                    ).props("dense outlined dark options-dense").classes(
                        "flex-1 min-w-0 text-[11px]"
                    )
                query_in = (
                    ui.textarea(placeholder=query_placeholder())
                    .props("outlined rows=2 autogrow=false hide-bottom-space")
                    .classes("w-full shrink-0 grow-0 text-xs loma-hint-placeholder")
                    .style("resize: none;")
                )
                query_in_ref["el"] = query_in

        def _stop() -> None:
            from services.session.workflow_control import request_cancel

            state["agent_stop"] = True
            request_cancel()

        with ui.row().classes(_DOC_FOOTER_CLS):
            ui.button(
                tr("knowledge_vault.stop"),
                on_click=_stop,
            ).props("flat dense no-caps")
            ui.button(tr("knowledge_vault.run"), on_click=_run, icon="play_arrow").props(
                "flat dense no-caps color=primary"
            )
