# -*- coding: utf-8 -*-
"""Clickable file/folder links for workspace chat."""
from __future__ import annotations

import os
from urllib.parse import quote


def resolve_abs_path(display_path: str) -> str:
    p = (display_path or "").strip().replace("\\", "/")
    if not p:
        return ""
    if os.path.isabs(p):
        return os.path.abspath(p)
    return os.path.abspath(os.path.join(os.path.expanduser("~"), p))


def loma_open_link(label: str, abs_path: str) -> str:
    """Markdown link opened via workspace chat loma-open handler."""
    from services.security.path_guard import register_openable

    register_openable(abs_path)  # the app chose to show this path; forged links are not registered
    safe_label = (label or abs_path).replace("[", "\\[").replace("]", "\\]")
    encoded = quote(abs_path, safe="")
    return f"[{safe_label}](loma-open:{encoded})"


def format_file_path_links(display_path: str) -> str:
    from pipeline.i18n import t as tr

    disp = (display_path or "").strip().replace("\\", "/")
    if not disp:
        return ""
    abs_file = resolve_abs_path(disp)
    folder_abs = os.path.dirname(abs_file)
    folder_lbl = tr("knowledge_vault.path_folder")
    file_lbl = tr("knowledge_vault.path_file")
    if "/" in disp:
        parent_disp, fname = disp.rsplit("/", 1)
        folder_line = f"{folder_lbl}: {loma_open_link(parent_disp + '/', folder_abs)}"
        file_line = f"{file_lbl}: {loma_open_link(fname, abs_file)}"
        return f"{folder_line}\n{file_line}"
    return f"{file_lbl}: {loma_open_link(disp, abs_file)}"
