# -*- coding: utf-8 -*-
import os

from nicegui import ui

from pipeline.base.base_extension import BaseExtension
from pipeline.i18n import t as tr
from services.session import state
from services.session import chat_archive
from ui.components.chat_message import render_chat
from ui.themes.assets import schedule_scroll_chat

_archive_root_label = "data/chats"
_archive_ui = {"path": "", "dragged": None, "search": ""}


def get_current_path() -> str:
    return chat_archive.normalize_folder_path(_archive_ui.get("path") or "")


def set_current_path(path: str) -> None:
    _archive_ui["path"] = chat_archive.normalize_folder_path(path)


def get_selected_folder() -> str:
    """Folder used when saving a new chat freeze."""
    return get_current_path()


def set_selected_folder(name: str) -> None:
    set_current_path(name)


def _extract_freeze_body(content: str) -> tuple[str, str]:
    """Return (source_type, body) from a LOMA/LOMA freeze export."""
    source_type = ""
    if "## METADATA" in content:
        for line in content.splitlines():
            if "Source Type:" in line:
                source_type = line.split("Source Type:", 1)[1].strip().strip("*").strip()
                break
    body = content
    if "## CONTENT" in content:
        body = content.split("## CONTENT", 1)[1].strip()
        if body.startswith("\n"):
            body = body.lstrip("\n")
    return source_type, body


def load_chat_history_from_file(filepath: str) -> list:
    """Parses LOMA exported markdown (.md) and YAML (.yaml) chat files into message dicts."""
    messages = []
    content = ""
    if not os.path.exists(filepath):
        return messages
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        if filepath.endswith(".md") and (
            "CHAT FREEZE EXPORT" in content or "## CONTENT" in content
        ):
            source_type, body = _extract_freeze_body(content)
            if "session summary" in source_type.lower():
                summary = body.strip()
                if summary:
                    return [{"role": "assistant", "content": summary}]
            content = body

        if filepath.endswith(".yaml"):
            if "raw_transcript:" in content:
                transcript_part = content.split("raw_transcript:", 1)[1]
                lines = transcript_part.split("\n")
                current_role = None
                current_content = []
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("User:"):
                        if current_role and current_content:
                            messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                        current_role = "user"
                        current_content = [stripped[5:].strip()]
                    elif stripped.startswith("LOMA:") or stripped.startswith("LOMA:") or stripped.startswith("Assistant:"):
                        if current_role and current_content:
                            messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                        current_role = "assistant"
                        if stripped.startswith("LOMA:"):
                            current_content = [stripped[5:].strip()]
                        elif stripped.startswith("LOMA:"):
                            current_content = [stripped[5:].strip()]
                        else:
                            current_content = [stripped[10:].strip()]
                    elif current_role:
                        line_content = line[2:] if line.startswith("  ") else line
                        current_content.append(line_content)
                if current_role and current_content:
                    messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
            else:
                lines = content.split("\n")
                current_role = None
                current_content = []
                for line in lines:
                    stripped = line.strip()
                    if stripped.startswith("User:"):
                        if current_role and current_content:
                            messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                        current_role = "user"
                        current_content = [stripped[5:].strip()]
                    elif stripped.startswith("LOMA:") or stripped.startswith("LOMA:") or stripped.startswith("Assistant:"):
                        if current_role and current_content:
                            messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                        current_role = "assistant"
                        current_content = [stripped.split(":", 1)[1].strip()]
                    elif current_role:
                        current_content.append(line)
                if current_role and current_content:
                    messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
        else:
            lines = content.split("\n")
            current_role = None
            current_content = []
            for line in lines:
                if line.startswith("### USER"):
                    if current_role and current_content:
                        messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                    current_role = "user"
                    current_content = []
                elif line.startswith("### ASSISTANT") or line.startswith("### LOMA") or line.startswith("### LOMA"):
                    if current_role and current_content:
                        messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
                    current_role = "assistant"
                    current_content = []
                elif line.startswith("#### LOMA reasoning"):
                    continue
                elif current_role and not line.startswith("#"):
                    current_content.append(line)
            if current_role and current_content:
                messages.append({"role": current_role, "content": "\n".join(current_content).strip()})
    except Exception as e:
        print(f"Error restoring chat history: {e}")

    if not messages and content.strip():
        messages.append({"role": "assistant", "content": content})
    return messages


