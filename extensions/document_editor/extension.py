# -*- coding: utf-8 -*-

"""Document editor — view, edit, export, and highlight .docx / .pdf for LOMA."""

from __future__ import annotations



import os



from nicegui import background_tasks, helpers, ui



from services.source_parser import parse_file

from pipeline.base.base_extension import BaseExtension

from pipeline.i18n import t as tr

from services.renderer import preview_html_from_draft

from extensions.document_editor.controls import (
    POLICY_LITE_OR_PRO,
    release_lite_lock,
    set_execution_policy,
)

from extensions.document_editor.highlight_runner import start_document_highlight_workflow

from ui.components.markup_viewer import (
    MARKDOWN_SOURCE_INNER_CLASSES,
    build_markdown_source_pane,
    build_markup_pane,
)

from ui.components.source_viewer_mode import (
    is_viewer_mode,
    normalize_source_viewer_mode,
)

from ui.components.viewer_selection import open_highlight_dialog, read_dom_selection



_UNDO_MAX = 10

_doc_state = {
    "parsed": None,
    "view_mode": "viewer",
    "edit_mode": False,
    "filename": "",
    "filepath": "",
    "editor": None,
    "_autosave_timer": None,
    "baseline": None,
    "undo_stack": [],
    "editor_font_px": 12,
}
_EDITOR_FONT_MIN = 9
_EDITOR_FONT_MAX = 24


def _push_undo_snapshot() -> None:
    """Save current body before a change (max 10 undo levels)."""
    body = _document_body_text()
    stack: list[str] = _doc_state.setdefault("undo_stack", [])
    if stack and stack[-1] == body:
        return
    stack.append(body)
    if len(stack) > _UNDO_MAX:
        _doc_state["undo_stack"] = stack[-_UNDO_MAX:]


def _undo_last_change() -> None:
    stack: list[str] = _doc_state.get("undo_stack") or []
    if not stack:
        ui.notify(tr("document_editor.nothing_undo"), color="warning")
        return
    restored = stack.pop()
    parsed = _doc_state.get("parsed")
    if not parsed:
        ui.notify(tr("document_editor.nothing_undo"), color="warning")
        return
    parsed["content"] = restored
    editor = _doc_state.get("editor")
    if editor is not None:
        editor.value = restored
        editor.update()
    path = (_doc_state.get("filepath") or "").strip()
    if path.lower().endswith(".docx"):
        from extensions.document_editor.persist import persist_docx_state

        try:
            persist_docx_state(path, parsed, restored)
        except Exception as ex:
            ui.notify(tr("document_editor.undo_save_failed", error=ex), color="negative")
            return
    ui.notify(tr("document_editor.undid"), color="positive")


def _coerce_ui_value(value) -> str:
    if isinstance(value, str):
        return value
    args = getattr(value, "args", None)
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        return str(args.get("value") or args.get("content") or "")
    return ""


def _document_body_text() -> str:
    raw = (_doc_state.get("parsed") or {}).get("content")
    if isinstance(raw, str):
        return raw
    _flush_editor_to_parsed()
    raw = (_doc_state.get("parsed") or {}).get("content")
    return raw if isinstance(raw, str) else ""


def _flush_editor_to_parsed() -> None:
    ed = _doc_state.get("editor")
    if ed is not None and _doc_state.get("parsed"):
        _doc_state["parsed"]["content"] = _coerce_ui_value(ed.value)


def _save_docx_if_needed(*, quiet: bool = False) -> None:
    path = (_doc_state.get("filepath") or "").strip()
    if not path:
        return
    _flush_editor_to_parsed()
    ext = path.lower()
    if ext.endswith(".txt"):
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(_document_body_text())
            _doc_state["_undo_edit_snapshotted"] = False
            if not quiet:
                ui.notify(tr("document_editor.saved", name=os.path.basename(path)), color="positive")
        except Exception as ex:
            ui.notify(tr("document_editor.save_failed", error=ex), color="negative")
        return
    if not ext.endswith(".docx"):
        return
    from extensions.document_editor.persist import persist_docx_state

    try:
        persist_docx_state(path, _doc_state.get("parsed"))
        _doc_state["_undo_edit_snapshotted"] = False
        if not quiet:
            ui.notify(tr("document_editor.saved", name=os.path.basename(path)), color="positive")
    except Exception as ex:
        ui.notify(tr("document_editor.save_failed", error=ex), color="negative")


