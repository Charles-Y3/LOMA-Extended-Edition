# -*- coding: utf-8 -*-
"""Shared helper: resolve attached image file paths from a ContextBundle."""
from __future__ import annotations

import os
from typing import Any


def image_paths_from_bundle(bundle: Any) -> list[str]:
    """Filesystem paths of image sources attached to this request.

    Used by both the direct pipeline and the agentic/plan pipeline so mutation
    dispatch sees the same source images regardless of which pipeline routed
    the request.
    """
    paths: list[str] = []
    for ps in getattr(bundle, "parsed_sources", None) or []:
        p = getattr(ps, "path", None) or getattr(ps, "media_path", None)
        if not p or not os.path.isfile(p):
            continue
        kind = getattr(ps, "kind", "") or ""
        name = (getattr(ps, "name", "") or "").lower()
        if kind == "image" or name.endswith((".jpg", ".jpeg", ".png", ".webp", ".gif")):
            paths.append(p)
    return paths
