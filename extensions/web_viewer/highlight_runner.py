# -*- coding: utf-8 -*-
"""Web viewer highlight entrypoint."""
from extensions.viewer_runtime.highlight_runner import start_viewer_highlight_workflow


def start_web_highlight_workflow(
    full_query: str,
    instruction: str,
    *,
    use_kv: bool = False,
    kv_mode: str = "ask",
    kv_scope: str = "all",
    kv_library_id: str | None = None,
) -> None:
    start_viewer_highlight_workflow(
        full_query, instruction, viewer_id="web_viewer",
        use_kv=use_kv, kv_mode=kv_mode, kv_scope=kv_scope, kv_library_id=kv_library_id,
    )
