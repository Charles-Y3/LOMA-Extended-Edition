# -*- coding: utf-8 -*-
"""Shared corpus types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


class CorpusKind(str, Enum):
    WORKSPACE = "workspace"
    LIBRARY = "library"


class TaskKind(str, Enum):
    SEARCH = "search"
    ASK = "ask"
    ANALYZE = "analyze"


class ReasoningMode(str, Enum):
    FAST = "fast"
    DEEP = "deep"
    AGENT = "agent"


class Mode(str, Enum):
    """The single user-facing dropdown value — replaces the old Task×Reasoning grid.
    TaskKind/ReasoningMode above stay as internal plumbing (retrieval tier + prompt
    framing); corpus_panel.py maps a Mode to the (TaskKind, ReasoningMode, deep_extras)
    combination each retrieval action actually needs."""

    SEARCH = "search"
    ASK = "ask"
    ANALYSE = "analyse"
    DEEP = "deep"
    AGENTIC = "agentic"


@dataclass
class ChunkRecord:
    chunk_id: str
    text: str
    source: str
    vault_path: str
    file_path: str
    page_number: int | None = None
    section: str = "General"
    segment_index: int = 0
    ingest_root: str = ""
    is_low_content: bool = False


def fully_excluded_file_keys(
    chunks: Iterable[ChunkRecord], key_fn: Callable[[ChunkRecord], str]
) -> set[str]:
    """Keys (via key_fn, e.g. absolute path or tree path) of files where EVERY chunk
    is is_low_content — not just some. A file with a handful of low-content chunks
    mixed in with real content (a stray short header/footer fragment) is NOT fully
    excluded: only a true whole-file case (e.g. a genuine cover-page file) is. Shared
    by the tree UI's per-file exclusion icon and the "(N excluded)" stats count so
    the two can't drift apart and disagree with each other again."""
    totals: dict[str, list[int]] = {}
    for ch in chunks:
        key = key_fn(ch)
        if not key:
            continue
        bucket = totals.setdefault(key, [0, 0])
        bucket[0] += 1
        if ch.is_low_content:
            bucket[1] += 1
    return {k for k, (total, low) in totals.items() if total and low == total}


@dataclass
class HitRecord:
    chunk: ChunkRecord
    score: float
    snippet: str = ""

    def to_dict(self) -> dict:
        return {
            "content": self.chunk.text,
            "score": self.score,
            "snippet": self.snippet or self.chunk.text[:240],
            "source_name": self.chunk.source,
            "file_location": self.chunk.file_path,
            "vault_path": self.chunk.vault_path,
            "page_number": self.chunk.page_number,
            "section": self.chunk.section,
            "metadata": {
                "source": self.chunk.source,
                "vault_path": self.chunk.vault_path,
                "page_number": self.chunk.page_number,
                "section_title": self.chunk.section,
            },
        }


@dataclass
class LibraryManifest:
    library_id: str
    name: str
    roots: list[str] = field(default_factory=list)
    lexical_ready: bool = False
    semantic_ready: bool = False
    semantic_building: bool = False
    chunk_count: int = 0
    file_count: int = 0
    semantic_enabled: bool = False
    # Absolute source-file paths the user explicitly removed from the catalogue — kept
    # here (not just deleted from self.chunks) so a later rebuild's filesystem rescan
    # doesn't silently bring the file back.
    excluded_paths: list[str] = field(default_factory=list)
    # Absolute source-file paths manually marked "exclude from retrieval" (distinct
    # from the automatic is_low_content tag) — reapplied on rebuild for the same reason.
    retrieval_excluded_paths: list[str] = field(default_factory=list)
