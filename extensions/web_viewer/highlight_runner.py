# -*- coding: utf-8 -*-
"""Web viewer highlight entrypoint."""
from extensions.viewer_runtime.highlight_runner import start_viewer_highlight_workflow


def start_web_highlight_workflow(full_query: str, instruction: str) -> None:
    start_viewer_highlight_workflow(full_query, instruction, viewer_id="web_viewer")
