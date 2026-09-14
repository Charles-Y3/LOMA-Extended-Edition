# -*- coding: utf-8 -*-
"""Persistent document libraries."""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import uuid
from typing import Callable

logger = logging.getLogger(__name__)

from extensions.knowledge_vault.corpus.types import (
    ChunkRecord,
    LibraryManifest,
    fully_excluded_file_keys,
)
from extensions.knowledge_vault.indexing_locks import pop_locked_files, prompt_close_locked_files
from extensions.knowledge_vault.translation import (
    TranslationIndex,
    append_unique_pairs,
    pairs_from_file,
)
from extensions.knowledge_vault.extract import (
    extract_file_chunks,
    list_document_files,
    vault_path_for,
)
from extensions.knowledge_vault.index.lexical import LexicalIndex
from extensions.knowledge_vault.settings import libraries_root

LogFn = Callable[[str], None]


def chunk_abs_path(ch: ChunkRecord) -> str:
    """Reconstruct the absolute source-file path from a ChunkRecord's display path
    (extract_file_chunks stores it relative to home when possible)."""
    p = (ch.file_path or "").strip().replace("\\", "/")
    if not p:
        return ""
    if os.path.isabs(p):
        return os.path.abspath(p)
    return os.path.abspath(os.path.join(os.path.expanduser("~"), p))


def _lib_dir(library_id: str) -> str:
    return os.path.join(libraries_root(), library_id)


def _manifest_path(library_id: str) -> str:
    return os.path.join(_lib_dir(library_id), "manifest.json")


def _db_path(library_id: str) -> str:
    return os.path.join(_lib_dir(library_id), "chunks.sqlite")


def _lexical_path(library_id: str) -> str:
    return os.path.join(_lib_dir(library_id), "lexical.pkl")


def semantic_dir(library_id: str) -> str:
    return os.path.join(_lib_dir(library_id), "semantic")


def list_libraries() -> list[LibraryManifest]:
    root = libraries_root()
    out: list[LibraryManifest] = []
    if not os.path.isdir(root):
        return out
    for name in sorted(os.listdir(root)):
        if os.path.isfile(os.path.join(root, name, "manifest.json")):
            out.append(load_manifest(name))
    return out


def load_manifest(library_id: str) -> LibraryManifest:
    path = _manifest_path(library_id)
    if not os.path.isfile(path):
        return LibraryManifest(library_id=library_id, name=library_id)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return LibraryManifest(
        library_id=library_id,
        name=str(raw.get("name") or library_id),
        roots=list(raw.get("roots") or []),
        lexical_ready=bool(raw.get("lexical_ready")),
        semantic_ready=bool(raw.get("semantic_ready")),
        semantic_building=bool(raw.get("semantic_building")),
        chunk_count=int(raw.get("chunk_count") or 0),
        file_count=int(raw.get("file_count") or 0),
        semantic_enabled=bool(raw.get("semantic_enabled")),
        excluded_paths=list(raw.get("excluded_paths") or []),
        retrieval_excluded_paths=list(raw.get("retrieval_excluded_paths") or []),
    )


def save_manifest(manifest: LibraryManifest) -> None:
    os.makedirs(_lib_dir(manifest.library_id), exist_ok=True)
    with open(_manifest_path(manifest.library_id), "w", encoding="utf-8") as f:
        json.dump(
            {
                "name": manifest.name,
                "roots": manifest.roots,
                "lexical_ready": manifest.lexical_ready,
                "semantic_ready": manifest.semantic_ready,
                "semantic_building": manifest.semantic_building,
                "chunk_count": manifest.chunk_count,
                "file_count": manifest.file_count,
                "semantic_enabled": manifest.semantic_enabled,
                "excluded_paths": manifest.excluded_paths,
                "retrieval_excluded_paths": manifest.retrieval_excluded_paths,
            },
            f,
            indent=2,
            ensure_ascii=False,
        )


