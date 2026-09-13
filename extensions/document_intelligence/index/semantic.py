# -*- coding: utf-8 -*-
"""Optional semantic vector index per library."""
from __future__ import annotations

import os
from typing import Callable, Sequence

from extensions.document_intelligence.corpus.types import ChunkRecord, HitRecord

LogFn = Callable[[str], None]


def _deps_ok() -> tuple[bool, str]:
    try:
        from services.rag_embeddings import rag_dependencies_available

        return rag_dependencies_available()
    except Exception as exc:
        return False, str(exc)


def _existing_ids(persist_dir: str, library_id: str) -> set[str]:
    if not os.path.isdir(persist_dir):
        return set()
    try:
        from langchain_chroma import Chroma

        from services.rag_embeddings import get_embedding_backend

        class _Adapter:
            def __init__(self):
                self._b = get_embedding_backend()

            def embed_documents(self, texts):
                return self._b.embed_documents(texts)

            def embed_query(self, text):
                return self._b.embed_query(text)

        db = Chroma(
            collection_name=f"doc_intel_{library_id}",
            persist_directory=persist_dir,
            embedding_function=_Adapter(),
            collection_metadata={"hnsw:space": "cosine"},
        )
        data = db.get()
        return set(data.get("ids") or [])
    except Exception:
        return set()


def delete_chunk_ids(library_id: str, persist_dir: str, chunk_ids: Sequence[str]) -> None:
    """Remove specific chunk ids from a library's Chroma collection — used when a file
    is deleted from the catalogue or manually excluded from retrieval, so stale
    embeddings don't keep surfacing until the next full semantic rebuild."""
    if not chunk_ids or not os.path.isdir(persist_dir):
        return
    try:
        from langchain_chroma import Chroma

        from services.rag_embeddings import get_embedding_backend

        class _Adapter:
            def __init__(self):
                self._b = get_embedding_backend()

            def embed_documents(self, texts):
                return self._b.embed_documents(texts)

            def embed_query(self, text):
                return self._b.embed_query(text)

        db = Chroma(
            collection_name=f"doc_intel_{library_id}",
            persist_directory=persist_dir,
            embedding_function=_Adapter(),
            collection_metadata={"hnsw:space": "cosine"},
        )
        db.delete(ids=list(chunk_ids))
    except Exception:
        pass


def build_semantic_index(
    library_id: str,
    chunks: Sequence[ChunkRecord],
    *,
    persist_dir: str,
    log_fn: LogFn | None = None,
) -> None:
    ok, msg = _deps_ok()
    if not ok:
        raise RuntimeError(msg)
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    from services.rag_embeddings import E5_HF_MODEL_ID, get_embedding_backend, resolve_e5_model_path

    if log_fn and resolve_e5_model_path() == E5_HF_MODEL_ID:
        # No embedding model found bundled locally (normally shipped — see
        # packaging/loma_core.spec) — the first embed call below blocks on a ~470MB
        # Hugging Face download with no progress callback of its own. Without this, the
        # UI shows "Semantic ..." with no further sign of life for as long as that takes
        # (or hangs, on a slow/blocked connection) — see services/rag_embeddings.py.
        log_fn("Embedding model not found bundled — downloading it now (~470MB), this can take a while…")

    os.makedirs(persist_dir, exist_ok=True)
    already = _existing_ids(persist_dir, library_id)
    docs: list[Document] = []
    ids: list[str] = []
    for ch in chunks:
        if ch.chunk_id in already:
            continue
        if ch.is_low_content:
            # Cover pages / near-empty chunks carry no analyzable content — a bare
            # title or filename match trivially wins similarity search (high score,
            # nothing usable), so skip embedding them rather than filter post-hoc.
            continue
        meta = {
            "source": ch.source,
            "vault_path": ch.vault_path,
            "file_location": ch.file_path,
            "page_number": ch.page_number or 0,
            "section_title": ch.section,
            "chunk_id": ch.chunk_id,
            "library_id": library_id,
        }
        docs.append(Document(page_content=ch.text, metadata=meta))
        ids.append(ch.chunk_id)

    class _Adapter:
        def __init__(self):
            self._b = get_embedding_backend()

        def embed_documents(self, texts):
            return self._b.embed_documents(texts)

        def embed_query(self, text):
            return self._b.embed_query(text)

    adapter = _Adapter()
    db = Chroma(
        collection_name=f"doc_intel_{library_id}",
        persist_directory=persist_dir,
        embedding_function=adapter,
        # Embeddings are L2-normalized (see E5EmbeddingBackend.embed_documents) — without
        # this, Chroma defaults to "l2" space, and LangChain's relevance-score formula picks
        # its normalization based on the declared space, so scores come back uncalibrated
        # relative to true cosine similarity. Only affects newly-created collections; an
        # existing one keeps whatever space it was created with.
        collection_metadata={"hnsw:space": "cosine"},
    )
    if docs:
        db.add_documents(docs, ids=ids)
    if log_fn:
        total = len(already) + len(docs)
        log_fn(f"Semantic index: {total} segments embedded ({len(docs)} new).")


def semantic_search(
    library_id: str,
    query: str,
    *,
    persist_dir: str,
    branch: str = "",
    limit: int = 50,
) -> list[HitRecord]:
    ok, msg = _deps_ok()
    if not ok:
        return []
    from langchain_chroma import Chroma

    from services.rag_embeddings import get_embedding_backend
    from extensions.document_intelligence.index.lexical import branch_matches_chunk

    if not os.path.isdir(persist_dir):
        return []

    class _Adapter:
        def __init__(self):
            self._b = get_embedding_backend()

        def embed_documents(self, texts):
            return self._b.embed_documents(texts)

        def embed_query(self, text):
            return self._b.embed_query(text)

    db = Chroma(
        collection_name=f"doc_intel_{library_id}",
        persist_directory=persist_dir,
        embedding_function=_Adapter(),
        collection_metadata={"hnsw:space": "cosine"},
    )
    # Do NOT prefix with "query: " here — the adapter's embed_query() (via
    # services.rag_embeddings.E5EmbeddingBackend.query_prefix) already adds it. Passing an
    # already-prefixed string double-prefixed every semantic search ("query: query: ...."),
    # which is not the format the E5 model was trained on and degraded ranking quality.
    results = db.similarity_search_with_relevance_scores(
        query.strip(),
        k=limit * 3,
    )
    hits: list[HitRecord] = []
    for doc, score in results:
        meta = doc.metadata or {}
        ch = ChunkRecord(
            chunk_id=str(meta.get("chunk_id") or ""),
            text=doc.page_content,
            source=str(meta.get("source") or ""),
            vault_path=str(meta.get("vault_path") or ""),
            file_path=str(meta.get("file_location") or ""),
            page_number=int(meta.get("page_number") or 0) or None,
            section=str(meta.get("section_title") or "General"),
        )
        if not branch_matches_chunk(ch, branch):
            continue
        hits.append(
            HitRecord(
                chunk=ch,
                score=float(score),
                snippet=doc.page_content[:280],
            )
        )
        if len(hits) >= limit:
            break
    return hits
