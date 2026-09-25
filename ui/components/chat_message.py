# -*- coding: utf-8 -*-
import html
import os
import re

import json

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import handlers, state
from ui.themes import registry, tokens
from ui.themes.assets import schedule_scroll_chat, schedule_scroll_chat_to_id_prefix

_FIGURE_MARKER_RE = re.compile(r"\[\[FIGURE:(\d+)(?::([^\]]+))?\]\]", re.IGNORECASE)


def _resolve_image_src(path: str) -> str | None:
    raw = (path or "").strip().strip('"').strip("'")
    if not raw:
        return None
    if raw.startswith(("http://", "https://", "data:")):
        return raw
    candidate = raw.replace("\\", os.sep)
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(candidate)
    if os.path.isfile(candidate):
        return candidate
    return None


def _charts_by_slot() -> dict[int, object]:
    out: dict[int, object] = {}
    for ch in getattr(state, "chart_artifacts", None) or []:
        slot = int(getattr(ch, "slot", 0) or 0)
        if slot:
            out[slot] = ch
    return out


def _render_figures_at_end(slots: list[tuple[int, str]], *, allow_images: bool) -> None:
    if not allow_images or not slots:
        return
    by_slot = _charts_by_slot()
    for slot, title in slots:
        ch = by_slot.get(slot)
        path = getattr(ch, "path", "") if ch else ""
        resolved = _resolve_image_src(path)
        if not resolved:
            continue
        label = title or getattr(ch, "title", f"Figure {slot}")

        def _open(p=path or resolved) -> None:
            from services.platform_paths import open_file_in_os

            try:
                open_file_in_os(p)
            except Exception as exc:
                ui.notify(str(exc)[:120], color="negative")

        ui.image(resolved).props("fit=contain").classes(
            "w-full max-w-full max-h-[56rem] rounded-lg border border-white/10 my-2 cursor-pointer "
            "hover:opacity-90 transition-opacity"
        ).on("click", _open).tooltip(tr("chat.figure_open_tooltip"))
        ui.label(f"Figure {slot}: {label}").classes("text-[11px] opacity-70")


def _chat_font_px() -> int:
    try:
        from services.session import state

        return max(11, min(20, int((state.current_settings or {}).get("chat_font_px", 13))))
    except (TypeError, ValueError):
        return 13


def _copy_message(text: str) -> None:
    from pipeline.i18n import t as tr

    payload = json.dumps(text or "")
    ui.run_javascript(f"navigator.clipboard.writeText({payload})")
    ui.notify(tr("chat.copy_done"), color="positive", timeout=1500)


def _legacy_loma_open_to_html(text: str) -> str:
    """Convert stripped loma-open markdown links to loma-os-open HTML (q-markdown drops the scheme)."""
    from urllib.parse import unquote

    def _repl(m: re.Match[str]) -> str:
        label, encoded = m.group(1), m.group(2)
        path = unquote(encoded)
        esc_path = html.escape(path, quote=True)
        esc_label = html.escape(label, quote=False)
        return f'<a href="#" class="loma-os-open" data-path="{esc_path}">{esc_label}</a>'

    return re.sub(
        r"\[([^\]]+)\]\(loma-open:([^)]+)\)",
        _repl,
        text,
    )


def _formslator_result_path(text: str) -> str | None:
    """Resolve formatted output path from a Formslator workspace message."""
    from urllib.parse import unquote

    from services.formslator.paths import OUTPUT_DIR

    m = re.search(r"Formatted:\s*(.+)", text)
    if m:
        candidate = os.path.abspath(m.group(1).strip())
        if os.path.isfile(candidate):
            return candidate

    m = re.search(r"\[([^\]]+)\]\(loma-open:([^)]+)\)", text)
    if m:
        candidate = os.path.abspath(unquote(m.group(2)))
        if os.path.isfile(candidate):
            return candidate

    m = re.search(r'data-path="([^"]+)"', text)
    if m:
        candidate = os.path.abspath(html.unescape(m.group(1)))
        if os.path.isfile(candidate):
            return candidate

    saved_labels = (
        tr("formslator.log.saved").rstrip(":"),
        "Saved",
    )
    for lbl in saved_labels:
        m = re.search(
            rf"\*\*{re.escape(lbl)}:?\*\*\s*(?:<[^>]+>)?([^<\n]+)",
            text,
        )
        if m:
            bn = html.unescape(m.group(1).strip())
            candidate = os.path.abspath(os.path.join(OUTPUT_DIR, bn))
            if os.path.isfile(candidate):
                return candidate
    return None


