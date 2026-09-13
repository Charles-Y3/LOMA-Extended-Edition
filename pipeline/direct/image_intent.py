# -*- coding: utf-8 -*-
"""Classify an image-generation request as a plain photo/artwork, a poster that needs
real overlaid text, a linear/branching flowchart, a real data chart, or one of three
infographic layouts (a stat/icon grid, a chronological timeline, or a comparison).

Diffusion models (all three bundled checkpoints) can neither spell text reliably nor
draw structured diagrams/charts — both are architectural limits, not prompting
problems (see services/poster_generation.py, services/diagram_generation.py,
services/chart_generation.py and services/infographic_generation.py for the
deterministic renderers this classification routes to). Getting this split right
matters: a wrong "diagram" classification on a plain image request would produce a
boxes-and-arrows image nobody asked for, and vice versa.
"""
from __future__ import annotations

import re
from typing import Literal

ImageIntent = Literal[
    "photo", "poster", "diagram", "chart",
    "infographic_stat", "infographic_timeline", "infographic_comparison",
]

_ARROW_OR_DIGIT_STEP = re.compile(r"\d\s*(?:→|->|\.)|\bstep\s*\d\b", re.IGNORECASE)


def classify_image_request(query: str) -> ImageIntent:
    """Diagram checked first — an "infographic" that also names steps/sequence is a
    diagram request wearing infographic wording, not a single-visual poster. Chart,
    timeline, comparison, and stat-grid hints are checked next, before the generic
    poster fallback, since all are more specific than "poster".

    Each specific phrase-list check (chart_image_hints, diagram_image_hints, ...)
    is paired with a broader *_broad_keywords fallback (see query_intent_i18n.py) —
    a bare-word safety net for phrasing the fixed phrase lists don't anticipate
    ("chart about X" vs. the enumerated "chart of"/"chart showing"/...). This is
    the single canonical classifier for BOTH standalone chat image requests and
    document/presentation image markers (services/marker_visual.py) — a fix here
    reaches every caller instead of needing to be duplicated."""
    from pipeline.query_intent_i18n import matches

    q = query or ""
    lower = q.lower()

    wants_diagram = matches(lower, "diagram_image_hints") or matches(lower, "diagram_broad_keywords")
    if not wants_diagram and matches(lower, "poster_image_hints"):
        # "infographic"/generic poster wording + explicit step/sequence cues still
        # means diagram — e.g. "infographic showing the steps: 1. ... 2. ... 3. ...".
        if matches(lower, "diagram_step_cues") or _ARROW_OR_DIGIT_STEP.search(lower):
            wants_diagram = True
    if wants_diagram:
        return "diagram"

    if matches(lower, "chart_image_hints") or matches(lower, "chart_broad_keywords"):
        return "chart"

    if matches(lower, "infographic_comparison_hints"):
        return "infographic_comparison"

    if matches(lower, "infographic_timeline_hints"):
        return "infographic_timeline"

    if matches(lower, "infographic_stat_hints") or matches(lower, "infographic_broad_keywords"):
        return "infographic_stat"

    if matches(lower, "poster_image_hints"):
        return "poster"

    return "photo"
