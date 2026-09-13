# -*- coding: utf-8 -*-
"""Office document mutation service (extract → plan → apply → export)."""
from __future__ import annotations

from services.office_mutation.extract import extract_units
from services.office_mutation.mutate import mutate_office_file
from services.office_mutation.paths import resolve_upload_path, source_format, target_extension
from services.office_mutation.plan_text import plan_text_map

__all__ = [
    "extract_units",
    "mutate_office_file",
    "plan_text_map",
    "resolve_upload_path",
    "source_format",
    "target_extension",
]