def _strip_formslator_link_lines(text: str) -> str:
    for key in ("formslator.log.saved", "formslator.log.output_folder"):
        lbl = re.escape(tr(key).rstrip(":"))
        text = re.sub(rf"\n\n\*\*{lbl}:?\*\*[^\n]*", "", text)
    text = re.sub(r"\n\n\*\*Saved:\*\*[^\n]*", "", text)
    text = re.sub(r"\n\n\*\*Output folder:\*\*[^\n]*", "", text)
    return text.rstrip()


def _is_formslator_job_log(text: str) -> bool:
    return tr("formslator.log.header") in text or "**Formslator — job log**" in text


def _render_formslator_open_links(result_path: str) -> None:
    from extensions.formslator.download_ui import open_output_file, open_output_folder
    from services.formslator.paths import OUTPUT_DIR

    bn = os.path.basename(result_path)
    link_cls = "text-blue-400 underline px-0 min-h-0 normal-case cursor-pointer"
    with ui.column().classes("gap-1 mt-1 w-full"):
        with ui.row().classes("items-baseline gap-1 flex-nowrap w-full"):
            ui.label(tr("formslator.log.saved")).classes("font-semibold shrink-0")
            ui.button(
                bn,
                on_click=lambda p=result_path: open_output_file(p),
            ).props("flat dense no-caps").classes(link_cls)
        with ui.row().classes("items-baseline gap-1 flex-nowrap w-full"):
            ui.label(tr("formslator.log.output_folder")).classes("font-semibold shrink-0")
            ui.button(
                OUTPUT_DIR,
                on_click=open_output_folder,
            ).props("flat dense no-caps").classes(link_cls)


def _insert_target_excerpt(idx: int) -> str:
    """The highlighted excerpt this assistant message answers, if this message
    is eligible to be inserted into the open Document Editor document — i.e. it
    was triggered from a highlight (the preceding user message carries the
    localized excerpt-prefix wrapper) and a .docx is currently open in edit mode.
    Returns "" when not eligible, which callers use as the signal to hide the
    insert buttons entirely."""
    if idx <= 0 or idx > len(state.messages):
        return ""
    msg = state.messages[idx]
    if msg.get("kv_mode") == "search":
        # A Search-mode reply is a ranked list of raw snippets, not prose fit for
        # splicing into a document — Ask/Analyze/Deep/Agentic stay eligible.
        return ""
    prev = state.messages[idx - 1]
    if prev.get("role") != "user":
        return ""
    prev_content = prev.get("content") or ""
    try:
        from pipeline.direct.highlight_excerpt import extract_highlight_excerpt, is_highlight_query
    except Exception:
        return ""
    if not is_highlight_query(prev_content):
        return ""
    excerpt = extract_highlight_excerpt(prev_content)
    if not excerpt:
        return ""
    try:
        from extensions.document_editor.insert_from_chat import insert_allowed
    except Exception:
        return ""
    if not insert_allowed():
        return ""
    return excerpt


def _insert_chat_reply(excerpt: str, content: str, *, summarize: bool) -> None:
    from pipeline.i18n import t as tr

    try:
        from extensions.document_editor.insert_from_chat import (
            insert_allowed,
            insert_text_below_excerpt,
            start_insert_summary,
            strip_citations_for_insert,
        )
    except Exception as exc:
        ui.notify(str(exc)[:200], color="negative")
        return

    # Re-check here (not just at button-render time): the insert buttons are
    # computed once per chat render and don't disappear the instant the user
    # flips Document Editor to View mode, so a stale button click needs its own
    # clear message instead of falling through to the generic "insert failed".
    if not insert_allowed():
        ui.notify(tr("chat.insert_edit_mode_required"), color="warning")
        return

    cleaned = strip_citations_for_insert(content)
    if not cleaned:
        ui.notify(tr("chat.insert_failed"), color="negative")
        return

    if summarize:
        ui.notify(tr("chat.insert_summarizing"), color="info")

        def _done(ok: bool) -> None:
            if ok:
                ui.notify(tr("chat.insert_done"), color="positive")
            elif not insert_allowed():
                ui.notify(tr("chat.insert_edit_mode_required"), color="warning")
            else:
                ui.notify(tr("chat.insert_failed"), color="negative")

        start_insert_summary(excerpt, cleaned, on_complete=_done)
        return

    ok = insert_text_below_excerpt(excerpt, cleaned)
    if ok:
        ui.notify(tr("chat.insert_done"), color="positive")
    elif not insert_allowed():
        ui.notify(tr("chat.insert_edit_mode_required"), color="warning")
    else:
        ui.notify(tr("chat.insert_failed"), color="negative")


