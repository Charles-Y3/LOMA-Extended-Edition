# -*- coding: utf-8 -*-
"""VRAM/RAM thresholds for resource governor."""
from __future__ import annotations

import config

TIGHT_RAM_GB = config.GOVERNOR_TIGHT_RAM_GB
TIGHT_VRAM_GB = config.GOVERNOR_TIGHT_VRAM_GB
MIN_AVAILABLE_RAM_GB = config.GOVERNOR_MIN_AVAILABLE_RAM_GB
MEDIA_KEEPALIVE_SECONDS = config.GOVERNOR_MEDIA_KEEPALIVE_SECONDS

MEDIA_TASKS = frozenset({"image_gen", "audio_gen", "video_gen"})
LLM_TASKS = frozenset({"llm_chat", "llm_vision"})
