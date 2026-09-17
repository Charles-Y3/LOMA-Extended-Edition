# -*- coding: utf-8 -*-
"""Shared highlight → query / add-to-sources dialogs for extension viewers."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import state
from services.session.prompt_memory import last_typed_prompt, remember_typed_prompt

_highlight_dialog_open = False


def render_last_query_chip(instruction_field) -> None:
    """Clickable chip to reuse the user's last typed prompt text."""
    last = last_typed_prompt()
    if not last:
        return
    preview = last if len(last) <= 72 else last[:69] + "..."

    def use_last() -> None:
        instruction_field.value = last
        instruction_field.update()

    with ui.row().classes("w-full items-center gap-1 flex-wrap"):
        ui.label(tr("viewer.highlight_last_query")).classes("text-[10px] text-gray-500 shrink-0")
        ui.button(preview, on_click=use_last).props("flat dense no-caps").classes(
            "text-[11px] text-cyan-300/90 hover:bg-cyan-500/10 normal-case "
            "max-w-full truncate text-left"
        ).tooltip(last)


def add_text_to_sources(text: str, label: str = "viewer_excerpt") -> None:
    excerpt = (text or "").strip()
    if not excerpt:
        ui.notify(tr("viewer.highlight_nothing_add"), color="warning")
        return
    name = f"{label[:40]}.txt" if label else "excerpt.txt"
    existing = [f.get("filename") for f in state.active_context_files]
    if name in existing:
        ui.notify(tr("viewer.highlight_already_sources"), color="warning")
        return
    if len(state.active_context_files) >= 5:
        ui.notify(tr("viewer.highlight_sources_limit"), color="warning")
        return
    state.active_context_files.append(
        {"filename": name, "type": "text", "content": excerpt, "_attached_turn": state.context_attach_turn}
    )
    ui.notify(tr("viewer.highlight_added"), color="positive")
    try:
        from ui.components.attachments_hub import render_sources_hub

        render_sources_hub.refresh()
    except Exception:
        pass


def _render_kv_controls() -> dict:
    """Checkbox + mode/scope/library selects letting the user ground this Ask LOMA
    query in their Knowledge Vault catalog. Mirrors the vault-checkbox pattern
    in extensions/formslator/tabs/translate.py (settings-persisted select boxes)."""
    from extensions.knowledge_vault.corpus.backend_resolver import has_any_kv_data
    from extensions.knowledge_vault.corpus.library import list_libraries
    from extensions.knowledge_vault.ui.constants import mode_options
    from services.session.settings import save_settings

    cfg = state.current_settings or {}
    ctrl: dict = {
        "use_kv": bool(cfg.get("highlight_use_kv", False)),
        "mode": str(cfg.get("highlight_kv_mode") or "ask"),
        "scope": str(cfg.get("highlight_kv_scope") or "all"),
        "library_id": str(cfg.get("highlight_kv_library_id") or ""),
    }
    # "workspace" is deliberately not a selectable scope here — session/current-
    # document content stays usable inside Knowledge Vault's own tabs only, never
    # from this popup (see resolve_kv_backend's include_workspace=False call below).
    if ctrl["scope"] == "workspace":
        ctrl["scope"] = "all"

    if not has_any_kv_data():
        # Nothing indexed anywhere (no workspace session, no ready libraries) — don't
        # show the checkbox at all rather than offer a control that can only ever
        # return zero hits. Force use_kv off so callers never attempt the search.
        ctrl["use_kv"] = False
        return ctrl

    use_kv_cb = ui.checkbox(tr("viewer.highlight_use_kv"), value=ctrl["use_kv"]).props("dense")

    with ui.column().classes("w-full gap-1") as options_col:
        modes = mode_options()
        mode_sel = ui.select(
            options=modes, value=ctrl["mode"] if ctrl["mode"] in modes else "ask",
            label=tr("viewer.highlight_kv_mode_label"),
        ).classes("w-full").props("dense standout")

        scopes = {
            "all": tr("viewer.highlight_kv_scope_all"),
            "selected": tr("viewer.highlight_kv_scope_selected"),
        }
        scope_sel = ui.select(
            options=scopes, value=ctrl["scope"] if ctrl["scope"] in scopes else "all",
            label=tr("viewer.highlight_kv_scope_label"),
        ).classes("w-full").props("dense standout")

        libraries = {lib.library_id: lib.name for lib in list_libraries()}
        library_sel = ui.select(
            options=libraries, value=ctrl["library_id"] if ctrl["library_id"] in libraries else None,
            label=tr("viewer.highlight_kv_library_label"),
        ).classes("w-full").props("dense standout")
        library_sel.bind_visibility_from(scope_sel, "value", value="selected")

    options_col.bind_visibility_from(use_kv_cb, "value")

    def _persist(_=None) -> None:
        ctrl["use_kv"] = bool(use_kv_cb.value)
        ctrl["mode"] = str(mode_sel.value or "ask")
        ctrl["scope"] = str(scope_sel.value or "all")
        ctrl["library_id"] = str(library_sel.value or "")
        cfg["highlight_use_kv"] = ctrl["use_kv"]
        cfg["highlight_kv_mode"] = ctrl["mode"]
        cfg["highlight_kv_scope"] = ctrl["scope"]
        cfg["highlight_kv_library_id"] = ctrl["library_id"]
        save_settings(cfg, quiet=True)

    use_kv_cb.on_value_change(_persist)
    mode_sel.on_value_change(_persist)
    scope_sel.on_value_change(_persist)
    library_sel.on_value_change(_persist)

    return ctrl