def _render_artifact_card(artifact_path: str) -> None:
    """Inline card for a generated deliverable — LOMA has no separate output panel."""
    from services.platform_paths import open_path_in_os
    from ui.components.loma_notify import notify

    if not artifact_path or not os.path.exists(artifact_path):
        return
    bn = os.path.basename(artifact_path)

    def _open() -> None:
        try:
            open_path_in_os(artifact_path)
        except Exception as ex:
            notify(tr("output.open_folder_failed", error=str(ex)), color="negative")

    with ui.row().classes(
        "items-center gap-2 mt-1 px-3 py-2 rounded-lg bg-blue-500/10 border border-blue-500/20 w-full"
    ):
        ui.icon("description", size="18px").classes("text-blue-400 shrink-0")
        ui.label(bn).classes("text-xs font-semibold truncate flex-1 min-w-0")
        ui.button(icon="open_in_new", on_click=_open).props(
            "flat round dense size=sm"
        ).classes("text-blue-400 shrink-0").tooltip(tr("output.open_artifact_tooltip"))


def _render_style_picker_retry_button(token: str) -> None:
    """"Reopen picker" button for a poster/presentation style-picker prompt
    message — shown only while it's the LAST message, so it doesn't linger
    stale in older chat history. Without this, clicking away from the style
    dialog (instead of picking one) left the request permanently stuck: the
    picker never reappears on its own, and the only way forward was retyping
    the same request from scratch.

    Reopens the SAME dialog with its original on_select callback (looked up
    in ui.themes.registry.style_picker_reopeners by `token` — see
    pipeline/direct/entry.py::_run_presentation_style_picker and
    pipeline/direct/step_executor.py::_run_poster_style_picker) rather than
    resubmitting the request as a new chat turn: that used to regenerate
    everything and duplicate the user's message in the transcript every
    click. Those same call sites clear the token the moment a style is
    actually picked, so this button can't fire the (now-stale) callback
    twice and stops appearing once the request completes."""
    from ui.themes import registry

    def _reopen() -> None:
        fn = registry.style_picker_reopeners.get(token)
        if fn:
            fn()

    ui.button(
        tr("chat.reopen_picker"),
        icon="refresh",
        on_click=_reopen,
    ).props("flat dense no-caps").classes(
        "mt-1 text-blue-400 hover:text-blue-300 self-start"
    )


def _render_doc_intel_path_buttons(blocks: list[dict[str, str]], *, font_px: int | None = None) -> None:
    from extensions.knowledge_vault.ui.open_path import open_local_path

    if not blocks:
        return
    # font_px matches the surrounding markdown's dynamic size (_chat_font_px) so
    # these lines scale with the user's font +/- controls instead of staying
    # fixed regardless of it, and so they read the same size as the answer
    # prose above them rather than visibly smaller.
    px = font_px if font_px is not None else _chat_font_px()
    size_style = f"font-size: {px}px; line-height: 1.4;"
    link_cls = "text-blue-400 underline px-0 min-h-0 normal-case cursor-pointer"
    for block in blocks:
        with ui.column().classes("gap-0.5 w-full").style(size_style):
            with ui.row().classes("items-baseline gap-1 flex-wrap w-full"):
                ui.label(tr("di.path_folder") + ":").classes("shrink-0").style(size_style)
                ui.button(
                    block["folder_label"],
                    on_click=lambda p=block["folder_path"]: open_local_path(p),
                ).props("flat dense no-caps").classes(link_cls).style(size_style)
            with ui.row().classes("items-baseline gap-1 flex-wrap w-full"):
                ui.label(tr("di.path_file") + ":").classes("shrink-0").style(size_style)
                ui.button(
                    block["file_label"],
                    on_click=lambda p=block["file_path"]: open_local_path(p),
                ).props("flat dense no-caps").classes(link_cls).style(size_style)


