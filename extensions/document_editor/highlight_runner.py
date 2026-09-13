# -*- coding: utf-8 -*-
"""Document viewer highlight entrypoint."""
from extensions.viewer_runtime.highlight_runner import start_viewer_highlight_workflow


def start_document_highlight_workflow(full_query: str, instruction: str) -> None:
    start_viewer_highlight_workflow(full_query, instruction, viewer_id="document_editor")
