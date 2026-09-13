# -*- coding: utf-8 -*-
"""Data directory paths for Formslator extension."""
from __future__ import annotations

import os

FORMSLATOR_ROOT = os.path.join("data", "formslator")
STYLES_DIR = os.path.join(FORMSLATOR_ROOT, "styles")
GLOSSARY_DIR = os.path.join(FORMSLATOR_ROOT, "glossary")
MAPPING_DIR = os.path.join(FORMSLATOR_ROOT, "mapping")
UPLOADS_DIR = os.path.join(FORMSLATOR_ROOT, "uploads")
OUTPUT_DIR = os.path.join(FORMSLATOR_ROOT, "output")
SETTINGS_FILE = os.path.join(FORMSLATOR_ROOT, "settings.json")
GLOSSARY_CONFIG_FILE = os.path.join(GLOSSARY_DIR, "glossary_config.json")

ALL_DIRS = (
    STYLES_DIR,
    GLOSSARY_DIR,
    MAPPING_DIR,
    UPLOADS_DIR,
    OUTPUT_DIR,
)


def ensure_dirs() -> None:
    for d in ALL_DIRS:
        os.makedirs(d, exist_ok=True)


_DEFAULT_TEMPLATES = (
    "default_template_v3_double_column.docx",
    "default_template_v3_single_column.docx",
)


def ensure_default_templates() -> None:
    """Copies Formslator's two bundled default style templates into the writable
    STYLES_DIR on first run. They ship as a read-only resource (packaging/loma_core.spec's
    'formslator_default_styles' datas entry) rather than living in STYLES_DIR itself,
    since STYLES_DIR sits under the gitignored data/ tree — worker.py's _resolve_template/
    _resolve_single_column_template already assume these two filenames exist there and
    auto-select them once nothing else has been chosen."""
    import shutil

    from services.platform_paths import resource_root

    ensure_dirs()
    src_dir = os.path.join(resource_root(), "formslator_default_styles")
    for name in _DEFAULT_TEMPLATES:
        dest = os.path.join(STYLES_DIR, name)
        if os.path.isfile(dest):
            continue
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, dest)


_DEFAULT_GLOSSARY_FILES = (
    "TaoTermsDatabase_v4_20250811.xlsx",
    "glossary_config.json",
)


def ensure_default_glossary() -> None:
    """Copies Formslator's bundled default glossary (and its column configuration) into
    the writable GLOSSARY_DIR on first run. They ship as a read-only resource
    (packaging/loma_core.spec's 'formslator_default_glossary' datas entry) rather than
    living in GLOSSARY_DIR itself, since GLOSSARY_DIR sits under the gitignored data/ tree —
    same pattern as ensure_default_templates() above."""
    import shutil

    from services.platform_paths import resource_root

    ensure_dirs()
    src_dir = os.path.join(resource_root(), "formslator_default_glossary")
    for name in _DEFAULT_GLOSSARY_FILES:
        dest = os.path.join(GLOSSARY_DIR, name)
        if os.path.isfile(dest):
            continue
        src = os.path.join(src_dir, name)
        if os.path.isfile(src):
            shutil.copy2(src, dest)


def resolve_writable_output_path(path: str) -> str:
    """Return a path that can be written; use numbered suffix if target is locked."""
    ensure_dirs()
    directory, filename = os.path.split(path)
    base, ext = os.path.splitext(filename)
    candidates = [path]
    for n in range(1, 51):
        candidates.append(os.path.join(directory, f"{base}_{n}{ext}"))

    for candidate in candidates:
        if not os.path.exists(candidate):
            return candidate
        try:
            os.remove(candidate)
            return candidate
        except OSError:
            continue

    import time

    return os.path.join(directory, f"{base}_{int(time.time())}{ext}")


def copy_output_to_downloads(src_path: str) -> str:
    """Copy a file from formslator output into the user's Downloads folder."""
    import shutil
    from pathlib import Path

    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)

    dest_dir = Path.home() / "Downloads"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / os.path.basename(src_path)
    if dest.exists():
        stem, suffix = os.path.splitext(dest.name)
        n = 2
        while dest.exists():
            dest = dest_dir / f"{stem} ({n}){suffix}"
            n += 1
    shutil.copy2(src_path, dest)
    return str(dest)
