# -*- coding: utf-8 -*-
"""Ephemeral workspace session."""
from __future__ import annotations

import os
import threading
from typing import Callable

from extensions.document_intelligence.corpus.types import ChunkRecord
from extensions.document_intelligence.extract import (
    extract_file_chunks,
    list_document_files,
    vault_path_for,
)
from extensions.document_intelligence.index.lexical import LexicalIndex
from extensions.document_intelligence.indexing_locks import pop_locked_files, prompt_close_locked_files
from extensions.document_intelligence.settings import load_settings
from extensions.document_intelligence.translation import TranslationIndex, pairs_from_file
from extensions.document_intelligence.ui.fs_tree import build_filesystem_tree, build_index_tree

LogFn = Callable[[str], None]


def _display_path(path: str) -> str:
    home = os.path.expanduser("~")
    abs_path = os.path.abspath(path)
    try:
        return (
            os.path.relpath(abs_path, home).replace("\\", "/")
            if abs_path.startswith(home)
            else abs_path
        )
    except ValueError:
        return abs_path


class WorkspaceSession:
    def __init__(self) -> None:
        self.roots: list[str] = []
        self.chunks: list[ChunkRecord] = []
        self.skipped: list[tuple[str, str, str]] = []
        self.lexical = LexicalIndex()
        self.translations = TranslationIndex()
        self.indexing = False
        self.index_error: str = ""
        self.file_count: int = 0
        self.index_progress: dict[str, int | str] = {
            "current": 0,
            "total": 0,
            "filename": "",
            "segments": 0,
            "pairs": 0,
        }
        self._lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return bool(self.lexical.chunks) and not self.indexing

    def filesystem_tree(self) -> dict:
        return build_filesystem_tree(self.roots)

    def indexed_tree(self) -> dict:
        if self.chunks or self.skipped:
            return build_index_tree(self.chunks, self.skipped)
        return self.filesystem_tree()

    def excluded_file_count(self) -> int:
        """Files fully excluded from retrieval — every chunk is_low_content (e.g. a
        genuine single-page cover file). A file with only some low-content chunks is
        not counted here — matches the tree UI's per-file exclusion icon."""
        from extensions.document_intelligence.corpus.library import chunk_abs_path
        from extensions.document_intelligence.corpus.types import fully_excluded_file_keys

        return len(fully_excluded_file_keys(self.chunks, chunk_abs_path))

    def delete_file(self, abs_path: str) -> bool:
        """Remove one file's chunks from this session (in-memory only, the file
        on disk is untouched, no manifest/db to persist since a workspace is
        ephemeral)."""
        from extensions.document_intelligence.corpus.library import chunk_abs_path

        abs_path = os.path.abspath(abs_path)
        keep: list[ChunkRecord] = []
        removed = False
        for ch in self.chunks:
            if chunk_abs_path(ch) == abs_path:
                removed = True
            else:
                keep.append(ch)
        if not removed:
            return False
        self.chunks = keep
        self.lexical.build(self.chunks)
        self.file_count = max(0, self.file_count - 1)
        return True

    def set_retrieval_excluded(self, abs_path: str, excluded: bool) -> bool:
        """Manually include/exclude a file from retrieval for this session."""
        from extensions.document_intelligence.corpus.library import chunk_abs_path

        abs_path = os.path.abspath(abs_path)
        changed = False
        for ch in self.chunks:
            if chunk_abs_path(ch) == abs_path:
                ch.is_low_content = excluded
                changed = True
        if not changed:
            return False
        self.lexical.build(self.chunks)
        return True

    def set_roots(self, roots: list[str]) -> None:
        self.roots = [os.path.abspath(r) for r in roots if r and os.path.isdir(r)]
        self.chunks = []
        self.lexical = LexicalIndex()
        self.translations = TranslationIndex()
        self.index_error = ""
        self.file_count = 0

    def build_index(
        self,
        *,
        log_fn: LogFn | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        if self.indexing or not self.roots:
            return

        def _run():
            with self._lock:
                self.indexing = True
                self.index_error = ""
                self.index_progress = {
                    "current": 0,
                    "total": 0,
                    "filename": "scanning…",
                    "segments": 0,
                    "pairs": 0,
                }
            all_chunks: list[ChunkRecord] = []
            all_pairs = []
            skipped: list[tuple[str, str, str]] = []
            try:
                paths: list[tuple[str, str]] = []
                for root in self.roots:
                    for path in list_document_files(root):
                        paths.append((root, path))

                total = len(paths)
                with self._lock:
                    self.index_progress["total"] = total
                    self.index_progress["filename"] = (
                        "scanning…" if total else "no documents found"
                    )

                doc_paths = [p for _root, p in paths if os.path.splitext(p)[1].lower() == ".doc"]
                if doc_paths:
                    from extensions.document_intelligence.extract import (
                        prime_doc_conversion_cache,
                    )

                    if log_fn:
                        log_fn(f"Converting {len(doc_paths)} legacy .doc file(s)…")
                    prime_doc_conversion_cache(doc_paths, log_fn=log_fn)

                for idx, (root, path) in enumerate(paths):
                    with self._lock:
                        self.index_progress["current"] = idx
                        self.index_progress["filename"] = os.path.basename(path)
                        self.index_progress["segments"] = len(all_chunks)
                        self.index_progress["pairs"] = len(all_pairs)

                    disp = _display_path(path)
                    last_reason = {"text": ""}

                    def _capture_log(msg: str, _last=last_reason) -> None:
                        _last["text"] = msg
                        if log_fn:
                            log_fn(msg)

                    new_chunks = extract_file_chunks(
                        path, ingest_root=root, log_fn=_capture_log
                    )
                    all_chunks.extend(new_chunks)
                    if not new_chunks:
                        skipped.append(
                            (root, vault_path_for(path, root), last_reason["text"] or "No content extracted")
                        )
                    all_pairs.extend(pairs_from_file(path, display_path=disp))

                    with self._lock:
                        self.index_progress["segments"] = len(all_chunks)
                        self.index_progress["pairs"] = len(all_pairs)

                with self._lock:
                    self.index_progress["current"] = total
                    self.index_progress["filename"] = "finishing…"

                self.chunks = all_chunks
                self.skipped = skipped
                self.file_count = total
                self.lexical.build(all_chunks)
                self.translations.build(all_pairs)
            except Exception as exc:
                self.index_error = str(exc)
            finally:
                with self._lock:
                    self.indexing = False
                    self.index_progress = {
                        "current": 0,
                        "total": 0,
                        "filename": "",
                        "segments": 0,
                        "pairs": 0,
                    }
                locked = pop_locked_files()
                if locked:

                    def _retry() -> None:
                        self.build_index(log_fn=log_fn, on_done=on_done)

                    prompt_close_locked_files(locked, on_continue=_retry)
                elif on_done:
                    on_done()

        threading.Thread(target=_run, daemon=True).start()

    def search_lexical(
        self, query: str, *, branch: str = "", limit: int | None = None
    ):
        cfg = load_settings()
        lim = limit or int(cfg.get("max_chunks_returned") or 50)
        cutoff = float(cfg.get("score_cutoff") or 0.0)
        return self.lexical.search(
            query, branch=branch, limit=lim, score_cutoff=cutoff
        )


_session: WorkspaceSession | None = None


def get_workspace() -> WorkspaceSession:
    global _session
    if _session is None:
        _session = WorkspaceSession()
    return _session


def reset_workspace() -> WorkspaceSession:
    global _session
    _session = WorkspaceSession()
    return _session
