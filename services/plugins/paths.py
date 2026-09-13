# -*- coding: utf-8 -*-
"""User-installed extension paths."""
from __future__ import annotations

import os
import sys


def loma_app_data_root() -> str:
    """"LOMA Extended Edition" is the primary folder name — kept distinct from bare
    "LOMA" (reserved for LOMA Complete Edition) and from "LOMA Core Edition" (see
    EDITIONS.md) so the editions never share or collide on the same AppData folder.
    Falls back to the bare-name folder if that's where an earlier install already
    has data, so nothing already-ingested (RAG corpus, chat history, settings) goes
    missing after this rename."""
    primary_name = "LOMA Extended Edition"
    legacy_names = ("LOMA",)

    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.path.expanduser("~")
        primary_name = ".loma-extended-edition"
        legacy_names = (".loma",)

    primary = os.path.join(base, primary_name)
    if os.path.isdir(primary):
        return primary
    for legacy_name in legacy_names:
        legacy = os.path.join(base, legacy_name)
        if os.path.isdir(legacy):
            return legacy
    return primary


def user_extensions_root() -> str:
    return os.path.join(loma_app_data_root(), "plugins", "extensions")


def ensure_user_extensions_root() -> str:
    root = user_extensions_root()
    os.makedirs(root, exist_ok=True)
    return root
