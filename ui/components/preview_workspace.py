# -*- coding: utf-8 -*-
"""Live Preview workspace — bound to session draft state and artifact export."""
from __future__ import annotations

import os

from nicegui import ui

from pipeline.i18n import t as tr
from pipeline.output_format import EXTENSION_BY_TYPE
from services.session import draft as draft_sync
from services.session import state
from services.session.preview_selection import capture_selection_from_editor
from ui.components.markup_viewer import build_markup_pane
from ui.components.preview_scroll import build_preview_scroll_pane
from ui.components.source_viewer_mode import (
    build_source_viewer_pills,
    is_viewer_mode,
    mode_hint,
    normalize_source_viewer_mode,
    supports_viewer_mode,
)
from ui.components.viewer_selection import read_dom_selection
from ui.themes import registry

PREVIEW_TYPE_EXT = dict(EXTENSION_BY_TYPE)
PREVIEW_TYPE_EXT.setdefault("image", ".jpg")
PREVIEW_TYPE_EXT.setdefault("sound", ".mp3")


def normalize_preview_view_mode(mode: str | None) -> str:
    return normalize_source_viewer_mode(mode)


def _is_source_mode(mode: str | None = None) -> bool:
    return not _is_viewer_mode(mode)


def _is_viewer_mode(mode: str | None = None) -> bool:
    return is_viewer_mode(mode or state.preview_view_mode)


def _resolve_preview_viewer_html() -> str:
    from services.renderer import resolve_markup_html

    output_type = _classified_output_type()
    draft = draft_sync.get_draft()
    artifact_path = _artifact_file_path()
    use_artifact = (
        artifact_path
        and state.artifact_ready
        and not state.preview_dirty
        and output_type in ("document", "presentation", "spreadsheet")
    )
    return resolve_markup_html(
        path=artifact_path if use_artifact else None,
        draft=draft,
        output_type=output_type,
    )


def _preview_supports_source_viewer(output_type: str) -> bool:
    return not _is_binary_preview_type(output_type)


def _preview_allows_viewer(output_type: str) -> bool:
    return _preview_supports_source_viewer(output_type) and supports_viewer_mode(output_type)


def _artifact_file_path() -> str | None:
    path = (state.last_generated_file_path or "").strip()
    if not path:
        return None
    if not os.path.isabs(path):
        path = os.path.abspath(path)
    if os.path.isfile(path):
        return path
    return None


