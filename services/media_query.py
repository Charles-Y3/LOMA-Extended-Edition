# -*- coding: utf-8 -*-

"""Classify user queries for attached audio/video processing."""

from __future__ import annotations



import os

from pathlib import Path

from pipeline.query_intent_i18n import matches



_VIDEO_EXT = frozenset({".mp4", ".mkv", ".mov", ".webm", ".avi"})

















def is_video_file(path: str) -> bool:

    return Path(path or "").suffix.lower() in _VIDEO_EXT





def classify_media_query(query: str, *, is_video: bool = False) -> tuple[str, str]:

    """

    Return (mode, transcript_scope).



    mode: transcript | vision | both

    transcript_scope: none | excerpt | full

    """

    lower = (query or "").lower().strip()



    wants_holistic = matches(lower, "media_holistic")

    wants_vision_only = matches(lower, "media_vision_only")

    wants_transcript = matches(lower, "media_transcript_only")

    wants_full_transcript = matches(lower, "media_full_transcript")



    if not is_video:

        if wants_vision_only:

            return "vision", "none"

        return "transcript", "full"



    # --- Video ---



    # Explicit full transcript (no vision unless also visual-only cues).

    if wants_full_transcript or (wants_transcript and not wants_vision_only and not wants_holistic):

        if wants_vision_only and wants_transcript:

            return "both", "full"

        return "transcript", "full"



    # Explicit vision-only (describe scene, colors, on-screen text, etc.).

    if wants_vision_only and not wants_transcript:

        return "vision", "none"



    # Explicit both: user asked for transcription AND visual description.

    if wants_transcript and wants_vision_only:

        return "both", "full"



    # Default holistic: about / explain / general video question → excerpt + frames.

    if wants_holistic or (not wants_transcript and not wants_vision_only):

        from services.model_router import find_vision_model



        if find_vision_model():

            return "both", "excerpt"

        return "transcript", "full"



    from services.model_router import find_vision_model



    if find_vision_model():

        return "both", "excerpt"

    return "transcript", "full"





def extract_video_keyframes(path: str, *, max_frames: int = 4) -> list[str]:

    """Sample evenly spaced PNG frames for vision models (requires ffmpeg)."""

    if not is_video_file(path) or not os.path.isfile(path):

        return []

    from services.ffmpeg_util import ffmpeg_available, run_ffmpeg



    if not ffmpeg_available():

        return []



    out_dir = os.path.join("data", "generated", "video_frames")

    os.makedirs(out_dir, exist_ok=True)

    stem = Path(path).stem[:48]

    pattern = os.path.join(out_dir, f"{stem}_frame_%03d.png")



    try:

        run_ffmpeg(

            [

                "-y",

                "-i",

                path,

                "-vf",

                f"select='not(mod(n\\,{max(1, 30 // max_frames)}))',scale=640:-1",

                "-frames:v",

                str(max_frames),

                pattern,

            ],

            check=False,

            capture_output=True,

        )

    except Exception:

        return []



    frames = sorted(

        str(p)

        for p in Path(out_dir).glob(f"{stem}_frame_*.png")

        if p.is_file() and p.stat().st_size > 100

    )

    return frames[:max_frames]