def _apply_path_after_rename(old_rel: str, new_rel: str) -> None:
    current = get_current_path()
    if current == old_rel:
        set_current_path(new_rel)
    elif current.startswith(f"{old_rel}/"):
        set_current_path(f"{new_rel}{current[len(old_rel):]}")


def _open_folder_dialog(*, rename_from: str | None = None) -> None:
    title = tr("archive.edit_folder") if rename_from else tr("archive.new_folder")
    with ui.dialog() as dialog, ui.card().classes("min-w-[280px] gap-3 p-4"):
        ui.label(title).classes("text-sm font-bold text-purple-300")
        name_input = ui.input(tr("archive.folder_name"), value=os.path.basename(rename_from) if rename_from else "").props(
            "outlined dense dark autofocus"
        ).classes("w-full text-[12px]")

        def confirm() -> None:
            raw = (name_input.value or "").strip()
            if not raw:
                ui.notify(tr("archive.enter_folder_name"), color="warning")
                return
            try:
                if rename_from:
                    new_rel = chat_archive.rename_folder(rename_from, raw)
                    _apply_path_after_rename(rename_from, new_rel)
                    ui.notify(tr("archive.folder_renamed"), color="positive")
                else:
                    folder = chat_archive.create_folder(raw, get_current_path())
                    set_current_path(folder)
                    ui.notify(tr("archive.folder_created", name=os.path.basename(folder)), color="positive")
                dialog.close()
                render_chats_tab.refresh()
            except FileExistsError:
                ui.notify(tr("archive.folder_exists"), color="warning")
            except Exception as ex:
                ui.notify(str(ex), color="negative")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(tr("common.cancel"), on_click=dialog.close).props("flat dense")
            ui.button(tr("common.save"), on_click=confirm).props("dense color=primary")
        name_input.on("keydown.enter", confirm)
    dialog.open()


def _confirm_delete_chat(rel_name: str, fpath: str) -> None:
    meta = chat_archive.get_entry(rel_name)
    label = meta.get("title") or os.path.basename(rel_name) or rel_name
    with ui.dialog() as dialog, ui.card().classes("p-4 gap-3"):
        ui.label(tr("archive.delete_chat_title")).classes("text-sm font-bold text-red-300")
        ui.label(tr("archive.delete_chat_body", name=label)).classes(
            "text-xs text-gray-400"
        )

        def _do_delete() -> None:
            try:
                os.remove(fpath)
                chat_archive.remove_entry(rel_name)
                ui.notify(tr("archive.removed", name=label), color="warning")
                render_chats_tab.refresh()
            except Exception as ex:
                ui.notify(tr("archive.deletion_failed", error=ex), color="negative")
            dialog.close()

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(tr("common.cancel"), on_click=dialog.close).props("flat")
            ui.button(tr("common.delete"), on_click=_do_delete).props("color=negative")
        dialog.open()


def _confirm_delete_folder(rel_folder: str) -> None:
    label = os.path.basename(rel_folder) or rel_folder
    with ui.dialog() as dialog, ui.card().classes("min-w-[300px] gap-3 p-4"):
        ui.label(tr("archive.delete_folder_title")).classes("text-sm font-bold text-red-300")
        ui.label(tr("archive.delete_folder_body", name=label)).classes(
            "text-[11px] text-gray-400 leading-relaxed"
        )

        def confirm() -> None:
            try:
                chat_archive.delete_folder_tree(rel_folder)
                current = get_current_path()
                if current == rel_folder or current.startswith(f"{rel_folder}/"):
                    set_current_path(chat_archive.parent_folder_path(rel_folder))
                dialog.close()
                ui.notify(tr("archive.folder_deleted"), color="warning")
                render_chats_tab.refresh()
            except Exception as ex:
                ui.notify(tr("archive.delete_failed", error=ex), color="negative")

        with ui.row().classes("w-full justify-end gap-2"):
            ui.button(tr("common.cancel"), on_click=dialog.close).props("flat dense")
            ui.button(tr("common.delete"), on_click=confirm).props("dense color=negative")
    dialog.open()