def create_library(name: str, roots: list[str] | None = None) -> LibraryManifest:
    library_id = str(uuid.uuid4())[:8]
    manifest = LibraryManifest(
        library_id=library_id,
        name=name.strip() or library_id,
        roots=normalize_library_roots(roots or []),
    )
    os.makedirs(_lib_dir(library_id), exist_ok=True)
    save_manifest(manifest)
    return manifest


def _is_subpath(child: str, parent: str) -> bool:
    child_a = os.path.normcase(os.path.abspath(child))
    parent_a = os.path.normcase(os.path.abspath(parent.rstrip("/\\")))
    if child_a == parent_a:
        return False
    try:
        return os.path.commonpath([child_a, parent_a]) == parent_a
    except ValueError:
        return False


def normalize_library_roots(roots: list[str]) -> list[str]:
    abs_roots = sorted({os.path.abspath(r) for r in roots if r and os.path.isdir(r)})
    if not abs_roots:
        return []
    pruned: list[str] = []
    for r in abs_roots:
        if any(_is_subpath(r, p) for p in abs_roots if p != r):
            continue
        pruned.append(r)
    return pruned


def merge_library_roots(existing: list[str], new_path: str) -> list[str]:
    new_path = os.path.abspath(new_path)
    if not os.path.isdir(new_path):
        return normalize_library_roots(existing)
    roots = normalize_library_roots(existing)
    if not roots:
        return [new_path]
    if any(_is_subpath(new_path, r) for r in roots):
        return roots
    kept = [r for r in roots if not _is_subpath(r, new_path)]
    kept.append(new_path)
    return normalize_library_roots(kept)


def add_folder_to_library(library_id: str, folder_path: str) -> LibraryManifest:
    manifest = load_manifest(library_id)
    manifest.roots = merge_library_roots(manifest.roots, folder_path)
    save_manifest(manifest)
    corp = get_library(library_id)
    corp.manifest = manifest
    return manifest


def delete_library(library_id: str) -> None:
    _open_libraries.pop(library_id, None)
    lib_path = _lib_dir(library_id)
    if os.path.isdir(lib_path):
        shutil.rmtree(lib_path, ignore_errors=True)

    # Translation Vault entries are decoupled from any one catalogue — deleting a
    # catalogue only strips the sources it contributed, not the pairs themselves;
    # a pair vanishes only once every catalogue that produced it is gone (see
    # translation_vault_store.remove_library_from_vault).
    from extensions.knowledge_vault.translation_vault_store import remove_library_from_vault

    remove_library_from_vault(library_id)