def _schedule_autosave() -> None:
    timer = _doc_state.get("_autosave_timer")
    if timer is not None:
        try:
            timer.active = False
        except Exception:
            pass
    _doc_state["_autosave_timer"] = ui.timer(0.6, lambda: _save_docx_if_needed(quiet=True), once=True)


def _export_document() -> None:
    path = (_doc_state.get("filepath") or "").strip()
    if not path or not os.path.isfile(path):
        ui.notify(tr("document_editor.no_document"), color="warning")
        return
    if _doc_state.get("edit_mode"):
        _save_docx_if_needed(quiet=True)
    from extensions.document_editor.persist import export_to_downloads

    try:
        dest = export_to_downloads(path)
        ui.notify(tr("document_editor.exported", name=os.path.basename(dest)), color="positive")
    except Exception as ex:
        ui.notify(tr("document_editor.export_failed", error=ex), color="negative")





def _sync_source_scroll_mode() -> None:
    area = _doc_state.get("content_area")
    if area is None:
        return
    use_outer = (
        not is_viewer_mode(_doc_state.get("view_mode") or "viewer")
        and not _doc_state.get("edit_mode")
    )
    if use_outer:
        area.classes(
            add="overflow-y-auto loma-scroll loma-doc-source-outer-scroll",
            remove="overflow-hidden",
        )
    else:
        area.classes(
            remove="overflow-y-auto loma-scroll loma-doc-source-outer-scroll",
            add="overflow-hidden",
        )


def _render_document_content(container) -> None:
    container.clear()
    _doc_state["editor"] = None
    _doc_state["content_el"] = None
    parsed = _doc_state["parsed"]
    if not parsed or parsed.get("type") != "text":
        return
    path = _doc_state.get("filepath") or ""
    ext = os.path.splitext(path)[1].lower()
    text = _document_body_text()
    font_px = _doc_state.get("editor_font_px", 12)

    if is_viewer_mode(_doc_state["view_mode"]):
        if ext == ".pdf" and path and os.path.isfile(path):
            from services.renderer import pdf_to_markup_html

            _doc_state["content_el"] = build_markup_pane(
                container, pdf_to_markup_html(path), font_px=font_px
            )
        else:
            _doc_state["content_el"] = build_markup_pane(
                container, preview_html_from_draft(text, "document"), font_px=font_px
            )
        _sync_source_scroll_mode()
        return

    if ext == ".pdf" and path and os.path.isfile(path):
        from services.renderer import pdf_to_plain_text

        _doc_state["content_el"] = build_markdown_source_pane(
            container, pdf_to_plain_text(path), editable=False, font_px=font_px
        )
        _sync_source_scroll_mode()
        return

    editable = _doc_state.get("edit_mode") and ext in (".docx", ".txt")

    def _on_source_edit(value) -> None:
        if _doc_state.get("edit_mode") and not _doc_state.get("_undo_edit_snapshotted"):
            _push_undo_snapshot()
            _doc_state["_undo_edit_snapshotted"] = True
        if _doc_state.get("parsed"):
            _doc_state["parsed"]["content"] = _coerce_ui_value(value)
        if _doc_state.get("edit_mode"):
            _schedule_autosave()

    _doc_state["editor"] = build_markdown_source_pane(
        container,
        text,
        editable=editable,
        on_change=_on_source_edit if editable else None,
        fill_height=editable,
        font_px=font_px,
    )
    _doc_state["content_el"] = _doc_state["editor"]
    _sync_source_scroll_mode()





def _revert_to_baseline() -> None:
    _undo_last_change()