def _drop_chat_on_folder(target_folder: str) -> None:
    rel = _archive_ui.get("dragged")
    _archive_ui["dragged"] = None
    if not rel:
        return
    try:
        chat_archive.move_chat_file(rel, target_folder)
        ui.notify(tr("archive.chat_moved"), color="positive")
        render_chats_tab.refresh()
    except Exception as ex:
        ui.notify(tr("archive.move_failed", error=ex), color="negative")


def _make_drop_target(element, target_folder: str) -> None:
    def highlight() -> None:
        element.classes(add="ring-1 ring-purple-400/50 bg-purple-500/10")

    def unhighlight() -> None:
        element.classes(remove="ring-1 ring-purple-400/50 bg-purple-500/10")

    def handle_drop() -> None:
        unhighlight()
        _drop_chat_on_folder(target_folder)

    element.on("dragover.prevent", highlight)
    element.on("dragleave", unhighlight)
    element.on("drop", handle_drop)


def _navigate_to(path: str) -> None:
    set_current_path(path)
    render_chats_tab.refresh()


def _go_up() -> None:
    _navigate_to(chat_archive.parent_folder_path(get_current_path()))


def _enter_folder(name: str) -> None:
    _navigate_to(chat_archive.join_folder_path(get_current_path(), name))


def _folder_entry_row(folder_name: str) -> None:
    target = chat_archive.join_folder_path(get_current_path(), folder_name)
    with ui.row().classes(
        "w-full items-center gap-1 px-2 py-2 rounded-xl border border-white/10 "
        "bg-black/25 hover:border-purple-500/40 transition-colors"
    ) as row:
        with ui.row().classes(
            "flex-1 items-center gap-2 min-w-0 cursor-pointer"
        ) as open_zone:
            ui.icon("folder", size="sm").classes("text-purple-400 shrink-0")
            ui.label(folder_name).classes("text-[12px] font-medium text-gray-200 truncate flex-1")
            # ui.icon("chevron_right", size="xs").classes("text-gray-500 shrink-0")
            open_zone.on("click", lambda _e, n=folder_name: _enter_folder(n))

        ui.button(
            icon="edit",
            on_click=lambda _e, rel=target: _open_folder_dialog(rename_from=rel),
        ).props("flat dense round size=sm").classes(
            "text-gray-400 hover:text-cyan-300 shrink-0"
        ).tooltip(tr("archive.edit_folder_tooltip"))

        ui.button(
            icon="delete_outline",
            on_click=lambda _e, rel=target: _confirm_delete_folder(rel),
        ).props("flat dense round size=sm").classes(
            "text-gray-400 hover:text-red-400 shrink-0"
        ).tooltip(tr("archive.delete_folder_tooltip"))
        
    _make_drop_target(row, target)


def _parent_entry_row() -> None:
    parent = chat_archive.parent_folder_path(get_current_path())
    with ui.row().classes(
        "w-full items-center gap-2 px-2.5 py-2 rounded-xl border border-dashed border-white/15 "
        "bg-black/15 hover:border-purple-500/30 cursor-pointer"
    ) as row:
        ui.icon("arrow_upward", size="sm").classes("text-gray-400 shrink-0")
        ui.label(tr("archive.up_parent")).classes("text-[11px] text-gray-400 flex-1")
        row.on("click", lambda: _go_up())
    _make_drop_target(row, parent)