def open_highlight_dialog(
    title: str,
    selection: str,
    *,
    on_query,
    source_label: str = "excerpt",
    show_add_to_sources: bool = True,
) -> None:
    global _highlight_dialog_open
    sel = (selection or "").strip()
    if not sel or _highlight_dialog_open:
        return
    _highlight_dialog_open = True

    preview = sel if len(sel) <= 240 else sel[:237] + "..."
    with ui.dialog() as dialog, ui.card().classes("min-w-[340px] max-w-lg gap-3 p-4"):
        ui.label(title).classes("text-sm font-bold text-cyan-300")
        ui.label(preview).classes(
            "text-[11px] text-gray-400 font-mono max-h-28 overflow-y-auto break-words "
            "whitespace-pre-wrap [overflow-wrap:anywhere] "
            "bg-black/30 rounded p-2 border border-white/10"
        )
        instruction = ui.input(tr("viewer.highlight_query_placeholder")).props(
            "outlined dense autofocus"
        ).classes("w-full text-[12px]")
        render_last_query_chip(instruction)

        kv_ctrl = _render_kv_controls()

        def _focus_input() -> None:
            try:
                instruction.run_method("focus")
            except Exception:
                pass
            try:
                ui.run_javascript(
                    """
                    setTimeout(function() {
                        const dlg = document.querySelector('.q-dialog--active, .q-dialog');
                        if (!dlg) return;
                        const inp = dlg.querySelector('input[type="text"], input:not([type])');
                        if (inp) { inp.focus(); }
                    }, 120);
                    """
                )
            except Exception:
                pass

        def _close_dialog() -> None:
            global _highlight_dialog_open
            _highlight_dialog_open = False
            dialog.close()

        def add_source_only() -> None:
            _close_dialog()
            add_text_to_sources(sel, source_label)

        def apply_and_close() -> None:
            text = (instruction.value or "").strip()
            use_kv = bool(kv_ctrl["use_kv"])
            kv_mode = str(kv_ctrl["mode"])
            kv_scope = str(kv_ctrl["scope"])
            kv_library_id = str(kv_ctrl["library_id"]) or None
            if not text and use_kv:
                # With Knowledge Vault on, an empty instruction is a valid
                # "just look this excerpt up" query — use the highlighted text itself
                # rather than forcing the user to retype it.
                text = sel
            if not text:
                ui.notify(tr("viewer.highlight_enter_question"), color="warning")
                return
            if use_kv and kv_scope == "selected" and not kv_library_id:
                ui.notify(tr("viewer.highlight_kv_no_library"), color="warning")
                return
            _close_dialog()
            remember_typed_prompt(text)
            on_query(sel, text, use_kv, kv_mode, kv_scope, kv_library_id)

        with ui.row().classes("w-full justify-end gap-2 mt-1 flex-wrap"):
            ui.button(tr("viewer.highlight_cancel"), on_click=_close_dialog).props("flat dense")
            if show_add_to_sources:
                ui.button(tr("viewer.highlight_add_sources"), icon="input", on_click=add_source_only).props(
                    "flat dense"
                )
            ui.button(tr("viewer.highlight_ask"), icon="send", on_click=apply_and_close).props("flat dense").classes(
                "text-cyan-300"
            )

        instruction.on("keydown.enter", apply_and_close)
    def _on_hide() -> None:
        global _highlight_dialog_open
        _highlight_dialog_open = False

    dialog.on("hide", _on_hide)
    dialog.open()
    ui.timer(0.05, _focus_input, once=True)
    ui.timer(0.15, _focus_input, once=True)
    ui.timer(0.35, _focus_input, once=True)


async def read_dom_selection() -> str:
    result = await ui.run_javascript(
        """
        const selection = window.getSelection();
        return selection ? selection.toString().trim() : "";
        """,
        timeout=2.0,
    )
    return (result or "").strip() if isinstance(result, str) else ""
