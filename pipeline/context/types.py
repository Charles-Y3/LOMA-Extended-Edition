# -*- coding: utf-8 -*-
"""Shared context ingest types."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

ContextStrategy = Literal["fit", "retrieve", "map_reduce", "per_source"]

_PREVIEW_CHARS = 1_200


@dataclass
class SourceDigest:
    """Parsed source material held for planners and map-reduce jobs."""

    digest_id: str
    name: str
    kind: str
    char_count: int
    full_text: str
    preview: str = ""
    vision_paths: list[str] = field(default_factory=list)
    truncated_in_preview: bool = False

    def to_index_line(self) -> str:
        extra = f", vision×{len(self.vision_paths)}" if self.vision_paths else ""
        return f"- **{self.name}** ({self.kind}, {self.char_count:,} chars{extra})"

    def to_dict(self) -> dict:
        return {
            "digest_id": self.digest_id,
            "name": self.name,
            "kind": self.kind,
            "char_count": self.char_count,
            "preview": self.preview,
            "truncated_in_preview": self.truncated_in_preview,
            "vision_paths": list(self.vision_paths),
        }


def make_preview(text: str, limit: int = _PREVIEW_CHARS) -> tuple[str, bool]:
    raw = (text or "").strip()
    if len(raw) <= limit:
        return raw, False
    return raw[:limit].rstrip() + "…", True