def _render_chat_card(rel_name: str) -> None:
    fpath = str(chat_archive.chat_path(rel_name))
    meta = chat_archive.get_entry(rel_name)
    display_name = os.path.basename(rel_name)
    file_ok = os.path.exists(fpath)

    with ui.card().classes(
        "w-full p-2.5 bg-black/20 border border-white/5 rounded-xl gap-0.5 "
        "hover:border-purple-500/30 transition-colors group"
    ).props("draggable") as card:
        card.on("dragstart", lambda r=rel_name: _archive_ui.update({"dragged": r}))

        with ui.row().classes("w-full items-center gap-1 flex-nowrap"):
            title_input = ui.input(value=meta.get("title", display_name)).props(
                "dark dense borderless"
            ).classes("flex-1 min-w-0 text-[11px] font-semibold text-gray-100 px-0")

            def save_title(_e, filename=rel_name, field=title_input):
                chat_archive.update_title(filename, field.value or tr("archive.untitled"))
                ui.notify(tr("archive.title_saved"), color="positive")

            title_input.on("blur", save_title)

            def make_add_source(name=rel_name, path=fpath, ok=file_ok):
                if not ok:
                    ui.notify(tr("archive.file_missing"), color="negative")
                    return
                try:
                    with open(path, "r", encoding="utf-8") as sf:
                        s_content = sf.read()
                    display = chat_archive.source_display_name(name)
                    existing_names = [f.get("filename") for f in state.active_context_files]
                    if display not in existing_names:
                        if len(state.active_context_files) < 5:
                            state.active_context_files.append(
                                {
                                    "filename": display,
                                    "type": "text",
                                    "content": s_content,
                                    "_attached_turn": state.context_attach_turn,
                                }
                            )
                            ui.notify(tr("archive.added_sources"), color="positive")
                            from ui.components.attachments_hub import render_sources_hub

                            render_sources_hub.refresh()
                        else:
                            ui.notify(tr("archive.sources_limit"), color="warning")
                    else:
                        ui.notify(tr("archive.already_source"), color="warning")
                except Exception as ex:
                    ui.notify(tr("archive.sourcing_failed", error=str(ex)), color="negative")

            ui.button(icon="input", on_click=make_add_source).props("flat dense round").classes(
                "text-blue-400 hover:bg-blue-500/10 text-xs shrink-0"
            ).tooltip(tr("archive.add_source_tooltip"))

            def make_continue_chat(path=fpath, ok=file_ok):
                if not ok:
                    ui.notify(tr("archive.file_missing"), color="negative")
                    return
                parsed_msgs = load_chat_history_from_file(path)
                if parsed_msgs:
                    state.messages.clear()
                    state.messages.extend(parsed_msgs)
                    render_chat.refresh()
                    schedule_scroll_chat()
                    ui.notify(tr("archive.chat_restored"), color="positive")
                else:
                    ui.notify(tr("archive.parse_failed"), color="negative")

            ui.button(icon="forum", on_click=make_continue_chat).props("flat dense round").classes(
                "text-green-400 hover:bg-green-500/10 text-xs shrink-0"
            ).tooltip(tr("archive.continue_tooltip"))

            def make_delete_chat(name=rel_name, path=fpath, ok=file_ok):
                if not ok:
                    try:
                        chat_archive.remove_entry(name)
                        ui.notify(tr("archive.removed_missing"), color="warning")
                        render_chats_tab.refresh()
                    except Exception as ex:
                        ui.notify(tr("archive.delete_failed", error=ex), color="negative")
                    return
                _confirm_delete_chat(name, path)

            ui.button(icon="delete_outline", on_click=make_delete_chat).props("flat dense round").classes(
                "text-red-400 hover:bg-red-500/10 text-xs shrink-0"
            ).tooltip(tr("archive.delete_chat_tooltip"))

        created = meta.get("created_at") or ""
        if not file_ok:
            ui.label(tr("archive.file_missing_disk")).classes("text-[10px] text-red-400")
        elif created:
            ui.label(created).classes("text-[10px] text-gray-500")