def _preview_image_src(path: str) -> str | None:
    """Return a src the browser can always paint (data URI preferred for Preview)."""
    import base64
    import mimetypes

    if not path or not os.path.isfile(path):
        return None
    abs_path = os.path.abspath(path)
    try:
        size = os.path.getsize(abs_path)
    except OSError:
        return None
    # ~6MB raw → safe for inline preview; larger files fall back to static/local route.
    if size <= 6_000_000:
        mime = mimetypes.guess_type(abs_path)[0] or "image/png"
        with open(abs_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    images_root = os.path.abspath(os.path.join("data", "generated", "images"))
    try:
        if os.path.commonpath([abs_path, images_root]) == images_root:
            rel = os.path.relpath(abs_path, images_root).replace("\\", "/")
            return f"/loma-generated-images/{rel}"
    except ValueError:
        pass
    return abs_path


def _is_binary_preview_type(output_type: str) -> bool:
    return output_type in ("image", "sound", "video")


def _classified_output_type() -> str:
    path = (state.last_generated_file_path or "").lower()
    # Ready image artifacts always preview as images (fixes missing picture when
    # live_workspace_output_type was not stamped as "image").
    if state.artifact_ready and path.endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp")):
        return "image"
    return (
        state.live_workspace_output_type
        or state.pending_output_type
        or "document"
    )


def sync_preview_editor(
    refresh_panel: bool = False,
    *,
    preserve_scroll: bool = False,
    scroll_to_bottom: bool = False,
) -> None:
    text = draft_sync.get_draft()
    view_mode = normalize_preview_view_mode(state.preview_view_mode)
    scroll_top = int(getattr(state, "preview_scroll_top", 0) or 0)
    follow_stream = scroll_to_bottom or state.workflow_active

    char_lbl = registry.preview_char_label
    if char_lbl is not None:
        char_lbl.set_text(tr("preview.chars", count=f"{len(text):,}"))

    hint_lbl = registry.preview_hint_label
    if hint_lbl is not None and not state.mutation_in_progress:
        hint_lbl.set_text(mode_hint(view_mode, editable=True, output_type=_classified_output_type()))

    if view_mode == "source":
        editor = registry.preview_editor
        if editor is not None:
            try:
                editor.value = text
                editor.update()
            except Exception:
                refresh_panel = True
        elif text.strip():
            refresh_panel = True

        if editor is not None and preserve_scroll and not follow_stream and scroll_top > 0:
            try:
                from nicegui import ui as ng_ui

                top = scroll_top
                ng_ui.run_javascript(
                    f"""
                    () => {{
                        const wrap = document.querySelector('.loma-preview-scroll-wrap');
                        const textarea = wrap && wrap.querySelector('.loma-preview-editor textarea');
                        const container = wrap && wrap.querySelector('.q-scrollarea__container');
                        const scroller = textarea || container;
                        if (scroller) scroller.scrollTop = {top};
                    }}
                    """
                )
            except Exception:
                pass
    else:
        output_type = _classified_output_type()
        holder = registry.preview_markup_holder
        if holder is not None and _preview_supports_source_viewer(output_type):
            try:
                build_markup_pane(holder, _resolve_preview_viewer_html())
            except Exception:
                refresh_panel = True
        elif text.strip() and _preview_supports_source_viewer(output_type):
            refresh_panel = True

    if follow_stream and not refresh_panel:
        from ui.themes.assets import schedule_scroll_preview

        schedule_scroll_preview()

    if refresh_panel:
        refresh_preview_panel()


def refresh_preview_panel() -> None:
    ui_mod = state.get_ui_module()
    if hasattr(ui_mod, "render_preview_tab") and hasattr(ui_mod.render_preview_tab, "refresh"):
        ui_mod.render_preview_tab.refresh()


def expected_artifact_basename(output_type: str) -> str:
    if state.last_generated_file_path:
        return os.path.basename(state.last_generated_file_path)
    ext = PREVIEW_TYPE_EXT.get(output_type, ".txt")
    return f"output_LOMA{ext}"


def update_preview_status_label(output_type: str | None = None) -> None:
    label = registry.preview_status_label
    if label is None:
        return
    out_type = output_type or state.live_workspace_output_type or "document"
    color = "text-green-400"
    if state.mutation_in_progress:
        cur = state.mutation_slide_current
        total = state.mutation_slide_total
        text = (
            tr("preview.status.processing_slide", cur=cur, total=total)
            if total > 0
            else tr("preview.status.processing_mutation")
        )
        color = "text-amber-300"
    elif state.preview_selection:
        n = len(state.preview_selection)
        text = tr("preview.status.selection_locked", count=f"{n:,}")
        color = "text-cyan-300"
    elif state.preview_dirty:
        text = tr("preview.status.unsaved")
        color = "text-amber-300"
    elif state.workflow_active:
        pending = (state.pending_output_type or state.live_workspace_output_type or "").strip().lower()
        if pending and pending != "chat":
            text = tr("preview.status.generating")
            color = "text-amber-300"
    elif (state.artifact_ready and state.last_generated_file_path) or draft_sync.get_draft():
        text = tr("preview.status.ready", name=expected_artifact_basename(out_type))
        color = "text-green-400"
    else:
        text = tr("preview.no_artifact")
        color = "text-gray-500"
    label.set_text(text)
    label.classes(remove="text-green-400 text-cyan-300 text-amber-300 text-gray-500", add=color)


def open_revision_prompt(*, revise_allowed: bool | None = None) -> None:
    sel = (state.preview_selection or "").strip()
    if not sel:
        return
    if revise_allowed is None:
        revise_allowed = _is_source_mode()

    preview = sel if len(sel) <= 240 else sel[:237] + "..."
    with ui.dialog() as dialog, ui.card().classes("min-w-[340px] max-w-lg gap-3 p-4"):
        ui.label(tr("preview.selected_text")).classes("text-sm font-bold text-cyan-300")
        ui.label(preview).classes(
            "text-[11px] text-gray-400 font-mono max-h-28 overflow-y-auto break-words "
            "whitespace-pre-wrap [overflow-wrap:anywhere] "
            "bg-black/30 rounded p-2 border border-white/10"
        )
        instruction = ui.input(tr("preview.how_can_i_help")).props("outlined dense dark autofocus").classes(
            "w-full text-[12px]"
        )
        from ui.components.viewer_selection import render_last_query_chip

        render_last_query_chip(instruction)

        def apply_and_close() -> None:
            text = (instruction.value or "").strip()
            dialog.close()
            if not revise_allowed:
                ui.notify(tr("preview.switch_to_source_to_edit"), color="warning")
                return
            if not text:
                ui.notify(tr("preview.describe_change"), color="warning")
                return
            from services.session.prompt_memory import remember_typed_prompt

            remember_typed_prompt(text)
            from services.session.preview_revision import start_preview_revision

            ui.notify(tr("preview.revising_selection"), color="info")
            start_preview_revision(text)

        def ask_and_close() -> None:
            text = (instruction.value or "").strip()
            dialog.close()
            if not text:
                ui.notify(tr("preview.enter_question"), color="warning")
                return
            from services.session.prompt_memory import remember_typed_prompt

            remember_typed_prompt(text)
            from services.session.preview_revision import submit_preview_ask

            ui.notify(tr("preview.answering_selection"), color="info")
            submit_preview_ask(text)

        with ui.row().classes("w-full justify-end gap-2 mt-1"):
            ui.button(tr("preview.cancel"), on_click=dialog.close).props("flat dense")
            ui.button(tr("preview.ask_loma"), on_click=ask_and_close).props("flat dense").classes(
                "text-cyan-300"
            )
            revise_btn = ui.button(tr("preview.revise"), on_click=apply_and_close).props("dense color=primary")
            if not revise_allowed:
                revise_btn.props("flat dense")
                revise_btn.classes("opacity-50")

        instruction.on("keydown.enter", apply_and_close)
    dialog.open()


def _render_image_quality_buttons() -> None:
    """Point to Artwork Studio for regenerating at higher quality — it already
    offers a superset of what this used to do (freely adjustable steps 4-40
    vs. 3 fixed presets, plus edit/composite), so this just opens it instead
    of duplicating a lesser version of the same capability."""
    from ui.layouts.extension_panel import show_extension

    with ui.row().classes("w-full gap-1 shrink-0 items-center min-w-0 overflow-hidden"):
        ui.label(tr("preview.quality_hint")).classes("text-[9px] text-gray-500 truncate min-w-0")
        ui.button(
            tr("preview.open_artwork_studio"),
            on_click=lambda: show_extension("artwork_studio"),
        ).props("flat dense size=sm").classes("text-[9px] shrink-0")


def _progress_banner() -> None:
    registry.preview_status_label = ui.label("").classes(
        "text-[11px] font-bold shrink-0 w-full min-w-0 truncate"
    )
    update_preview_status_label()


def _preview_has_artifact() -> bool:
    if state.mutation_in_progress:
        return True
    if state.workflow_active:
        pending = (state.pending_output_type or state.live_workspace_output_type or "").strip().lower()
        if pending and pending != "chat":
            return True
    if state.artifact_ready and state.last_generated_file_path:
        return True
    draft = draft_sync.get_draft()
    return bool(draft and draft.strip())


def build_preview_workspace_panel() -> None:
    draft = draft_sync.get_draft()
    char_count = len(draft)
    busy = state.mutation_in_progress
    has_content = _preview_has_artifact()
    output_type = _classified_output_type()
    view_mode = normalize_preview_view_mode(state.preview_view_mode)
    if output_type == "sound" or not supports_viewer_mode(output_type):
        view_mode = "source"
        state.preview_view_mode = "source"
        use_viewer = False
    else:
        use_viewer = view_mode == "viewer" and _preview_allows_viewer(output_type)
    registry.preview_char_label = None
    registry.preview_view_mode_btn = None
    registry.preview_markup_holder = None
    registry.preview_hint_label = None

    def on_editor_change(e):
        val = draft_sync._as_text(getattr(e, "args", e))
        state.live_workspace_plain = val
        state.draft_content = val
        state.preview_dirty = True

    with ui.column().classes(
        "w-full h-full min-h-0 min-w-0 gap-1 flex flex-col overflow-x-hidden loma-preview-root"
    ):
        with ui.row().classes("w-full items-center justify-between shrink-0 gap-2 min-w-0"):
            ui.label(tr("preview.live_draft")).classes(
                "text-[9px] font-bold text-cyan-400 tracking-widest shrink-0"
            )
            if has_content and not busy:
                registry.preview_char_label = ui.label(
                    tr("preview.chars", count=f"{char_count:,}")
                ).classes("text-[9px] text-gray-500 shrink-0")

        _progress_banner()

        toolbar_row = ui.row().classes(
            "w-full items-center gap-1 shrink-0 flex-nowrap min-w-0 overflow-hidden"
        )
        with toolbar_row:
            if has_content:
                ui.label(tr("preview.format_label", name=expected_artifact_basename(output_type))).classes(
                    "text-[9px] text-gray-500 flex-1 min-w-0 truncate"
                )
            else:
                ui.element("div").classes("flex-1 min-w-0")

            if has_content and _preview_supports_source_viewer(output_type):

                def set_preview_view_mode(mode: str) -> None:
                    state.preview_view_mode = normalize_preview_view_mode(mode)
                    refresh_preview_panel()

                build_source_viewer_pills(
                    toolbar_row,
                    mode=view_mode,
                    on_change=set_preview_view_mode,
                    show_viewer=_preview_allows_viewer(output_type),
                )
                registry.preview_view_mode_btn = None

            def reload_from_draft_store():
                from services.session.reset import reset_workspace_session

                reset_workspace_session(execution_mode="direct")
                try:
                    from ui.components.chat_message import render_chat

                    render_chat.refresh()
                except Exception:
                    pass
                ui.notify(tr("preview.workspace_reset"), color="info")

            ui.button(tr("preview.reload"), icon="refresh", on_click=reload_from_draft_store).props("flat dense").classes(
                "text-[10px] text-gray-400 shrink-0"
            )

            async def save_from_preview():
                from services.session.artifact import save_preview_to_artifact

                if busy:
                    ui.notify(tr("preview.wait_processing"), color="warning")
                    return
                if _is_source_mode():
                    draft_sync.sync_editor_to_state()
                original = (
                    state.active_context_files[0].get("filename", "output")
                    if state.active_context_files
                    else "output"
                )
                path = save_preview_to_artifact(
                    output_type=_classified_output_type(), original_filename=original
                )
                if path:
                    state.preview_dirty = False
                    ui.notify(
                        tr("preview.saved_to", name=os.path.basename(path)),
                        color="positive",
                    )
                    update_preview_status_label(_classified_output_type())
                else:
                    ui.notify(tr("preview.save_failed"), color="negative")

            async def download_from_preview():
                from services.session.artifact import (
                    export_ready_artifact,
                    ready_artifact_path,
                    request_fingerprint,
                    save_preview_to_artifact,
                )

                if busy:
                    ui.notify(tr("preview.wait_processing"), color="warning")
                    return
                if _is_source_mode():
                    draft_sync.sync_editor_to_state()
                original = (
                    state.active_context_files[0].get("filename", "output")
                    if state.active_context_files
                    else "output"
                )
                out_type = _classified_output_type()
                # Fingerprint this request the same way compile_artifact does, so a
                # cached last_generated_file_path only gets handed back here when it
                # truly matches what this Download press would generate — not just
                # because something happens to be sitting there from an earlier,
                # unrelated request in this session.
                fp = request_fingerprint(
                    state.live_workspace_mode or "generation",
                    out_type,
                    original,
                    instruction=draft_sync.get_last_user_instruction(
                        state.messages, state.last_user_instruction
                    ),
                    content=draft_sync.get_draft_for_export(),
                )
                path = None
                # Binary media: export existing file even if prompt draft is dirty.
                if state.artifact_ready and (
                    out_type in ("image", "sound", "video") or not state.preview_dirty
                ):
                    path = export_ready_artifact(
                        out_type, original, log_fn=state.add_log, expected_fingerprint=fp
                    ) or ready_artifact_path(fp)
                if not path:
                    path = ready_artifact_path(fp) if out_type in ("image", "sound", "video") else None
                if not path:
                    path = save_preview_to_artifact(
                        output_type=out_type, original_filename=original
                    )
                if path and os.path.exists(path):
                    state.preview_dirty = False
                    ui.download(path)
                    ui.notify(tr("preview.downloading", name=os.path.basename(path)), color="positive")
                    update_preview_status_label(out_type)
                else:
                    ui.notify(tr("preview.nothing_download"), color="warning")

            save_btn = ui.button(tr("preview.save"), icon="save", on_click=save_from_preview).props("flat dense").classes(
                "text-[10px] shrink-0"
            )
            download_btn = ui.button(tr("preview.download"), icon="download", on_click=download_from_preview).props(
                "flat dense color=positive"
            ).classes("text-[10px] font-bold shrink-0")
            if busy:
                save_btn.disable()
                download_btn.disable()

        with ui.column().classes(
            "w-full flex-1 min-h-0 min-w-0 flex flex-col gap-0 overflow-hidden "
            "overflow-x-hidden loma-preview-content-column"
        ):
            if has_content:
                artifact_path = _artifact_file_path()
                image_src = (
                    _preview_image_src(artifact_path)
                    if artifact_path
                    and artifact_path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".bmp"))
                    else None
                )
                is_image_preview = output_type == "image" and bool(image_src)

                async def on_viewer_mouseup(_=None):
                    if busy:
                        return
                    highlight = await read_dom_selection()
                    if not highlight:
                        return
                    state.preview_selection = highlight.strip()
                    state.preview_sel_start = -1
                    state.preview_sel_end = -1
                    open_revision_prompt(revise_allowed=False)

                async def on_editor_mouseup(_=None):
                    if busy:
                        return
                    captured = await capture_selection_from_editor()
                    if captured:
                        open_revision_prompt(revise_allowed=True)

                if is_image_preview:
                    # Explicitly sized frame — Quasar q-img collapses to 0 height without it.
                    with ui.element("div").classes(
                        "w-full flex-1 min-h-[200px] min-w-0 relative overflow-hidden "
                        "rounded-lg bg-black/20 loma-preview-image-frame"
                    ):
                        ui.image(image_src).props("fit=contain").classes(
                            "absolute inset-0 w-full h-full"
                        )
                    _render_image_quality_buttons()
                    ui.label(tr("preview.image_prompt_label")).classes(
                        "text-[9px] font-bold text-cyan-400 tracking-widest shrink-0 mt-1"
                    )
                    with ui.element("div").classes(
                        "w-full h-[100px] max-h-[26%] min-h-[64px] min-w-0 shrink-0 "
                        "overflow-hidden overflow-x-hidden flex flex-col loma-preview-editor-wrap"
                    ):
                        build_preview_scroll_pane(
                            use_viewer=False,
                            draft=draft,
                            viewer_html="",
                            on_editor_change=on_editor_change,
                            on_editor_mouseup=on_editor_mouseup,
                            on_viewer_mouseup=on_viewer_mouseup,
                            output_type=output_type,
                        )
                else:
                    if _is_binary_preview_type(output_type) and artifact_path:
                        with ui.row().classes(
                            "w-full items-center gap-2 p-2 rounded-lg bg-black/30 border border-white/10 shrink-0"
                        ):
                            icon = "audiotrack" if output_type == "sound" else "movie"
                            ui.icon(icon, size="sm").classes("text-cyan-400")
                            ui.label(os.path.basename(artifact_path)).classes(
                                "text-[10px] text-gray-400 truncate flex-1"
                            )

                    with ui.element("div").classes(
                        "w-full flex-1 min-h-0 overflow-hidden flex flex-col loma-preview-editor-wrap"
                    ):
                        build_preview_scroll_pane(
                            use_viewer=use_viewer,
                            draft=draft,
                            viewer_html=_resolve_preview_viewer_html(),
                            on_editor_change=on_editor_change,
                            on_editor_mouseup=on_editor_mouseup,
                            on_viewer_mouseup=on_viewer_mouseup,
                            output_type=output_type,
                        )

                hint = (
                    tr("preview.mutation_hint")
                    if busy
                    else mode_hint(view_mode, editable=True, output_type=output_type)
                )
                registry.preview_hint_label = ui.label(hint).classes(
                    "text-[10px] text-gray-500 shrink-0 leading-tight pt-1 pb-0 w-full "
                    "min-w-0 truncate loma-preview-hint"
                )
            else:
                registry.preview_editor = None
                registry.preview_markup_holder = None
                registry.preview_hint_label = None
                with ui.column().classes(
                    "w-full flex-1 items-center justify-center gap-2 p-6 "
                    "border border-dashed border-white/10 rounded-lg bg-black/20 min-h-[160px]"
                ):
                    ui.icon("description", size="2rem").classes("text-gray-600")
                    ui.label(tr("preview.nothing_title")).classes("text-sm font-semibold text-gray-400")
                    ui.label(tr("preview.empty_blurb")).classes("text-[11px] text-gray-500 text-center leading-relaxed max-w-md")

    if has_content and state.workflow_active:
        from ui.themes.assets import schedule_scroll_preview

        schedule_scroll_preview()