def _render_message_content(
    content: str,
    *,
    is_processing: bool,
    chart_figures_ready: bool = False,
    font_px: int | None = None,
) -> None:
    from services.graph_generation.report_layout import strip_figure_markers

    raw = content or ""
    px = font_px if font_px is not None else _chat_font_px()
    md_style = (
        f"font-size: {px}px; line-height: 1.55; word-break: break-word; overflow-wrap: anywhere;"
    )
    md_classes = (
        "break-words [overflow-wrap:anywhere] "
        "[&_pre]:whitespace-pre-wrap [&_pre]:break-words "
        "[&_code]:whitespace-pre-wrap [&_code]:break-words"
    )

    if "**Document Intelligence**" in raw:
        # Render text/path-block parts in document order (one ui.markdown call per
        # text run, a button pair right after it) instead of stripping every path
        # block out and rendering them as one list at the end — each citation's
        # folder/file link then stays right under that citation, not regrouped
        # with every other citation's links.
        from extensions.knowledge_vault.ui.chat_paths import split_content_with_path_blocks

        any_text = False
        all_slots: list[tuple[int, str]] = []
        for part in split_content_with_path_blocks(raw):
            if isinstance(part, dict):
                _render_doc_intel_path_buttons([part], font_px=px)
                continue
            text = _legacy_loma_open_to_html(part)
            display_text, slots = strip_figure_markers(text)
            all_slots.extend(slots)
            if display_text.strip():
                any_text = True
                ui.markdown(display_text).classes(md_classes).style(
                    md_style
                )
        if not any_text and is_processing:
            ui.markdown("").classes(f"{md_classes} italic opacity-80").style(md_style)
        if chart_figures_ready and not is_processing and all_slots:
            _render_figures_at_end(all_slots, allow_images=True)
        return

    text = _legacy_loma_open_to_html(raw)
    formslator_path = _formslator_result_path(text) if _is_formslator_job_log(text) else None
    if formslator_path:
        text = _strip_formslator_link_lines(text)
    display_text, slots = strip_figure_markers(text)
    allow_images = not is_processing
    if display_text.strip():
        ui.markdown(display_text).classes(md_classes).style(md_style)
    elif is_processing:
        ui.markdown(display_text).classes(f"{md_classes} italic opacity-80").style(md_style)
    else:
        try:
            from services.model_assignments import has_usable_chat_model

            if not has_usable_chat_model():
                ui.markdown(tr("chat.no_models_installed")).classes(
                    f"{md_classes} italic opacity-80"
                ).style(md_style)
        except Exception:
            pass

    if formslator_path:
        _render_formslator_open_links(formslator_path)

    if display_text.strip() and not formslator_path:
        ui.run_javascript(
            """
            (() => {
              const roots = document.querySelectorAll(
                '.loma-chat-assistant .q-markdown, .loma-chat-assistant .nicegui-markdown'
              );
              const root = roots[roots.length - 1];
              if (!root) return;
              root.querySelectorAll('a.loma-os-open, a[href^="loma-open:"]').forEach(a => {
                const href = a.getAttribute('href') || '';
                if (href.startsWith('loma-open:')) {
                  if (a.dataset.lomaOsBound === '1') return;
                  a.dataset.lomaOsBound = '1';
                  a.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    const path = decodeURIComponent(href.slice('loma-open:'.length));
                    if (!path) return;
                    fetch('/loma/open-path', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ path: path })
                    }).catch(() => {});
                  }, true);
                  return;
                }
                if (a.classList.contains('loma-os-open')) {
                  if (a.dataset.lomaOsBound === '1') return;
                  a.dataset.lomaOsBound = '1';
                  a.addEventListener('click', (e) => {
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    const path = a.getAttribute('data-path') || '';
                    if (!path) return;
                    fetch('/loma/open-path', {
                      method: 'POST',
                      headers: { 'Content-Type': 'application/json' },
                      body: JSON.stringify({ path: path })
                    }).catch(() => {});
                  }, true);
                  return;
                }
                if (!href || href.startsWith('#')) return;
                a.setAttribute('target', '_blank');
                a.setAttribute('rel', 'noopener noreferrer');
                if (a.dataset.lomaExternal === '1') return;
                a.dataset.lomaExternal = '1';
                a.addEventListener('click', (e) => {
                  if (e.defaultPrevented || e.button === 1) return;
                  e.preventDefault();
                  e.stopImmediatePropagation();
                  window.open(href, '_blank', 'noopener,noreferrer');
                }, true);
              });
            })();
            """
        )

    if chart_figures_ready and allow_images and slots:
        _render_figures_at_end(slots, allow_images=True)


