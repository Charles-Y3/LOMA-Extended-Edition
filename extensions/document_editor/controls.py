# -*- coding: utf-8 -*-
"""Re-exports shared viewer controls (document viewer)."""
from extensions.viewer_runtime.controls import (  # noqa: F401
    POLICY_LITE_ONLY,
    POLICY_LITE_OR_PRO,
    apply_lite_lock,
    get_effective_execution_mode,
    get_execution_policy,
    is_lite_lock_active,
    on_viewer_closed as on_document_viewer_closed,
    on_viewer_opened as on_document_viewer_opened,
    release_lite_lock,
    set_execution_policy,
    sync_workspace_execution_mode_select,
)