def build_document_viewer_panel() -> None:
    from ui.themes import tokens

    t = tokens.get_theme()

    content_holder: dict = {"column": None}

    content_area_holder: dict = {"area": None}

    hint_label_holder: dict = {"label": None}

    name_input_holder: dict = {"label": None}
    drop_hint_holder: dict = {"label": None}
    empty_prompt_holder: dict = {"row": None}
    dropzone_wrap_holder: dict = {"el": None}
    file_chip_wrap_holder: dict = {"el": None}



    async def on_content_mouseup(_=None) -> None:

        highlight = await read_dom_selection()

        if not highlight:

            return

        fname = _doc_state["filename"] or "document"



        def on_query(sel: str, instruction: str) -> None:

            source_part = fname or "document"

            query = (

                f"{tr('chat.excerpt_prefix', source=source_part)}\n\n"

                f'"{sel.strip()}"\n\n'

                f"{instruction.strip()}"

            )

            from services.session import state



            state.messages.append({"role": "user", "content": query})

            state.add_log(f"Selection query sent to workspace ({len(sel.strip())} chars).")

            ui_mod = state.get_ui_module()

            if hasattr(ui_mod, "render_chat") and hasattr(ui_mod.render_chat, "refresh"):

                ui_mod.render_chat.refresh()

            from ui.themes.assets import schedule_scroll_chat



            schedule_scroll_chat()

            start_document_highlight_workflow(query, instruction)

        ext = os.path.splitext(_doc_state.get("filepath") or "")[1].lower()
        can_revise = bool(_doc_state.get("edit_mode") and ext == ".docx")

        def on_revise(sel: str, instruction: str) -> None:
            def _done() -> None:
                col = content_holder["column"]
                if col:
                    _render_document_content(col)

            from extensions.document_editor.revision import start_document_revision

            ui.notify(tr("document_editor.revising"), color="info")
            start_document_revision(_doc_state, sel, instruction, on_complete=_done)

        open_highlight_dialog(
            tr("document_editor.query_highlighted"),
            highlight,
            on_query=on_query,
            source_label=f"doc_{fname}",
            revise_allowed=can_revise,
            on_revise=on_revise if can_revise else None,
        )



    def _set_filename_label(name: str) -> None:

        lbl = name_input_holder["label"]
        hint = drop_hint_holder["label"]
        has_name = bool((name or "").strip())

        if lbl is not None:
            lbl.set_text(name or "")
            lbl.update()

        dropzone = dropzone_wrap_holder["el"]
        chip = file_chip_wrap_holder["el"]
        if dropzone is not None:
            dropzone.set_visibility(not has_name)
        if chip is not None:
            chip.set_visibility(has_name)

        if hint is not None:
            hint.set_visibility(not has_name)

        empty_row = empty_prompt_holder["row"]
        if empty_row is not None:
            empty_row.set_visibility(not has_name)



    def _update_mode_hint() -> None:

        lbl = hint_label_holder["label"]

        if lbl is None:

            return

        lbl.set_visibility(False)



    def _set_view_mode(mode: str) -> None:

        if not _doc_state["parsed"]:

            return

        _flush_editor_to_parsed()
        if _doc_state.get("edit_mode"):
            _save_docx_if_needed(quiet=True)

        mode = normalize_source_viewer_mode(mode)
        if _doc_state.get("edit_mode") and is_viewer_mode(mode):
            _doc_state["edit_mode"] = False
            if view_edit_switch is not None:
                view_edit_switch.value = True
                view_mode_lbl.set_text(tr("document_editor.view"))
                view_mode_lbl.classes(replace="text-[11px] text-blue-400")

        _doc_state["view_mode"] = mode

        col = content_holder["column"]

        if col:

            _render_document_content(col)

        _update_mode_hint()



    def load_file_path(path: str, *, edit_on_load: bool = False) -> None:

        if not path or not os.path.isfile(path):

            ui.notify(tr("document_editor.file_not_found"), color="negative")

            return

        parsed_source = parse_file(path)
        parsed = parsed_source.legacy_upload_dict()

        if not parsed or parsed.get("type") in ("error", "unsupported"):

            reason = (parsed or {}).get("content", "Could not parse file.")

            ui.notify(str(reason)[:120], color="negative")

            return

        _doc_state["parsed"] = parsed

        _doc_state["filename"] = os.path.basename(path)

        _doc_state["filepath"] = path
        _doc_state["baseline"] = {
            "content": (parsed.get("content") or ""),
            "filepath": path,
        }
        _doc_state["undo_stack"] = []
        _doc_state["_undo_edit_snapshotted"] = False

        ext = os.path.splitext(path)[1].lower()
        if edit_on_load and ext in (".docx", ".txt"):
            _doc_state["edit_mode"] = True
            _doc_state["view_mode"] = "source"
        else:
            _doc_state["view_mode"] = "viewer"
            _doc_state["edit_mode"] = False
        if view_edit_switch is not None:
            view_edit_switch.value = not _doc_state["edit_mode"]
            if ext == ".pdf":
                view_edit_switch.props("disable")
            else:
                view_edit_switch.props(remove="disable")

        _set_filename_label(_doc_state["filename"])

        area = content_area_holder["area"]

        if area:

            area.set_visibility(True)

        col = content_holder["column"]

        if col:

            _render_document_content(col)

        _update_mode_hint()

        ui.notify(tr("document_editor.loaded", name=_doc_state['filename']), color="positive")



    with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2 overflow-hidden"):
        upload_holder: dict = {"uploader": None}

        with ui.row().classes("w-full items-center justify-between gap-2 shrink-0 flex-nowrap"):
            with ui.row().classes("items-center gap-2 flex-wrap min-w-0"):
                ui.label(tr("document_editor.mode")).classes("text-[10px] text-gray-500 uppercase tracking-wide")
                view_edit_switch = ui.switch(
                    value=True,
                ).props("dense").classes("text-[11px]")
                view_mode_lbl = ui.label(tr("document_editor.view")).classes("text-[11px] text-blue-400")

                mode_hint_lbl = ui.label(tr("document_editor.view_hint")).classes("text-xs text-gray-400")

                def _on_view_edit_change() -> None:
                    view_on = bool(view_edit_switch.value)
                    view_mode_lbl.set_text(
                        tr("document_editor.view") if view_on else tr("document_editor.edit")
                    )
                    view_mode_lbl.classes(
                        replace="text-[11px] "
                        + ("text-blue-400" if view_on else "text-amber-400")
                    )
                    mode_hint_lbl.set_text(
                        tr("document_editor.view_hint") if view_on else tr("document_editor.edit_hint")
                    )
                    path = _doc_state.get("filepath") or ""
                    ext = os.path.splitext(path)[1].lower()
                    if not view_on and ext == ".pdf":
                        view_edit_switch.value = True
                        ui.notify(tr("document_editor.pdf_no_edit"), color="warning")
                        return
                    if view_on and _doc_state.get("edit_mode"):
                        _save_docx_if_needed(quiet=True)
                    _doc_state["edit_mode"] = not view_on
                    _set_view_mode("viewer" if view_on else "source")
                    ui.notify(
                        tr("document_editor.view_mode_notify")
                        if view_on
                        else tr("document_editor.edit_mode_notify"),
                        color="info",
                    )

                view_edit_switch.on("update:model-value", lambda _: _on_view_edit_change())
                set_execution_policy(POLICY_LITE_OR_PRO)
                release_lite_lock()

            with ui.row().classes("items-center gap-1 shrink-0"):

                def _open_new_document_dialog() -> None:
                    from extensions.document_editor.new_document import (
                        create_blank_document,
                        office_word_available,
                    )
                    from services.platform_paths import generated_dir

                    word_ok = office_word_available()
                    fmt_opts = {"txt": tr("document_editor.fmt_txt"), "docx": tr("document_editor.fmt_docx")}
                    default_fmt = "docx"

                    with ui.dialog() as dlg, ui.card().classes("p-4 gap-3 min-w-[300px]"):
                        ui.label(tr("document_editor.new_document")).classes("text-sm font-semibold")
                        name_in = ui.input(tr("document_editor.doc_name"), value="untitled").props(
                            "dense dark standout autofocus"
                        ).classes("w-full")
                        fmt_sel = ui.select(
                            options=fmt_opts,
                            value=default_fmt,
                            label=tr("document_editor.format"),
                        ).props("dense dark standout").classes("w-full")
                        if not word_ok:
                            ui.label(tr("document_editor.blank_docx_hint")).classes("text-[11px] text-gray-500")

                        def _create() -> None:
                            name = (name_in.value or "untitled").strip()
                            as_docx = fmt_sel.value == "docx"
                            out_dir = generated_dir()
                            try:
                                path = create_blank_document(out_dir, name, as_docx=as_docx)
                                dlg.close()
                                load_file_path(path, edit_on_load=True)
                                ui.notify(tr("document_editor.created", name=os.path.basename(path)), color="positive")
                            except Exception as exc:
                                ui.notify(str(exc)[:200], color="negative")

                        with ui.row().classes("w-full justify-end gap-2"):
                            ui.button(tr("document_editor.cancel"), on_click=dlg.close).props("flat dense")
                            ui.button(tr("document_editor.create"), on_click=_create).props("flat dense color=primary")
                    dlg.open()

                def _revert_btn() -> None:
                    def _run() -> None:
                        _revert_to_baseline()
                        col = content_holder["column"]
                        if col:
                            _render_document_content(col)

                    ui.button(icon="undo", on_click=_run).props("flat round dense size=sm").classes(
                        "text-gray-400 opacity-80 hover:opacity-100 hover:text-amber-300 shrink-0"
                    ).tooltip(tr("document_editor.undo_tooltip"))

                _revert_btn()

                ui.button(icon="note_add", on_click=_open_new_document_dialog).props(
                    "flat round dense size=sm"
                ).classes(
                    "text-gray-400 opacity-80 hover:opacity-100 hover:text-emerald-300 shrink-0"
                ).tooltip(tr("document_editor.new_doc_tooltip"))

                dict_recording = {"active": False}

                async def _toggle_dictate() -> None:
                    from ui.components.loma_notify import notify
                    from ui.components.voice_controls import (
                        dictate_with_live_preview,
                        stop_dictation,
                    )

                    if dict_recording["active"]:
                        dictate_btn.props("icon=hourglass_top color=orange")
                        notify(tr("voice.transcribing"), color="info")
                        await stop_dictation()
                        return

                    async def _start_dictate() -> None:
                        dict_recording["active"] = True
                        dictate_btn.props("icon=stop color=red")
                        notify(tr("voice.recording_hint"), color="info", timeout=8000)
                        result = await dictate_with_live_preview()
                        dict_recording["active"] = False
                        dictate_btn.props("icon=mic color=default")
                        if not result.get("ok"):
                            err = (result.get("error") or "").strip()
                            if err == "too-short":
                                notify(tr("voice.too_short"), color="warning")
                            elif err and (
                                "whisper" in err.lower()
                                or "funasr" in err.lower()
                                or "sensevoice" in err.lower()
                            ):
                                from services.voice_input import sensevoice_ready

                                if not sensevoice_ready():
                                    from ui.components.voice_input_installer import open_voice_input_chooser

                                    open_voice_input_chooser(on_ready=lambda: None)
                                notify(err[:240], color="warning", multi_line=True)
                            elif err and err not in ("no-speech", "aborted"):
                                notify(tr("voice.input_failed"), color="warning")
                            return
                        text = (result.get("text") or "").strip()
                        full = (result.get("full") or "").strip()
                        if not text and not full:
                            notify(tr("voice.no_speech"), color="warning")
                            return
                        editor = _doc_state.get("editor")
                        if editor is not None:
                            if full:
                                editor.value = full
                            else:
                                cur = _coerce_ui_value(editor.value)
                                sep = " " if cur and not cur.endswith((" ", "\n")) else ""
                                editor.value = f"{cur}{sep}{text}"
                            editor.update()
                            if _doc_state.get("parsed"):
                                _doc_state["parsed"]["content"] = editor.value
                            if _doc_state.get("edit_mode"):
                                _schedule_autosave()
                        elif _doc_state.get("parsed"):
                            cur = (_doc_state["parsed"].get("content") or "").strip()
                            sep = "\n\n" if cur else ""
                            _doc_state["parsed"]["content"] = f"{cur}{sep}{text}"
                            col = content_holder["column"]
                            if col:
                                _render_document_content(col)
                        ui.notify(tr("document_editor.dictation_inserted"), color="positive")

                    def _kick() -> None:
                        # NiceGUI's slot stack is keyed by id(asyncio.current_task()),
                        # so a fresh task always starts with an empty stack regardless
                        # of how it's created — any ui.* call inside _start_dictate
                        # needs the slot re-entered explicitly via the captured client
                        # (same fix as ui/components/voice_controls.py's mic button).
                        client = ui.context.client
                        background_tasks.create(
                            helpers.await_with_context(_start_dictate(), client), name="dictate-start"
                        )

                    from ui.components.voice_input_installer import ensure_voice_before_record

                    ensure_voice_before_record(on_ready=_kick)

                dictate_btn = ui.button(icon="mic", on_click=_toggle_dictate).props(
                    "flat round dense size=sm"
                ).classes(
                    "text-gray-400 opacity-80 hover:opacity-100 hover:text-sky-300 shrink-0"
                ).tooltip(tr("document_editor.dictate_tooltip"))

                def _export_btn() -> None:
                    ui.button(icon="download", on_click=_export_document).props(
                        "flat round dense size=sm"
                    ).classes(
                        "text-blue-400 opacity-80 hover:opacity-100 shrink-0"
                    ).tooltip(tr("document_editor.export_tooltip"))

                _export_btn()

                def _apply_editor_font_size() -> None:
                    px = _doc_state.get("editor_font_px", 12)
                    el = _doc_state.get("content_el")
                    if el is None:
                        return
                    if is_viewer_mode(_doc_state.get("view_mode") or "viewer"):
                        el.style(f"--loma-view-font-size: {px}px;")
                    else:
                        el.style(f"font-size: {px}px;")

                def _bump_font_size(delta: int) -> None:
                    px = max(
                        _EDITOR_FONT_MIN,
                        min(_EDITOR_FONT_MAX, _doc_state.get("editor_font_px", 12) + delta),
                    )
                    _doc_state["editor_font_px"] = px
                    _apply_editor_font_size()

                ui.button(icon="text_decrease", on_click=lambda: _bump_font_size(-1)).props(
                    "flat round dense size=sm"
                ).classes(
                    "text-gray-400 opacity-80 hover:opacity-100 shrink-0"
                ).tooltip(tr("document_editor.font_decrease_tooltip"))

                ui.button(icon="text_increase", on_click=lambda: _bump_font_size(1)).props(
                    "flat round dense size=sm"
                ).classes(
                    "text-gray-400 opacity-80 hover:opacity-100 shrink-0"
                ).tooltip(tr("document_editor.font_increase_tooltip"))

        async def on_upload(e) -> None:
            try:
                if not hasattr(e, "file"):
                    return
                name = e.file.name
                data = await e.file.read()
                ext = os.path.splitext(name)[1].lower()
                if ext not in (".docx", ".pdf"):
                    ui.notify(tr("document_editor.only_docx_pdf"), color="warning")
                    return
                upload_dir = os.path.join("data", "uploads", "document_editor")
                os.makedirs(upload_dir, exist_ok=True)
                dest = os.path.join(upload_dir, name)
                with open(dest, "wb") as f:
                    f.write(data if isinstance(data, bytes) else bytes(str(data), "utf-8"))
                load_file_path(dest)
            except Exception as ex:
                ui.notify(tr("document_editor.upload_failed", error=ex), color="negative")

        from pipeline.i18n import is_cjk_locale
        from ui.themes import tokens as theme_tokens

        dz = theme_tokens.get_theme()
        _SOURCE_CHIP = (
            "w-full items-center justify-between gap-2 flex-nowrap group "
            "rounded-lg px-3 py-2 min-h-[36px]"
        )

        def pick_document() -> None:
            upl = upload_holder.get("uploader")
            if upl is not None:
                upl.run_method("pickFiles")

        with ui.row().classes("w-full items-center gap-2 flex-nowrap shrink-0"):
            with ui.row().classes(
                f"flex-1 min-w-0 {_SOURCE_CHIP} {dz['file_card']}"
            ) as file_chip_wrap:
                file_chip_wrap.set_visibility(False)
                file_chip_wrap_holder["el"] = file_chip_wrap
                with ui.row().classes("items-center gap-2 flex-1 min-w-0 flex-nowrap"):
                    ui.icon("description", size="18px").classes("text-blue-400 shrink-0")
                    name_input_holder["label"] = ui.label("").classes(
                        f"text-[12px] font-semibold truncate {dz['file_card_text']}"
                    )

            with ui.element("div").classes(
                "flex-1 relative overflow-hidden rounded-lg px-3 py-2 min-h-[48px] "
                "border border-dashed cursor-pointer "
                f"{dz['dropzone']}"
            ).on("click", pick_document) as dropzone_wrap:
                dropzone_wrap_holder["el"] = dropzone_wrap
                upload_holder["uploader"] = ui.upload(
                    on_upload=on_upload, auto_upload=True
                ).props('dark accept=".pdf,.docx" max-files=1').classes("hidden")
                ui.upload(on_upload=on_upload, auto_upload=True).props(
                    'dark accept=".pdf,.docx" max-files=1'
                ).classes("absolute inset-0 w-full h-full opacity-0 z-[5] cursor-pointer")
                with ui.row().classes("items-center gap-2 flex-1 min-w-0 flex-nowrap relative z-[1]"):
                    with ui.row().classes("items-center gap-2 flex-1 min-w-0") as empty_prompt:
                        empty_prompt_holder["row"] = empty_prompt
                        ui.icon("cloud_upload", size="18px").classes(f"{dz['dropzone_icon']} shrink-0")
                        drop_hint_holder["label"] = ui.label(tr("document_editor.drop_hint")).classes(
                            f"{'text-xs' if is_cjk_locale() else 'text-[11px]'} leading-snug "
                            f"{dz['dropzone_text']} flex-1"
                        )

            def clear_viewer() -> None:
                _doc_state["parsed"] = None
                _doc_state["filename"] = ""
                _doc_state["filepath"] = ""
                _doc_state["view_mode"] = "viewer"
                _doc_state["edit_mode"] = False
                _doc_state["editor"] = None
                _doc_state["content_el"] = None
                _doc_state["baseline"] = None
                _doc_state["content_area"] = content_area_holder.get("area")
                _set_filename_label("")
                if view_edit_switch is not None:
                    view_edit_switch.props(remove="disable")
                hint = hint_label_holder["label"]
                if hint:
                    hint.set_visibility(False)
                col = content_holder["column"]
                if col:
                    col.clear()
                    with col:
                        ui.label(tr("document_editor.upload_empty")).classes(
                            "text-[11px] text-gray-500 text-center w-full p-4"
                        )
                area = content_area_holder["area"]
                if area:
                    area.set_visibility(False)
                upl = upload_holder.get("uploader")
                if upl is not None:
                    try:
                        upl.reset()
                    except Exception:
                        pass

            ui.button(icon="close", on_click=clear_viewer).props("flat round dense size=sm").classes(
                f"{t['muted']} opacity-70 hover:text-red-400 shrink-0"
            ).tooltip(tr("document_editor.clear_tooltip"))

        with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-1"):
            with ui.column().classes(
                "w-full flex-1 min-h-0 flex flex-col gap-1 loma-doc-editor-root "
                f"bg-black/20 border border-white/30 rounded-lg p-3 select-text overflow-hidden"
            ) as content_area:
                content_area.set_visibility(False)
                content_area_holder["area"] = content_area
                _doc_state["content_area"] = content_area
                content_area.on("mouseup", on_content_mouseup)
                content_holder["column"] = ui.column().classes(
                    "w-full flex-1 min-h-0 h-full gap-0 overflow-hidden loma-doc-editor-body"
                )
                with content_holder["column"]:
                    ui.label(tr("document_editor.upload_empty")).classes(
                        "text-[11px] text-gray-500 text-center w-full p-4"
                    )

            hint_label_holder["label"] = ui.label("").classes(
                "text-[11px] text-gray-500 shrink-0 leading-tight w-full text-center px-1"
            )
            hint_label_holder["label"].set_visibility(False)

        ui.label(tr("document_editor.highlight_hint")).classes("text-xs text-gray-400 text-center w-full shrink-0 pt-1")





class DocumentEditorExtension(BaseExtension):

    extension_id = "document_editor"

    label = "Document Editor"

    def metadata(self) -> dict:
        base = super().metadata()
        base["description"] = (
            "View and edit .docx, read PDFs, export to Downloads, and highlight text for LOMA."
        )
        return base

    def mount(self, container) -> None:
        with container:
            build_document_viewer_panel()


# Backward-compatible alias
DocumentViewerExtension = DocumentEditorExtension