@ui.refreshable
def render_chat() -> None:
    from pipeline.i18n import t as tr

    registry.chat_container.clear()
    t = tokens.get_theme()
    font_px = _chat_font_px()
    if registry.chat_scroll is not None:
        registry.chat_scroll.style(f"--loma-chat-font-size: {font_px}px")

    with registry.chat_container:
        for idx, msg in enumerate(state.messages):
            is_user = msg["role"] == "user"
            bubble = t["chat_user"] if is_user else t["chat_assistant"]
            role_cls = "loma-chat-user" if is_user else "loma-chat-assistant"
            is_live_assistant = (
                not is_user
                and state.workflow_active
                and idx == len(state.messages) - 1
            )
            msg_row = ui.row().classes(f'w-full {"justify-end" if is_user else "justify-start"} mb-4')
            if msg.get("anchor"):
                msg_row.props(f'id={msg["anchor"]}')
            with msg_row:
                with ui.column().classes(
                    "max-w-[85%] gap-2 "
                    + ("items-end" if is_user else "items-start")
                ):
                    if not is_user and (msg.get("thinking") or "").strip():
                        expansion_props = "dense header-class=text-gray-400"
                        if is_live_assistant:
                            expansion_props += " default-opened"
                        with ui.expansion("Reasoning", icon="psychology").classes(
                            "w-full text-[11px] opacity-80"
                        ).props(expansion_props):
                            ui.markdown(msg["thinking"]).classes(
                                "p-3 rounded-lg text-[11px] leading-relaxed break-words "
                                "[overflow-wrap:anywhere] italic text-gray-400 "
                                "bg-black/20 border border-white/5"
                            )
                    content = msg.get("content", "") or ""
                    if not is_user and not content.strip() and (
                        msg.get("processing") or is_live_assistant
                    ):
                        from pipeline.i18n import t as tr
                        from services.session.workflow_control import workflow_processing_message

                        content = workflow_processing_message()
                    bubble_cls = (
                        f"{role_cls} {bubble} italic opacity-80"
                        if msg.get("processing") and not is_user
                        else f"{role_cls} {bubble}"
                    )
                    is_processing = bool(
                        msg.get("processing") or (is_live_assistant and state.workflow_active)
                    )
                    is_reboot_notice = (
                        not is_user
                        and idx == 0
                        and "reboot" in content.lower()
                    )
                    copy_btn_props = "flat round dense size=sm"
                    copy_btn_cls = "opacity-40 hover:opacity-100 shrink-0 self-end mb-1"

                    if is_user:
                        with ui.row().classes("w-full items-end justify-end gap-1"):
                            ui.button(
                                icon="content_copy",
                                on_click=lambda c=content: _copy_message(c),
                            ).props(copy_btn_props).classes(copy_btn_cls).tooltip(
                                tr("chat.copy_tooltip")
                            )
                            with ui.column().classes(f"{bubble_cls} gap-2"):
                                _render_message_content(
                                    content,
                                    is_processing=is_processing or is_reboot_notice,
                                    chart_figures_ready=bool(msg.get("chart_figures_ready")),
                                    font_px=font_px,
                                )
                    else:
                        insert_excerpt = (
                            "" if is_processing else _insert_target_excerpt(idx)
                        )
                        with ui.row().classes("w-full items-end justify-start gap-1"):
                            with ui.column().classes(f"{bubble_cls} gap-2"):
                                _render_message_content(
                                    content,
                                    is_processing=is_processing or is_reboot_notice,
                                    chart_figures_ready=bool(msg.get("chart_figures_ready")),
                                    font_px=font_px,
                                )
                                if msg.get("artifact_path"):
                                    _render_artifact_card(msg["artifact_path"])
                                retry_token = msg.get("style_picker_token")
                                if retry_token and idx == len(state.messages) - 1:
                                    _render_style_picker_retry_button(retry_token)
                            if insert_excerpt:
                                ui.button(
                                    icon="post_add",
                                    on_click=lambda e=insert_excerpt, c=content: _insert_chat_reply(
                                        e, c, summarize=False
                                    ),
                                ).props(copy_btn_props).classes(copy_btn_cls).tooltip(
                                    tr("chat.insert_into_document_tooltip")
                                )
                                ui.button(
                                    icon="summarize",
                                    on_click=lambda e=insert_excerpt, c=content: _insert_chat_reply(
                                        e, c, summarize=True
                                    ),
                                ).props(copy_btn_props).classes(copy_btn_cls).tooltip(
                                    tr("chat.insert_summary_tooltip")
                                )
                            ui.button(
                                icon="content_copy",
                                on_click=lambda c=content: _copy_message(c),
                            ).props(copy_btn_props).classes(copy_btn_cls).tooltip(
                                tr("chat.copy_tooltip")
                            )
    # Scroll target is read off the last message itself (not a separate mutable
    # flag) so it's always in lockstep with what actually just rendered — no
    # ordering to get right between "set" and "clear" across async UI-thread
    # dispatches.
    last_scroll_to = state.messages[-1].get("scroll_to") if state.messages else None
    if last_scroll_to:
        schedule_scroll_chat_to_id_prefix(last_scroll_to)
    else:
        schedule_scroll_chat()
