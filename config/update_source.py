# -*- coding: utf-8 -*-
"""Update source config — placeholder until LOMA is published on GitHub.

Fill in UPDATE_REPO (e.g. "yourname/LOMA") once the repo is public. An empty
UPDATE_REPO disables update checks entirely — check_for_updates() always
returns no updates and the settings toggle has nothing to do.
"""
from __future__ import annotations

# GitHub "owner/repo". Empty disables update checks.
UPDATE_REPO = ""

# Branch the manifest and update files are read from.
UPDATE_BRANCH = "main"

# Path (relative to repo root) to the remote update manifest JSON.
UPDATE_MANIFEST_PATH = "update_manifest.json"


def manifest_url() -> str:
    if not UPDATE_REPO:
        return ""
    return f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}/{UPDATE_MANIFEST_PATH}"


def raw_file_url(relative_path: str) -> str:
    if not UPDATE_REPO:
        return ""
    return f"https://raw.githubusercontent.com/{UPDATE_REPO}/{UPDATE_BRANCH}/{relative_path}"