def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS chunks (
            chunk_id TEXT PRIMARY KEY,
            text TEXT NOT NULL,
            source TEXT,
            vault_path TEXT,
            file_path TEXT,
            page_number INTEGER,
            section TEXT,
            segment_index INTEGER,
            ingest_root TEXT,
            is_low_content INTEGER DEFAULT 0
        )"""
    )
    try:
        conn.execute("ALTER TABLE chunks ADD COLUMN is_low_content INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass  # already has the column — table pre-dates this field
    conn.execute(
        """CREATE TABLE IF NOT EXISTS file_state (
            abs_path TEXT PRIMARY KEY,
            mtime REAL,
            size INTEGER
        )"""
    )
    conn.commit()


def _row_to_chunk(row: tuple) -> ChunkRecord:
    return ChunkRecord(
        chunk_id=row[0],
        text=row[1],
        source=row[2] or "",
        vault_path=row[3] or "",
        file_path=row[4] or "",
        page_number=row[5],
        section=row[6] or "General",
        segment_index=int(row[7] or 0),
        ingest_root=row[8] or "",
        is_low_content=bool(row[9]) if len(row) > 9 else False,
    )


def _load_chunks_from_db(library_id: str) -> list[ChunkRecord]:
    db = _db_path(library_id)
    if not os.path.isfile(db):
        return []
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute(
            "SELECT chunk_id, text, source, vault_path, file_path, page_number, "
            "section, segment_index, ingest_root, is_low_content FROM chunks"
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_chunk(r) for r in rows]


def _file_state(library_id: str) -> dict[str, tuple[float, int]]:
    db = _db_path(library_id)
    if not os.path.isfile(db):
        return {}
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT abs_path, mtime, size FROM file_state").fetchall()
    finally:
        conn.close()
    return {r[0]: (float(r[1]), int(r[2])) for r in rows}


def _persist_library(library_id: str, chunks: list[ChunkRecord], file_states: dict) -> None:
    os.makedirs(_lib_dir(library_id), exist_ok=True)
    conn = sqlite3.connect(_db_path(library_id))
    try:
        _init_db(conn)
        conn.execute("DELETE FROM chunks")
        conn.execute("DELETE FROM file_state")
        for ch in chunks:
            conn.execute(
                "INSERT INTO chunks VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    ch.chunk_id,
                    ch.text,
                    ch.source,
                    ch.vault_path,
                    ch.file_path,
                    ch.page_number,
                    ch.section,
                    ch.segment_index,
                    ch.ingest_root,
                    int(ch.is_low_content),
                ),
            )
        for abs_path, (mtime, size) in file_states.items():
            conn.execute(
                "INSERT INTO file_state VALUES (?,?,?)",
                (abs_path, mtime, size),
            )
        conn.commit()
    finally:
        conn.close()


class LibraryCorpus:
    def __init__(self, library_id: str) -> None:
        self.library_id = library_id
        self.manifest = load_manifest(library_id)
        self.chunks: list[ChunkRecord] = []
        # (ingest_root, vault_path, reason) for files scanned but not indexed —
        # locked, encrypted, unsupported .doc conversion, etc. In-memory only,
        # refreshed on each build_lexical_index() run.
        self.skipped: list[tuple[str, str, str]] = []
        self.lexical = LexicalIndex()
        self.translations = TranslationIndex()
        self.indexing = False
        self.semantic_building = self.manifest.semantic_building
        self.index_progress: dict[str, int | str] = {
            "current": 0,
            "total": 0,
            "filename": "",
            "segments": 0,
            "pairs": 0,
        }
        self._lock = threading.Lock()
        if self.manifest.lexical_ready and os.path.isfile(_lexical_path(library_id)):
            self.lexical.load(_lexical_path(library_id))
            self.chunks = list(self.lexical.chunks)
        tp = _translations_path(library_id)
        if os.path.isfile(tp):
            import pickle

            from extensions.knowledge_vault.translation import ensure_pair_id, ensure_reviewed

            with open(tp, "rb") as f:
                pairs = pickle.load(f)
            for p in pairs:
                ensure_pair_id(p)
                ensure_reviewed(p)
            self.translations.build(pairs)

    def save_translations(self) -> None:
        """Persist the current in-memory translation pairs back to disk — used by
        the Translation Vault tab's edit/delete actions (a normal re-index also
        rewrites this file wholesale, see build_lexical_index below)."""
        import pickle

        with open(_translations_path(self.library_id), "wb") as f:
            pickle.dump(self.translations.pairs, f, protocol=pickle.HIGHEST_PROTOCOL)

    def maybe_resume_semantic(self) -> None:
        m = load_manifest(self.library_id)
        if not m.semantic_enabled or m.semantic_ready:
            return
        if m.semantic_building:
            m.semantic_building = False
            save_manifest(m)
            self.manifest = m
        if not self.semantic_building:
            self.build_semantic_index()

    def indexed_tree(self) -> dict:
        from extensions.knowledge_vault.ui.fs_tree import build_index_tree

        if self.chunks or self.skipped:
            return build_index_tree(self.chunks, self.skipped)
        return {}

    def excluded_file_count(self) -> int:
        """Files fully excluded from retrieval — every chunk is_low_content, whether
        auto-tagged (e.g. cover page) or manually excluded via the tree menu. A file
        with only SOME low-content chunks (e.g. a stray short header fragment) is not
        counted here — matches the tree UI's per-file exclusion icon."""
        return len(fully_excluded_file_keys(self.chunks, chunk_abs_path))

    def delete_file(self, abs_path: str) -> bool:
        """Remove one file's chunks/embeddings from the catalogue and keep it out of
        future rescans (the file on disk is untouched)."""
        abs_path = os.path.abspath(abs_path)
        keep: list[ChunkRecord] = []
        removed_ids: list[str] = []
        for ch in self.chunks:
            if chunk_abs_path(ch) == abs_path:
                removed_ids.append(ch.chunk_id)
            else:
                keep.append(ch)
        if not removed_ids:
            return False
        self.chunks = keep
        self.lexical.build(self.chunks)
        self.lexical.save(_lexical_path(self.library_id))
        file_states = _file_state(self.library_id)
        file_states.pop(abs_path, None)
        _persist_library(self.library_id, self.chunks, file_states)
        self.manifest = load_manifest(self.library_id)
        self.manifest.chunk_count = len(self.chunks)
        self.manifest.file_count = max(0, self.manifest.file_count - 1)
        paths = set(self.manifest.excluded_paths)
        paths.add(abs_path)
        self.manifest.excluded_paths = sorted(paths)
        save_manifest(self.manifest)
        if self.manifest.semantic_ready or self.manifest.semantic_enabled:
            from extensions.knowledge_vault.index.semantic import delete_chunk_ids

            delete_chunk_ids(self.library_id, semantic_dir(self.library_id), removed_ids)
        return True

    def set_retrieval_excluded(self, abs_path: str, excluded: bool) -> bool:
        """Manually include/exclude a file from retrieval, independent of the
        automatic is_low_content tag (e.g. a low-quality OCR page the heuristic
        misses). Persists across index rebuilds via manifest.retrieval_excluded_paths."""
        abs_path = os.path.abspath(abs_path)
        changed_ids: list[str] = []
        for ch in self.chunks:
            if chunk_abs_path(ch) == abs_path:
                ch.is_low_content = excluded
                changed_ids.append(ch.chunk_id)
        if not changed_ids:
            return False
        self.lexical.build(self.chunks)
        self.lexical.save(_lexical_path(self.library_id))
        _persist_library(self.library_id, self.chunks, _file_state(self.library_id))
        self.manifest = load_manifest(self.library_id)
        paths = set(self.manifest.retrieval_excluded_paths)
        if excluded:
            paths.add(abs_path)
        else:
            paths.discard(abs_path)
        self.manifest.retrieval_excluded_paths = sorted(paths)
        save_manifest(self.manifest)
        if excluded and (self.manifest.semantic_ready or self.manifest.semantic_enabled):
            from extensions.knowledge_vault.index.semantic import delete_chunk_ids

            delete_chunk_ids(self.library_id, semantic_dir(self.library_id), changed_ids)
        return True

    def build_lexical_index(
        self,
        *,
        log_fn: LogFn | None = None,
        on_done: Callable[[], None] | None = None,
    ) -> None:
        if self.indexing:
            return

        def _run():
            with self._lock:
                self.indexing = True
                self.index_progress = {
                    "current": 0,
                    "total": 0,
                    "filename": "scanning…",
                    "segments": 0,
                    "pairs": 0,
                    "error": "",
                }
            try:
                prior_state = _file_state(self.library_id)
                prior_chunks = _load_chunks_from_db(self.library_id)
                by_abs: dict[str, list[ChunkRecord]] = {}
                for ch in prior_chunks:
                    by_abs.setdefault(ch.file_path, []).append(ch)

                all_chunks: list[ChunkRecord] = []
                all_pairs = []
                pair_keys: set[frozenset[str]] = set()
                skipped: list[tuple[str, str, str]] = []
                file_states: dict[str, tuple[float, int]] = {}
                seen: set[str] = set()
                excluded = set(self.manifest.excluded_paths)
                retrieval_excluded = set(self.manifest.retrieval_excluded_paths)
                paths: list[tuple[str, str]] = []
                for root in self.manifest.roots:
                    for path in list_document_files(root):
                        if os.path.abspath(path) in excluded:
                            continue
                        paths.append((root, path))

                total = len(paths)
                with self._lock:
                    self.index_progress["total"] = total
                    self.index_progress["filename"] = (
                        "scanning…" if total else "no documents found"
                    )

                # Legacy .doc files each cost a full Word/LibreOffice cold start to
                # convert to .docx — batch-convert every one that actually needs
                # (re-)extraction through a single warmed-up instance up front,
                # instead of paying that cold-start cost per file during the loop
                # below (see extract.prime_doc_conversion_cache's docstring).
                doc_paths_to_prime = []
                for _root, path in paths:
                    if os.path.splitext(path)[1].lower() != ".doc":
                        continue
                    abs_path = os.path.abspath(path)
                    old = prior_state.get(abs_path)
                    try:
                        mtime = os.path.getmtime(path)
                        size = os.path.getsize(path)
                    except OSError:
                        continue
                    if old and abs(old[0] - mtime) < 0.01 and old[1] == size:
                        continue
                    doc_paths_to_prime.append(path)
                if doc_paths_to_prime:
                    from extensions.knowledge_vault.extract import (
                        prime_doc_conversion_cache,
                    )

                    if log_fn:
                        log_fn(f"Converting {len(doc_paths_to_prime)} legacy .doc file(s)…")
                    prime_doc_conversion_cache(doc_paths_to_prime, log_fn=log_fn)

                for idx, (root, path) in enumerate(paths):
                    basename = os.path.basename(path)
                    with self._lock:
                        self.index_progress["current"] = idx
                        self.index_progress["filename"] = basename
                        self.index_progress["segments"] = len(all_chunks)
                        self.index_progress["pairs"] = len(all_pairs)

                    abs_path = os.path.abspath(path)
                    seen.add(abs_path)
                    mtime = os.path.getmtime(path)
                    size = os.path.getsize(path)
                    file_states[abs_path] = (mtime, size)
                    old = prior_state.get(abs_path)
                    disp = _display_path(path)
                    vp = vault_path_for(path, root)
                    if old and abs(old[0] - mtime) < 0.01 and old[1] == size:
                        cached = by_abs.get(disp, [])
                        all_chunks.extend(cached)
                        if not cached:
                            skipped.append(
                                (root, vp, "No content indexed (unchanged since last scan)")
                            )
                    else:
                        last_reason = {"text": ""}

                        def _capture_log(msg: str, _last=last_reason) -> None:
                            _last["text"] = msg
                            if log_fn:
                                log_fn(msg)

                        new_chunks = extract_file_chunks(
                            path, ingest_root=root, log_fn=_capture_log
                        )
                        if abs_path in retrieval_excluded:
                            for ch in new_chunks:
                                ch.is_low_content = True
                        all_chunks.extend(new_chunks)
                        if not new_chunks:
                            skipped.append(
                                (root, vp, last_reason["text"] or "No content extracted")
                            )

                    # Extracted unconditionally (not only on the "changed" branch above) —
                    # unlike chunk extraction, which is cached by mtime/size because it can
                    # be expensive (OCR, .doc conversion), pairs_from_file() is a cheap
                    # re-read of one file's tables/lines. Gating it on the same cache used
                    # to skip chunk re-extraction meant every translation pair from an
                    # unchanged file silently vanished from translations.pkl on every
                    # incremental re-index — chunks stayed, but Translation Vault / Formslator's
                    # translation-reuse lost that file's pairs the moment nothing about it
                    # had changed since the last scan.
                    try:
                        pair_keys = append_unique_pairs(
                            all_pairs, pairs_from_file(path, display_path=disp), pair_keys
                        )
                    except (PermissionError, OSError):
                        note_locked_file(path)

                    with self._lock:
                        self.index_progress["segments"] = len(all_chunks)
                        self.index_progress["pairs"] = len(all_pairs)

                with self._lock:
                    self.index_progress["current"] = total
                    self.index_progress["filename"] = "saving index…"

                self.chunks = all_chunks
                self.skipped = skipped
                self.lexical.build(all_chunks)
                self.lexical.save(_lexical_path(self.library_id))
                self.translations.build(all_pairs)
                import pickle

                with open(_translations_path(self.library_id), "wb") as f:
                    pickle.dump(all_pairs, f, protocol=pickle.HIGHEST_PROTOCOL)

                from extensions.knowledge_vault.translation_vault_store import (
                    sync_library_into_vault,
                )

                sync_library_into_vault(self.library_id, all_pairs)
                _persist_library(self.library_id, all_chunks, file_states)
                self.manifest = load_manifest(self.library_id)
                self.manifest.lexical_ready = True
                self.manifest.chunk_count = len(all_chunks)
                self.manifest.file_count = len(seen)
                save_manifest(self.manifest)
            except Exception as exc:
                logger.exception("Index build failed for library %s", self.library_id)
                if log_fn:
                    log_fn(f"Index error: {exc}")
                with self._lock:
                    self.index_progress["error"] = str(exc)
            finally:
                with self._lock:
                    self.indexing = False
                    self.index_progress = {
                        "current": 0,
                        "total": 0,
                        "filename": "",
                        "segments": 0,
                        "pairs": 0,
                        "error": self.index_progress.get("error", ""),
                    }
                locked = pop_locked_files()
                if locked:

                    def _retry() -> None:
                        self.build_lexical_index(log_fn=log_fn, on_done=on_done)

                    prompt_close_locked_files(locked, on_continue=_retry)
                elif self.manifest.semantic_enabled:
                    # build_semantic_index's on_done takes the failure reason ("" on
                    # success) — this forwarded on_done is build_lexical_index's own
                    # zero-arg contract, so adapt rather than pass it straight through.
                    self.build_semantic_index(on_done=(lambda _error: on_done()) if on_done else None)
                elif on_done:
                    on_done()

        threading.Thread(target=_run, daemon=True).start()

    def build_semantic_index(
        self,
        *,
        log_fn: LogFn | None = None,
        on_done: Callable[[str], None] | None = None,
    ) -> None:
        """on_done receives "" on success, or the failure reason — previously a build
        failure (e.g. a chromadb/embedding-model load error specific to a packaged .exe)
        was only ever written via log_fn (logging.info, log-file-only), with zero UI
        signal — the "Semantic ..." chip just stayed stuck un-ticked forever with no
        indication anything had gone wrong."""
        if self.semantic_building or not self.manifest.lexical_ready:
            return

        def _run():
            with self._lock:
                self.semantic_building = True
            self.manifest.semantic_building = True
            self.manifest.semantic_enabled = True
            save_manifest(self.manifest)
            error = ""
            try:
                from extensions.knowledge_vault.index.semantic import build_semantic_index

                build_semantic_index(
                    self.library_id,
                    self.chunks,
                    persist_dir=semantic_dir(self.library_id),
                    log_fn=log_fn,
                )
                self.manifest = load_manifest(self.library_id)
                self.manifest.semantic_ready = True
                self.manifest.semantic_enabled = True
            except Exception as exc:
                error = str(exc) or exc.__class__.__name__
                logger.exception("Semantic build failed for library %s", self.library_id)
                if log_fn:
                    log_fn(f"Semantic build error: {exc}")
                self.manifest.semantic_ready = False
            finally:
                self.manifest = load_manifest(self.library_id)
                if self.chunks and _existing_semantic_count(self.library_id) >= len(self.chunks):
                    self.manifest.semantic_ready = True
                    error = ""
                self.manifest.semantic_building = False
                save_manifest(self.manifest)
                with self._lock:
                    self.semantic_building = False
                if not self.manifest.semantic_ready and not error:
                    error = "unknown error"
                if on_done:
                    on_done(error)

        threading.Thread(target=_run, daemon=True).start()


def _existing_semantic_count(library_id: str) -> int:
    from extensions.knowledge_vault.index.semantic import _existing_ids

    return len(_existing_ids(semantic_dir(library_id), library_id))


def resume_pending_semantic_builds() -> None:
    for lib in list_libraries():
        if lib.semantic_enabled and not lib.semantic_ready:
            get_library(lib.library_id).maybe_resume_semantic()


def _translations_path(library_id: str) -> str:
    return os.path.join(_lib_dir(library_id), "translations.pkl")


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


_open_libraries: dict[str, LibraryCorpus] = {}


def get_library(library_id: str) -> LibraryCorpus:
    if library_id not in _open_libraries:
        _open_libraries[library_id] = LibraryCorpus(library_id)
    return _open_libraries[library_id]
