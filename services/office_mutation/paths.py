# -*- coding: utf-8 -*-
"""Resolve uploaded Office files regardless of output deliverable type."""
from __future__ import annotations

import os

OFFICE_EXTENSIONS = (".docx", ".pptx", ".xlsx", ".pdf")


def resolve_upload_path(original_filename: str) -> str:
    """Find the uploaded file by basename (any Office extension)."""
    base = os.path.splitext(os.path.basename(original_filename or ""))[0]
    if not base:
        return ""
    uploads = os.path.join("data", "uploads")
    for ext in OFFICE_EXTENSIONS:
        candidate = os.path.join(uploads, f"{base}{ext}")
        if os.path.isfile(candidate):
            return candidate
    direct = os.path.join(uploads, os.path.basename(original_filename or ""))
    if os.path.isfile(direct):
        return direct
    if original_filename and os.path.isfile(original_filename):
        return original_filename
    return ""


def source_format(path: str) -> str:
    ext = os.path.splitext(path or "")[1].lower()
    if ext == ".pptx":
        return "presentation"
    if ext == ".docx":
        return "document"
    if ext == ".xlsx":
        return "spreadsheet"
    if ext == ".pdf":
        return "document"
    return ""


def target_extension(source_path: str, output_type: str) -> str:
    """Output file extension for the requested deliverable."""
    out = (output_type or "").strip().lower()
    if out == "presentation":
        return ".pptx"
    if out == "spreadsheet":
        return ".xlsx"
    return ".docx"
