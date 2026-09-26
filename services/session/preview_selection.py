# core/preview_selection.py
# -*- coding: utf-8 -*-
"""Capture and apply Preview text selections."""
from __future__ import annotations

import json
import time

from nicegui import ui

from services.session import state
from pipeline.i18n import t as _tr  # noqa: E402

PREVIEW_EDITOR_ID = "loma-preview-editor"


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # #region agent log
    try:
        with open("debug-6f9f14.log", "a", encoding="utf-8") as lf:
            lf.write(
                json.dumps(
                    {
                        "sessionId": "6f9f14",
                        "hypothesisId": hypothesis_id,
                        "location": location,
                        "message": message,
                        "data": data,
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _preview_editor_ng_id() -> int | None:
    from ui.themes import registry

    editor = registry.preview_editor
    if editor is None:
        return None
    try:
        return int(editor.id)
    except (TypeError, ValueError, AttributeError):
        return None


async def read_selection_range() -> dict:
    """Read textarea selection; returns {start, end, text, taFound, hasRange, via}."""
    ng_id = _preview_editor_ng_id()
    if ng_id is None:
        _debug_log("B", "preview_selection.py:read_selection_range", "no editor ref", {})
        return {"taFound": False, "hasRange": False, "start": 0, "end": 0, "text": "", "via": "none"}

    try:
        probe = await ui.run_javascript(
            """
            (() => ({
                ok: true,
                hasActive: !!document.activeElement,
                activeTag: document.activeElement ? document.activeElement.tagName : null
            }))()
            """,
            timeout=1.5,
        )
        _debug_log("C", "preview_selection.py:read_selection_range", "probe", probe if isinstance(probe, dict) else {"probe": probe})
    except Exception as ex:
        _debug_log("C", "preview_selection.py:read_selection_range", "probe error", {"error": str(ex), "ngId": ng_id})

    try:
        result = await ui.run_javascript(
            f"""
            (() => {{
                function findTa() {{
                    const active = document.activeElement;
                    if (active && active.tagName === 'TEXTAREA') {{
                        return {{ ta: active, via: 'activeElement' }};
                    }}
                    const focusTa = document.querySelector('textarea:focus');
                    if (focusTa) {{
                        return {{ ta: focusTa, via: 'focusSelector' }};
                    }}
                    const all = Array.from(document.querySelectorAll('textarea'));
                    if (all.length) {{
                        return {{ ta: all[0], via: 'firstTextarea' }};
                    }}
                    return {{ ta: null, via: 'missing' }};
                }}
                const {{ ta, via }} = findTa();
                if (!ta) return {{ taFound: false, hasRange: false, start: 0, end: 0, text: '', via, ngId: {ng_id} }};
                const start = ta.selectionStart;
                const end = ta.selectionEnd;
                const text = start !== end ? ta.value.substring(start, end) : '';
                return {{
                    taFound: true,
                    hasRange: start !== end,
                    start,
                    end,
                    text,
                    scrollTop: ta.scrollTop,
                    via,
                    ngId: {ng_id},
                }};
            }})()
            """,
            timeout=3.0,
        )
        if isinstance(result, dict):
            _debug_log(
                "B",
                "preview_selection.py:read_selection_range",
                "read result",
                {
                    "taFound": result.get("taFound"),
                    "via": result.get("via"),
                    "ngId": result.get("ngId"),
                    "hasRange": result.get("hasRange"),
                },
            )
            return result
    except Exception as ex:
        _debug_log("B", "preview_selection.py:read_selection_range", "js error", {"error": str(ex), "ngId": ng_id})
    return {"taFound": False, "hasRange": False, "start": 0, "end": 0, "text": "", "via": "error"}


def store_selection(start: int, end: int, text: str) -> None:
    state.preview_sel_start = int(start)
    state.preview_sel_end = int(end)
    state.preview_selection = (text or "").strip()


def clear_selection() -> None:
    state.preview_selection = ""
    state.preview_sel_start = -1
    state.preview_sel_end = -1


def selection_valid_in_draft(draft: str, selection: str) -> bool:
    """True when locked selection can be spliced into draft."""
    if not selection or not draft:
        return False
    start = getattr(state, "preview_sel_start", -1)
    end = getattr(state, "preview_sel_end", -1)
    if start >= 0 and end > start and end <= len(draft):
        return True
    return selection in draft


async def capture_selection_from_editor() -> bool:
    """mouseup handler: persist highlighted range before focus is lost."""
    data = await read_selection_range()
    _debug_log(
        "A",
        "preview_selection.py:capture_selection_from_editor",
        "mouseup capture",
        {
            "taFound": data.get("taFound"),
            "hasRange": data.get("hasRange"),
            "via": data.get("via"),
            "start": data.get("start"),
            "end": data.get("end"),
            "text_len": len(data.get("text") or ""),
        },
    )
    if not data.get("hasRange") or not (data.get("text") or "").strip():
        return False
    store_selection(int(data["start"]), int(data["end"]), data["text"])
    try:
        state.preview_scroll_top = int(data.get("scrollTop") or 0)
    except (TypeError, ValueError):
        state.preview_scroll_top = 0
    return True


def update_selection_ui() -> None:
    """Refresh inline selection widgets without rebuilding the whole panel."""
    import ui.themes as themes

    sel = (state.preview_selection or "").strip()
    row = getattr(themes, "preview_revision_row", None)
    status = getattr(themes, "preview_selection_status", None)
    if status is not None:
        if sel:
            preview = sel if len(sel) <= 120 else sel[:117] + "…"
            status.set_text(_tr("preview.selected", count=f"{len(sel):,}", preview=preview))
        else:
            status.set_text(_tr("preview.highlight_hint"))
    if row is not None:
        row.set_visibility(bool(sel))
