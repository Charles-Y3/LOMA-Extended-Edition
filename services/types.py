# -*- coding: utf-8 -*-
"""Shared value types for atomic services (no cross-service imports)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

SourceKind = Literal["document", "web", "excerpt"]


@dataclass(frozen=True)
class TextChunk:
    chunk_id: str
    source: str
    source_kind: SourceKind
    text: str
    index: int
    section: str | None = None


@dataclass(frozen=True)
class SourceDocument:
    name: str
    text: str
    source_kind: SourceKind = "document"