def _breadcrumb_path() -> str:
    current = get_current_path()
    if not current:
        return _archive_root_label
    return f"{_archive_root_label}/{current}"


@ui.refreshable
def render_archive_file_list() -> None:
    from services.chat_archive_search import list_all_chat_files, search_chat_files

    current = get_current_path()
    query = (_archive_ui.get("search") or "").strip()
    try:
        subfolders, files = chat_archive.list_directory(current)
    except ValueError:
        set_current_path("")
        current = ""
        subfolders, files = chat_archive.list_directory("")

    if query:
        filtered_files = search_chat_files(list_all_chat_files(), query)
        subfolders = []
    else:
        filtered_files = files

    with ui.scroll_area().classes("w-full flex-1 min-h-0 loma-scroll pr-1"):
        with ui.column().classes("w-full gap-2 pb-4"):
            if current and not query:
                _parent_entry_row()
            for folder_name in subfolders:
                _folder_entry_row(folder_name)
            for rel_name in filtered_files:
                _render_chat_card(rel_name)
            if not subfolders and not filtered_files:
                ui.label(tr("archive.no_items")).classes(
                    "text-xs text-gray-500 italic p-4 text-center w-full"
                )
                if query:
                    ui.label(tr("archive.no_match", query=query)).classes(
                        "text-[11px] text-gray-600 italic px-2 text-center w-full"
                    )
                elif not current:
                    ui.label(tr("archive.empty_hint")).classes(
                        "text-xs text-gray-600 italic px-2 text-center w-full leading-relaxed"
                    )
                else:
                    ui.label(tr("archive.drag_hint")).classes(
                        "text-[11px] text-gray-600 italic px-2 text-center w-full"
                    )


@ui.refreshable
def render_chats_tab() -> None:
    chat_archive.CHAT_DIR.mkdir(parents=True, exist_ok=True)

    with ui.column().classes("w-full flex-1 min-h-0 flex flex-col gap-2"):
        with ui.row().classes("w-full items-center justify-between shrink-0 gap-1 flex-nowrap"):
            with ui.row().classes("items-center gap-0.5 shrink-0"):
                if get_current_path():
                    ui.button(icon="arrow_upward", on_click=_go_up).props("flat dense round").classes(
                        "text-gray-300"
                    ).tooltip(tr("archive.up_tooltip"))
                ui.button(icon="create_new_folder", on_click=lambda: _open_folder_dialog()).props(
                    "flat dense round"
                ).classes("text-purple-300").tooltip(tr("archive.new_folder_tooltip"))

        def _apply_search(e) -> None:
            _archive_ui["search"] = (getattr(e, "value", None) or "").strip()
            render_archive_file_list.refresh()

        ui.input(
            placeholder=tr("archive.search_placeholder"),
            value=_archive_ui.get("search") or "",
            on_change=_apply_search,
        ).props("dense outlined clearable").classes("w-full text-xs shrink-0")
        ui.label(tr("archive.search_hint")).classes("text-[10px] text-gray-500 shrink-0 -mt-1 mb-1")

        with ui.row().classes("w-full items-center gap-1 shrink-0 flex-nowrap min-w-0"):
            ui.icon("folder_open", size="xs").classes("text-purple-400/80 shrink-0")
            ui.label(_breadcrumb_path()).classes(
                "text-[11px] text-gray-400 truncate flex-1 font-mono"
            )
            if get_current_path():
                ui.button(tr("common.root"), on_click=lambda: _navigate_to("")).props("flat dense size=sm").classes(
                    "text-[10px] text-purple-300 shrink-0"
                ).tooltip(tr("archive.back_root_tooltip", root=_archive_root_label))

        render_archive_file_list()

        ui.label(tr("archive.drag_footer")).classes(
            "text-xs text-gray-500 text-center w-full shrink-0 pt-1"
        )


class ChatArchiveManagerExtension(BaseExtension):
    extension_id = "chat_archive_manager"
    label = "Chat Archive"
    show_in_dropdown = True

    def mount(self, container) -> None:
        with container:
            render_chats_tab()