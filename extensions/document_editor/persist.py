# -*- coding: utf-8 -*-
"""Persist document editor source edits back to disk."""
from __future__ import annotations

import os
import shutil
from pathlib import Path


def save_docx_markdown(filepath: str, markdown_text: str) -> None:
    from services.artifact_build import build_docx_from_markdown

    # Document Editor saves are direct WYSIWYG edits, not AI-generated reports — the user
    # controls headings/structure themselves, so don't inject a cover page or Word TOC field
    # (build_docx_from_markdown defaults both to True for the chat-workspace "build a report"
    # flow in services/artifact_build.py, which should keep that default).
    build_docx_from_markdown(markdown_text or "", filepath, want_cover=False, want_toc=False)


def _text_body(value) -> str:
    if isinstance(value, str):
        return value
    args = getattr(value, "args", None)
    if isinstance(args, str):
        return args
    if isinstance(args, dict):
        return str(args.get("value") or args.get("content") or "")
    return ""


def persist_docx_state(filepath: str, parsed: dict | None, editor_value: str | None = None) -> str:
    """Merge editor text into parsed dict and write .docx. Returns saved markdown body."""
    if not filepath or not filepath.lower().endswith(".docx"):
        return ""
    if editor_value is not None:
        body = _text_body(editor_value).strip()
    else:
        body = ""
    if body == "" and parsed:
        body = _text_body(parsed.get("content")).strip()
    elif parsed is not None:
        parsed["content"] = body
    if not body:
        return ""
    save_docx_markdown(filepath, body)
    return body


def export_to_downloads(filepath: str) -> str:
    """Copy the current file to the user's Downloads folder."""
    src = Path(filepath)
    if not src.is_file():
        raise FileNotFoundError(filepath)
    dest_dir = Path.home() / "Downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    shutil.copy2(src, dest)
    return str(dest)
