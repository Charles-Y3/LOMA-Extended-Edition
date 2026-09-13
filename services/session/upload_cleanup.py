# -*- coding: utf-8 -*-
"""Upload folder retention and workspace source clearing."""
from __future__ import annotations

import os
import time
from typing import Callable

from services.session import state

UPLOAD_DIR = os.path.join("data", "uploads")


def upload_retention_days(settings: dict | None = None) -> int:
    """Days to keep files in data/uploads root; 0 = never delete on startup."""
    cfg = settings if settings is not None else (state.current_settings or {})
    try:
        return max(0, int(cfg.get("upload_retention_days", 7)))
    except (TypeError, ValueError):
        return 7


def purge_stale_uploads(
    settings: dict | None = None,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> int:
    """Delete files in data/uploads root older than retention setting. Returns count removed."""
    days = upload_retention_days(settings)
    if days <= 0:
        return 0
    if not os.path.isdir(UPLOAD_DIR):
        return 0

    cutoff = time.time() - (days * 86400)
    removed = 0
    for name in os.listdir(UPLOAD_DIR):
        path = os.path.join(UPLOAD_DIR, name)
        if not os.path.isfile(path):
            continue
        try:
            if os.path.getmtime(path) < cutoff:
                os.remove(path)
                removed += 1
        except OSError as exc:
            if log_fn:
                log_fn(f"Upload retention: could not remove {name!r}: {exc}")
    if removed and log_fn:
        log_fn(f"Upload retention: purged {removed} file(s) older than {days} day(s)")
    return removed


def _invalidate_context_cache() -> None:
    state.context_bundle_cache_key = None
    state.context_parsed_sources_cache = None
    state.context_images_cache = []
    state.context_source_digests_cache = None
    state.context_media_blocks_cache = []


def clear_all_sources(*, delete_disk_files: bool = True) -> int:
    """Clear sources panel entries, web cache, and optional upload files on disk."""
    filenames = [
        (f.get("filename") or "").strip()
        for f in list(state.active_context_files or [])
        if isinstance(f, dict)
    ]
    links = list(state.active_web_links or [])

    state.active_context_files.clear()
    state.active_web_links.clear()
    state.web_scrape_cache.clear()
    _invalidate_context_cache()

    try:
        from services.web_context_cache import drop_cached

        for link in links:
            drop_cached(link)
    except Exception:
        pass

    deleted = 0
    if delete_disk_files:
        for name in filenames:
            if not name:
                continue
            path = os.path.join(UPLOAD_DIR, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    deleted += 1
                    state.add_log(f"Removed upload: {name}")
                except OSError as exc:
                    state.add_log(f"Could not remove upload {name}: {exc}")

    return deleted
