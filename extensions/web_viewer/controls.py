# -*- coding: utf-8 -*-
"""Re-exports shared viewer controls (web viewer)."""
from extensions.viewer_runtime.controls import (  # noqa: F401
    POLICY_LITE_ONLY,
    POLICY_LITE_OR_PRO,
    get_execution_policy,
    on_viewer_closed as on_web_viewer_closed,
    on_viewer_opened as on_web_viewer_opened,
    set_execution_policy,
)
