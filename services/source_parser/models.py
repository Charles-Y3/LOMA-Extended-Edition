# -*- coding: utf-8 -*-
"""Canonical parsed representation of any input-panel source."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ParsedSource:
    """
    Output of the source parser — independent of capability and deliverable type.

    - markdown/text: for chat context, RAG, generation prompts
    - mutation_units: for in-place Office edits (when applicable)
    - media_path: for audio/video when not yet transcribed
    - raw: original parse_uploaded_file dict or link metadata
    """

    name: str
    kind: str  # text | document | presentation | spreadsheet | image | audio | video | web | chat | error
    path: str = ""
    markdown: str = ""
    mutation_units: list[dict[str, Any]] = field(default_factory=list)
    media_path: str = ""
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.kind != "error" and not self.error

    def context_text(self) -> str:
        """Text suitable for LLM context (capabilities read this, not file bytes)."""
        return (self.markdown or "").strip()

    def is_office(self) -> bool:
        return self.kind in ("document", "presentation", "spreadsheet")

    def legacy_upload_dict(self) -> dict[str, Any]:
        """Dict matching parse_uploaded_file shape (UI / legacy consumers)."""
        if self.raw and self.raw.get("type"):
            out = dict(self.raw)
            out["filename"] = self.name
            return out
        if self.kind == "image":
            return {"filename": self.name, "type": "image", "content": self.media_path or self.path}
        if self.kind == "audio":
            return {"filename": self.name, "type": "media_audio", "content": self.media_path or self.path}
        if self.kind == "video":
            return {"filename": self.name, "type": "media_video", "content": self.media_path or self.path}
        if self.kind == "error":
            return {"filename": self.name, "type": "error", "content": self.error or "parse error"}
        return {
            "filename": self.name,
            "type": "text",
            "content": self.markdown,
        }

    def to_context_dict(self) -> dict[str, Any]:
        """Serialize for state.active_context_files (enriched, backward compatible)."""
        out = self.legacy_upload_dict()
        out["source_kind"] = self.kind
        if self.path:
            out["source_path"] = self.path
        if self.mutation_units:
            out["mutation_unit_count"] = len(self.mutation_units)
        return out
