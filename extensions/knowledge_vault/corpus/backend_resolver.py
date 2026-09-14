# -*- coding: utf-8 -*-
"""Resolve a single CorpusBackend for callers outside Knowledge Vault's own tabs
(e.g. the document editor's Ask LOMA highlight popup) from a scope + library choice."""
from __future__ import annotations

from extensions.knowledge_vault.corpus.library import get_library, list_libraries
from extensions.knowledge_vault.corpus.workspace import get_workspace
from extensions.knowledge_vault.retrieval.engine import CorpusBackend


class _MultiLexical:
    """Fans a search out to every sub-backend's own LexicalIndex and merges the
    results — real document chunks get a uuid4 chunk_id (extract.py), so merge_hits'
    chunk-id-keyed dedup is safe across libraries (collision probability is
    negligible); it's only unsafe for handmade sequential ids like translation.py's
    "t0"/"t1" pairs, which never flow through here."""

    def __init__(self, backends: list[CorpusBackend]) -> None:
        self._backends = backends

    def search(self, query: str, *, branch: str = "", limit: int = 50, score_cutoff: float = 0.0):
        from extensions.knowledge_vault.index.hybrid import merge_hits

        all_hits = []
        for backend in self._backends:
            lexical = getattr(backend, "lexical", None)
            if lexical is not None:
                all_hits.extend(lexical.search(query, branch=branch, limit=limit, score_cutoff=score_cutoff))
        return merge_hits(all_hits, limit=limit)

    def term_is_significant(self, term: str) -> bool:
        # A term counts as significant if ANY sub-corpus's own IDF-based judgment
        # says so — a term that's common filler in one small library shouldn't
        # suppress highlighting a real content word just because that library
        # happens to be one of several being searched together.
        return any(
            getattr(getattr(b, "lexical", None), "term_is_significant", lambda _t: True)(term)
            for b in self._backends
        )


class MultiCorpusBackend:
    """Real "search every catalogue" backend for scope="all" — merges the workspace
    and every ready library into one CorpusBackend, rather than the old behavior of
    silently picking just one of them. `.chunks` concatenates every sub-backend's
    chunks (safe for context_builder.py's neighbor-expansion, which scopes by
    file_path before ever comparing chunk_id). `.indexed_tree()` is a no-op — nothing
    on this multi-catalogue path (retrieve()/run_ask/run_search/run_analyze/run_agent)
    calls it; only Knowledge Vault's own tab's tree-view UI does, which never
    resolves a backend through here."""

    def __init__(self, backends: list[CorpusBackend]) -> None:
        self._backends = [b for b in backends if b is not None]
        self.lexical = _MultiLexical(self._backends)

    @property
    def chunks(self) -> list:
        out: list = []
        for b in self._backends:
            out.extend(getattr(b, "chunks", None) or [])
        return out

    def indexed_tree(self) -> dict:
        return {}


def has_any_kv_data() -> bool:
    """True if there's anything at all to search — an indexed workspace session or
    at least one ready library. Callers outside Knowledge Vault's own tabs (e.g. the
    document editor's Ask LOMA highlight popup) use this to decide whether the "use
    Knowledge Vault" checkbox should even be shown — with nothing indexed, offering
    it just invites a confusing zero-hit search."""
    if get_workspace().lexical.chunks:
        return True
    return any(lib.lexical_ready for lib in list_libraries())


def resolve_kv_backend(
    scope: str, library_id: str | None, *, include_workspace: bool = True
) -> CorpusBackend | None:
    """scope: "all" | "workspace" | "selected" — must behave exactly like the Ask
    LOMA popup's own catalogue dropdown promises: "selected" searches only that one
    library, "all" searches every catalogue (workspace + every ready library), not
    an arbitrary single one of them.

    include_workspace: False keeps the current session's own documents out of an
    "all"-scope result — callers outside Knowledge Vault's own tabs that no longer
    offer "workspace" as a selectable scope (it was removed as an option, not just
    hidden) pass this so "all catalogues" can't silently fall back to searching
    session content instead."""
    scope_n = (scope or "all").strip().lower()

    if scope_n == "selected":
        if not library_id:
            return None
        return get_library(library_id)

    if scope_n == "workspace":
        return get_workspace() if include_workspace else None

    backends: list[CorpusBackend] = []
    workspace = get_workspace()
    if include_workspace and workspace.lexical.chunks:
        backends.append(workspace)
    for lib in list_libraries():
        if lib.lexical_ready:
            backends.append(get_library(lib.library_id))
    if not backends:
        return None
    if len(backends) == 1:
        return backends[0]
    return MultiCorpusBackend(backends)
