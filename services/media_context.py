# -*- coding: utf-8 -*-

"""Resolve attached audio/video into text for chat context."""

from __future__ import annotations



import os



from services.media_query import classify_media_query, extract_video_keyframes, is_video_file

from services.media_transcription import transcribe_media_file, transcription_deps_available

from services.model_router import check_model_supports_audio, find_video_vision_model, find_vision_model



GAP_TRANSCRIPTION = "__LOMA_GAP_TRANSCRIPTION__"

GAP_VISION = "__LOMA_GAP_VISION__"



_TRANSCRIPT_EXCERPT_CHARS = 2800





def _transcribe(

    filepath: str,

    *,

    scope: str = "full",

) -> tuple[str | None, str | None]:

    ok, _missing = transcription_deps_available()

    if not ok:

        return None, GAP_TRANSCRIPTION

    parsed = transcribe_media_file(filepath)

    if parsed.get("type") == "text":

        body = (parsed.get("content") or "").strip()

        filename = os.path.basename(filepath)

        total_len = len(body)
        if scope == "excerpt" and total_len > _TRANSCRIPT_EXCERPT_CHARS:
            body = (
                body[:_TRANSCRIPT_EXCERPT_CHARS].rstrip()
                + "\n\n…\n\n"
                + f"*(Transcript excerpt — {total_len:,} characters total. "
                "Ask for **full transcript** if you need every word.)*"
            )

            heading = "## Transcript excerpt (audio)"

        else:

            heading = "## Transcript (audio)"

        return f"{heading}: {filename}\n\n{body}", None

    if parsed.get("type") == "error":

        content = str(parsed.get("content") or "")

        if "faster-whisper" in content.lower() or "ffmpeg" in content.lower():

            return None, GAP_TRANSCRIPTION

        return None, content

    return None, GAP_TRANSCRIPTION





def _vision_wait_message(filename: str) -> str:

    return (

        f"## Video: {filename}\n\n"

        "Your question requires **visual analysis** of this video (not audio transcription alone).\n\n"

        "1. Open **Settings** and select a **vision-capable model** (e.g. LLaVA, Moondream).\n"

        "2. Resubmit your question and **wait several minutes** while LOMA extracts frames "

        "and runs vision inference.\n\n"

        "Until a vision model is configured, LOMA cannot answer visual detail questions about this file."

    )





def _holistic_instruction() -> str:

    return (

        "**Instructions:** Give **one cohesive analysis** of this video. Combine what is said "

        "(transcript excerpt below, if present) with what is visible (attached frames). "

        "Do not reply as if transcript and visuals are separate tasks."

    )





def _vision_only_instruction(frame_count: int, vision_model: str) -> str:

    return (

        f"**Instructions:** Answer using **visual evidence only** from the {frame_count} attached frame(s). "

        f"Vision model: `{vision_model}`. Do not invent dialogue you cannot see on screen."

    )





def resolve_media_to_markdown(

    filepath: str,

    model_name: str,

    query: str = "",

) -> tuple[str | None, str | None, list[str]]:

    """

    Returns (markdown_for_context, gap_error, vision_frame_paths).

    """

    filename = os.path.basename(filepath)

    ext = os.path.splitext(filename)[1].lower()

    video = is_video_file(filepath)

    mode, transcript_scope = classify_media_query(query, is_video=video)



    if mode in ("vision", "both") and video:

        vision_model = find_video_vision_model()

        if not vision_model:

            return _vision_wait_message(filename), GAP_VISION, []



        frames = extract_video_keyframes(filepath)

        holistic = mode == "both" and transcript_scope == "excerpt"



        blocks: list[str] = [f"## Video: {filename}"]

        if holistic:

            blocks.append(_holistic_instruction())

        elif mode == "vision":

            blocks.append(_vision_only_instruction(len(frames), vision_model))

        else:

            blocks.append(

                f"Vision and transcript requested (model: `{vision_model}`). "

                f"{len(frames)} frame(s) attached."

            )



        if mode == "both":

            md, gap = _transcribe(filepath, scope=transcript_scope)

            if md:

                blocks.append(md)

            elif gap:

                blocks.append(

                    "(Transcript unavailable — answer from frames only or install Whisper/ffmpeg.)"

                )

        elif not frames:

            return (

                f"## Video: {filename}\n\n"

                "Could not extract frames (ffmpeg required). Install ffmpeg or ask for a transcript instead.",

                None,

                [],

            )

        else:

            blocks.append(

                f"{len(frames)} frame(s) attached for visual understanding — "

                "processing may take several minutes."

            )

        return "\n\n".join(blocks), None, frames



    if ext not in {".mp4", ".mkv", ".mov", ".webm", ".avi"} and check_model_supports_audio(model_name):

        return (

            f"## Attached audio: {filename}\n"

            f"The active chat model (`{model_name}`) supports audio input. "

            f"File path: `{filepath}`. Answer questions about this recording using multimodal understanding.",

            None,

            [],

        )



    md, gap = _transcribe(filepath, scope=transcript_scope if video else "full")

    return md, gap, []


