# -*- coding: utf-8 -*-
"""Bounded limits for agentic presentation generation (compile-safe)."""
from __future__ import annotations

# Copy
MAX_DECK_TITLE_CHARS = 80
MAX_SLIDE_TITLE_CHARS = 70
MAX_SUBTITLE_CHARS = 120
MAX_BULLET_CHARS = 140
MAX_BULLETS_PER_SLIDE = 5
MIN_SLIDES = 1
MAX_SLIDES = 30

# Density heuristics for compile gate
MAX_CHARS_PER_CONTENT_SLIDE = 650
MAX_TOTAL_BULLETS_WARNING = 40

# Allowed layout / design tokens (workers must pick from these)
ALLOWED_LAYOUTS = frozenset({"title", "content", "section", "closing", "quote"})
ALLOWED_MOODS = frozenset({
    "warm",
    "professional",
    "nature",
    "creative",
    "minimal",
    "bold",
    "calm",
})
ALLOWED_PALETTES = frozenset({
    "warm_coral",
    "ocean_teal",
    "forest_green",
    "royal_purple",
    "slate_modern",
    "sunset_amber",
    "rose_blush",
    "midnight_blue",
})
