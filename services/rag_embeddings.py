# -*- coding: utf-8 -*-
"""Shared local embedding backend (E5-multilingual) for RAG-style retrieval — used by
Document Intelligence's Deep search mode, Formslator's vault alignment, and the intent
classifier's anchor embeddings. Not tied to any single extension."""
from __future__ import annotations

import logging
import os
import threading

_logger = logging.getLogger(__name__)

# intfloat/multilingual-e5-small (~470MB) rather than -base (~1.1GB) or -large (~2.2GB) —
# same E5 family and query:/passage: prefix convention, still covers every language LOMA's
# UI localizes into (en/zh/es/de) plus the rest of XLM-R's 100+, at a third of -base's size.
# Bundled locally under embedding-model/ (see packaging/loma_core.spec) so every shipped
# extension that needs it (Document Intelligence's Semantic enhancement / Deep search, the
# chat intent classifier) works with zero first-run download — this id is only the fallback
# for a dev checkout that hasn't populated that folder, or a stripped install missing it.
E5_HF_MODEL_ID = "intfloat/multilingual-e5-small"

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_PKG_DIR)

_LOCAL_CANDIDATES = (
    os.path.join(_PROJECT_ROOT, "embedding-model"),
    os.path.join(_PROJECT_ROOT, "reference", "SOPA", "embedding-model"),
    os.path.join(_PROJECT_ROOT, "data", "rag", "embedding-model"),
    os.path.join(_PROJECT_ROOT, "models", "embedding-model"),
)

_load_lock = threading.Lock()


def _is_sentence_transformer_dir(path: str) -> bool:
    return os.path.isdir(path) and os.path.isfile(os.path.join(path, "config.json"))


def resolve_e5_model_path() -> str:
    """Pick the first valid local E5 folder (bundled — see packaging/loma_core.spec), else
    fall back to downloading the standard HF model id."""
    for candidate in _LOCAL_CANDIDATES:
        if _is_sentence_transformer_dir(candidate):
            return candidate
    return E5_HF_MODEL_ID


class E5EmbeddingBackend:
    def __init__(self) -> None:
        self._model_path = resolve_e5_model_path()
        self._model = None

    @property
    def model_path(self) -> str:
        return self._model_path

    def _load(self):
        if self._model is not None:
            return self._model
        # Startup warmup and the first chat can race — without a lock both call
        # SentenceTransformer() and the UI appears to "reload" then hang.
        with _load_lock:
            if self._model is not None:
                return self._model
            from sentence_transformers import SentenceTransformer

            # self._model_path falls back to a bare HF Hub id (E5_HF_MODEL_ID) only when no
            # local embedding-model folder is found (see resolve_e5_model_path's
            # _LOCAL_CANDIDATES) — normally bundled (packaging/loma_core.spec), so this path
            # is just the dev-checkout-without-the-folder / stripped-install case. That
            # download can take a long time or hang outright on a slow/blocked connection,
            # and SentenceTransformer() gives no progress callback of its own, so log
            # before/after rather than leaving callers with zero signal while this blocks
            # (this used to be print(), which vanishes into devnull in a packaged
            # --windowed .exe — see main.py's log file setup).
            is_download = self._model_path == E5_HF_MODEL_ID
            if is_download:
                _logger.info(
                    "Loading embedding model %s — not found bundled locally, this may "
                    "download ~470MB on first use and take a while.",
                    self._model_path,
                )
            else:
                _logger.info("Loading embedding weights from %s…", self._model_path)
            self._model = SentenceTransformer(self._model_path)
            _logger.info("Embedding weights loaded.")
            return self._model

    def query_prefix(self, text: str) -> str:
        return f"query: {text.strip()}"

    def passage_prefix(self, text: str) -> str:
        return f"passage: {text.strip()}"

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        vec = model.encode([self.query_prefix(text)], normalize_embeddings=True)[0]
        return vec.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        prefixed = [self.passage_prefix(t) for t in texts]
        vecs = model.encode(prefixed, normalize_embeddings=True)
        return [v.tolist() for v in vecs]


_backend: E5EmbeddingBackend | None = None
_backend_lock = threading.Lock()


def get_embedding_backend(*, force_reload: bool = False) -> E5EmbeddingBackend:
    global _backend
    if _backend is not None and not force_reload:
        return _backend
    with _backend_lock:
        if _backend is not None and not force_reload:
            return _backend
        _backend = E5EmbeddingBackend()
        return _backend


def reset_embedding_cache() -> None:
    global _backend
    with _backend_lock:
        _backend = None


def rag_dependencies_available() -> tuple[bool, str]:
    try:
        import chromadb  # noqa: F401
    except ImportError:
        return False, "chromadb not installed — run: pip install -r requirements.txt"
    try:
        import sentence_transformers  # noqa: F401
    except ImportError as exc:
        detail = str(exc).strip() or type(exc).__name__
        if "torchvision" in detail.lower() or "nms" in detail.lower():
            return (
                False,
                "sentence-transformers import failed (torch/torchvision mismatch). "
                "Run: pip install -r requirements.txt",
            )
        return (
            False,
            f"sentence-transformers not available ({detail}) — "
            "run: pip install -r requirements.txt",
        )
    except Exception as exc:
        detail = str(exc).strip() or type(exc).__name__
        return (
            False,
            f"sentence-transformers import failed ({detail}) — "
            "run: pip install -r requirements.txt",
        )
    return True, ""
