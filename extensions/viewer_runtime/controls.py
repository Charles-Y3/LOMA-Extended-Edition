# -*- coding: utf-8 -*-
"""Execution policy and workspace Lite lock for document/web viewer extensions."""
from __future__ import annotations

from pipeline.execution_modes.constants import normalize_execution_mode
from services.session import settings as session_settings
from services.session import state

POLICY_LITE_ONLY = "lite_only"
POLICY_LITE_OR_PRO = "lite_or_pro"

VIEWER_EXTENSION_IDS = frozenset({"document_editor"})


def _policy_from_state() -> str:
    policy = (
        getattr(state, "viewer_execution_policy", None)
        or getattr(state, "document_viewer_execution_policy", None)
        or POLICY_LITE_ONLY
    )
    policy = (policy or POLICY_LITE_ONLY).strip()
    return policy if policy in (POLICY_LITE_ONLY, POLICY_LITE_OR_PRO) else POLICY_LITE_ONLY


def get_execution_policy() -> str:
    return _policy_from_state()


def set_execution_policy(policy: str) -> None:
    policy = (policy or POLICY_LITE_ONLY).strip()
    if policy not in (POLICY_LITE_ONLY, POLICY_LITE_OR_PRO):
        policy = POLICY_LITE_ONLY
    state.viewer_execution_policy = policy
    state.document_viewer_execution_policy = policy
    ext = _active_viewer_extension()
    if policy == POLICY_LITE_ONLY and ext:
        apply_lite_lock(ext)
    elif policy == POLICY_LITE_OR_PRO:
        release_lite_lock()


def is_lite_lock_active() -> bool:
    return bool(
        getattr(state, "viewer_lite_lock_active", False)
        or getattr(state, "document_viewer_lite_lock_active", False)
    )


def get_effective_execution_mode() -> str:
    if get_execution_policy() == POLICY_LITE_ONLY or is_lite_lock_active():
        return "direct"
    return normalize_execution_mode(
        (state.current_settings or {}).get("execution_mode")
        or (state.current_settings or {}).get("chat_execution_mode")
    )


def apply_lite_lock(extension_id: str = "") -> None:
    if is_lite_lock_active():
        sync_workspace_execution_mode_select()
        return
    current = normalize_execution_mode(
        (state.current_settings or {}).get("execution_mode")
        or (state.current_settings or {}).get("chat_execution_mode")
    )
    state.viewer_saved_execution_mode = current
    state.document_viewer_saved_execution_mode = current
    state.viewer_lite_lock_active = True
    state.document_viewer_lite_lock_active = True
    state.viewer_lock_owner = extension_id or _active_viewer_extension() or ""
    state.current_settings["execution_mode"] = "direct"
    state.current_settings["chat_execution_mode"] = "direct"
    session_settings.save_settings(state.current_settings, quiet=True)
    sync_workspace_execution_mode_select()


def release_lite_lock() -> None:
    if not is_lite_lock_active():
        sync_workspace_execution_mode_select()
        return
    saved = getattr(state, "viewer_saved_execution_mode", None) or getattr(
        state, "document_viewer_saved_execution_mode", None
    )
    if saved:
        mode = normalize_execution_mode(saved)
        state.current_settings["execution_mode"] = mode
        state.current_settings["chat_execution_mode"] = mode
        session_settings.save_settings(state.current_settings, quiet=True)
    state.viewer_lite_lock_active = False
    state.document_viewer_lite_lock_active = False
    state.viewer_saved_execution_mode = None
    state.document_viewer_saved_execution_mode = None
    state.viewer_lock_owner = ""
    sync_workspace_execution_mode_select()


def on_viewer_opened(extension_id: str) -> None:
    if extension_id not in VIEWER_EXTENSION_IDS:
        return
    if get_execution_policy() == POLICY_LITE_ONLY:
        apply_lite_lock(extension_id)
    else:
        release_lite_lock()


def on_viewer_closed(extension_id: str) -> None:
    if extension_id not in VIEWER_EXTENSION_IDS:
        return
    release_lite_lock()


def sync_workspace_execution_mode_select() -> None:
    try:
        from ui.themes import registry

        sel = registry.chat_mode_select
        if sel is None:
            return
        mode = normalize_execution_mode(
            (state.current_settings or {}).get("execution_mode")
            or (state.current_settings or {}).get("chat_execution_mode")
        )
        sel.value = mode
        if is_lite_lock_active():
            sel.disable()
        else:
            sel.enable()
            try:
                sel.props(remove="disable")
            except Exception:
                pass
        sel.update()
    except Exception:
        pass


def _active_viewer_extension() -> str:
    try:
        from ui.themes import registry

        ext = (registry.active_extension or "").strip()
        if ext in VIEWER_EXTENSION_IDS and registry.extension_panel_open:
            return ext
    except Exception:
        pass
    return ""
