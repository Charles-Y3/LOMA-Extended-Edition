# -*- coding: utf-8 -*-
"""Routing helpers for capability selection."""
from __future__ import annotations

import os
import re

from pipeline.schemas.task_schema import InputMetadata

_MEDIA_EXTS = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mkv", ".mov", ".webm", ".avi"})

# Hint word lists formerly here (transcribe/translate-media/image-mutation) now live
# in pipeline/query_intent_i18n.py's CONCEPTS ("verb_transcribe", "verb_translate",
# "image_mutation_hints") — one shared, multilingual source instead of a per-file
# English-only tuple. See CLAUDE.md section 8.


def _file_is_media(name: str, ftype: str | None = None) -> bool:
    if ftype in ("media_audio", "media_video", "audio", "video"):
        return True
    ext = os.path.splitext((name or "").lower())[1]
    return ext in _MEDIA_EXTS


def metadata_has_media(metadata: InputMetadata | None) -> bool:
    if metadata is None:
        return False
    for f in metadata.files or []:
        if isinstance(f, dict) and _file_is_media(f.get("name") or "", f.get("type")):
            return True
    return False


def metadata_has_transcribable_source(metadata: InputMetadata | None) -> bool:
    """Uploaded media files or video/audio web links."""
    if metadata_has_media(metadata):
        return True
    if metadata is None:
        return False
    try:
        from services.media_fetch import link_is_video_or_audio

        for link in metadata.links or []:
            if link_is_video_or_audio(link):
                return True
    except Exception:
        pass
    return False


def query_requests_transcription(query: str, preferred_output: str | None = None) -> bool:
    from pipeline.query_intent_i18n import matches

    if matches(query, "verb_transcribe"):
        return True
    if matches(query, "verb_translate"):
        return True
    lower = (query or "").lower()
    if (preferred_output or "").strip().lower() == "document" and any(
        w in lower for w in ("recording", "audio", "video", "mp3", "mp4", "interview", "youtube")
    ):
        return True
    return False


def query_requests_image_mutation(query: str) -> bool:
    from pipeline.query_intent_i18n import matches

    lower = (query or "").lower()
    if re.search(
        r"\b(change|recolor|colour|color|edit|modify|adjust)\b.*\b(to|into)\b",
        lower,
    ):
        return True
    if not matches(query, "image_mutation_hints"):
        return False
    if any(w in lower for w in ("generate", "create an image", "draw me", "draw a", "from scratch")):
        if not any(w in lower for w in ("mutate", "edit", "change", "modify", "recolor", "inpaint")):
            return False
    return True


def should_route_image_mutation(metadata: InputMetadata | None) -> bool:
    """Uploaded image + edit/mutate intent → image_mutation (not image_generation).

    Deliberately does NOT require the resolved deliverable type to already be
    "image" — `resolve_deliverable_type()`'s query-format hints are generation-phrase
    oriented ("draw me", "generate an image"), so a plain edit instruction like
    "change the frog to black color" resolves to "chat" and used to make this whole
    check return False, silently discarding the attached image in favor of a fresh,
    unrelated text-to-image generation. An attached image plus a color/edit-verb
    instruction should always win over deliverable-type guessing; the caller
    (InputRouter.route()) forces output_type to "image" when this returns True.
    """
    if metadata is None or not getattr(metadata, "has_image", False):
        return False
    return query_requests_image_mutation(metadata.query)


def should_route_media_transcription(metadata: InputMetadata | None) -> bool:
    """Route to media_transcription for explicit transcribe intent with media or video links."""
    if metadata is None:
        return False
    if not metadata_has_transcribable_source(metadata):
        return False
    return query_requests_transcription(
        metadata.query,
        metadata.preferred_output_format,
    )


_TABULAR_SUFFIXES = (".csv", ".xlsx", ".xls", ".tsv")


def metadata_has_tabular_upload(metadata: InputMetadata | None) -> bool:
    if metadata is None:
        return False
    for f in metadata.files or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("name") or f.get("filename") or "").lower()
        if any(name.endswith(ext) for ext in _TABULAR_SUFFIXES):
            return True
        if (f.get("type") or "").lower() in ("spreadsheet",):
            return True
    return False


def query_requests_graph_analysis(query: str) -> bool:
    from pipeline.query_intent_i18n import matches

    return matches(query, "verb_analyze")


def should_enrich_with_graph_analysis(
    metadata: InputMetadata | None,
    *,
    deliverable: str | None = None,
) -> bool:
    if metadata is None:
        return False
    if not metadata_has_tabular_upload(metadata):
        return False
    from pipeline.output_format import normalize_output_type

    out = normalize_output_type(deliverable or metadata.preferred_output_format)
    if out in ("document", "presentation", "spreadsheet"):
        return True
    return query_requests_graph_analysis(metadata.query)
