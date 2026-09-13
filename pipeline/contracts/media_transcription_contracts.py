# -*- coding: utf-8 -*-
"""Contracts for media_transcription role outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaTranscriptionContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, MediaTranscriptionContract] = {
    "transcript_markdown": MediaTranscriptionContract(
        id="transcript_markdown",
        description="Timestamped transcript for chat or docx export.",
        output_rules=(
            "Use markdown headings for timestamps (e.g. ## [mm:ss]).",
            "One paragraph per timestamp segment.",
            "Do not add commentary unless the user asked for it.",
        ),
        max_chars=48000,
    ),
    "transcript_summary": MediaTranscriptionContract(
        id="transcript_summary",
        description="Summary of transcript content.",
        output_rules=(
            "Summarize key points in concise bullets or short sections.",
            "Do not invent facts not stated in the transcript.",
        ),
        max_chars=12000,
    ),
    "transcript_translation": MediaTranscriptionContract(
        id="transcript_translation",
        description="Translated transcript.",
        output_rules=(
            "Return the translated transcript with timestamps preserved where possible.",
            "Do not add translator notes unless requested.",
        ),
        max_chars=48000,
    ),
}


def get_media_transcription_contract(contract_id: str) -> MediaTranscriptionContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["transcript_markdown"])


def get_media_transcription_contract_for_role(role_id: str) -> MediaTranscriptionContract:
    role_to_contract = {
        "transcript_presenter": "transcript_markdown",
        "transcript_summarizer": "transcript_summary",
        "transcript_translator": "transcript_translation",
    }
    return get_media_transcription_contract(role_to_contract.get(role_id, "transcript_markdown"))
