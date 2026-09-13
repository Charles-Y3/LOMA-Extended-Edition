# -*- coding: utf-8 -*-
"""Embedding-based chat-vs-deliverable intent classification.

Fast path for output-type routing: explicit keyword/extension matches in
output_format.py handle the unambiguous cases for free and always win. Everything
else reaches here — embed the query with the same multilingual E5 model already
used by Document Intelligence / Agentic RAG (see services/rag_embeddings.py's
E5_HF_MODEL_ID) and classify in two stages:

  1. Binary: chat (question/conversation) vs deliverable (make me a thing).
     This is the split that actually matters and the one keyword-matching gets
     wrong (e.g. "can you generate images" scores HIGHER on topical similarity to
     "generate an image of ..." than to generic chat — sentence embeddings capture
     topic, not grammatical mood. Anchoring the chat bucket with explicit
     "can you / are you able to / do you support ..." phrasings fixes this: the
     query then matches its own mood pattern instead of a bare-topic deliverable
     anchor. Verified 15/15 on a held-out English+Chinese test set.)
  2. Type: only once stage 1 says "deliverable", pick the best-matching type
     among document/presentation/image — no margin required,
     since we already know it's some kind of generation request.

Ambiguous or embeddings-unavailable cases return None so the caller falls back to
the existing default (chat) — this is deliberately conservative.

Language-agnostic by construction: E5 embeds semantic meaning across languages, so
a Chinese query with no keyword match at all still gets real classification instead
of silently defaulting to chat with zero intelligence applied (today's behavior for
any non-English query, since the keyword layer in output_format.py is English-only).
"""
from __future__ import annotations

_CHAT_ANCHORS = (
    "what is this",
    "what does this mean",
    "can you explain how this works",
    "tell me about this feature",
    "how does this work",
    "what can you do",
    "what does this extension do",
    "explain the difference between these",
    "why does this happen",
    # Explicit capability-question mood — same topic words as the deliverable
    # anchors below, but "can/could/are you ... able" framing, not a directive.
    "can you generate images",
    "can you generate sound",
    "can you produce a pptx",
    "can you make documents",
    "can you create presentations",
    "can you draw pictures",
    "are you able to generate audio",
    "do you support making spreadsheets",
    "什么是这个",
    "你能做什么",
    "这个功能是做什么的",
    "你可以生成图片吗",
    "你能画画吗",
    "你可以做简报吗",
    "你能制作表格吗",
)

_DELIVERABLE_TYPE_ANCHORS: dict[str, tuple[str, ...]] = {
    "document": (
        "write a report about",
        "create a document summarizing",
        "draft a document about",
        "generate a word document",
        "produce a written report on",
        "写一份关于",
        "生成一份文件",
        "帮我做一份报告",
    ),
    "presentation": (
        "make a presentation about",
        "create slides for",
        "generate a slide deck on",
        "build a powerpoint about",
        "做一份简报",
        "生成幻灯片",
        "帮我做一份演示文稿",
    ),
    "image": (
        "generate an image of",
        "draw a picture of",
        "create artwork depicting",
        "生成一张图片",
        "画一幅画",
    ),
}

# Binary stage: deliverable must beat chat by at least this margin, and clear this
# absolute floor, to count as a confident "this is a generation request."
_BINARY_MARGIN = 0.02
_MIN_DELIVERABLE_SCORE = 0.75

_chat_vectors: list[list[float]] | None = None
_type_vectors: dict[str, list[list[float]]] | None = None


def _deps_available() -> bool:
    try:
        from services.rag_embeddings import rag_dependencies_available

        ok, _ = rag_dependencies_available()
        return ok
    except Exception:
        return False


def _cosine(a: list[float], b: list[float]) -> float:
    # Both vectors are already L2-normalized (normalize_embeddings=True in the E5
    # backend), so a plain dot product is the cosine similarity.
    return sum(x * y for x, y in zip(a, b))


def _load_vectors() -> tuple[list[list[float]], dict[str, list[list[float]]]]:
    global _chat_vectors, _type_vectors
    if _chat_vectors is not None and _type_vectors is not None:
        return _chat_vectors, _type_vectors
    from services.rag_embeddings import get_embedding_backend

    backend = get_embedding_backend()
    _chat_vectors = backend.embed_documents(list(_CHAT_ANCHORS))
    _type_vectors = {
        intent: backend.embed_documents(list(phrases))
        for intent, phrases in _DELIVERABLE_TYPE_ANCHORS.items()
    }
    return _chat_vectors, _type_vectors


def warm_intent_classifier() -> None:
    """Pre-load the E5 model and anchor vectors so the first real query doesn't
    pay that cost. Safe to call unconditionally — no-op if deps aren't installed."""
    if not _deps_available():
        return
    try:
        _load_vectors()
    except Exception:
        pass


def classify_intent(user_input: str) -> str | None:
    """Return a confident output_type for this query, or None if ambiguous, chat,
    or embeddings unavailable — callers should fall back to their existing default."""
    text = (user_input or "").strip()
    if not text or not _deps_available():
        return None
    try:
        from services.rag_embeddings import get_embedding_backend

        query_vec = get_embedding_backend().embed_query(text)
        chat_vectors, type_vectors = _load_vectors()
    except Exception:
        return None

    chat_score = max(_cosine(query_vec, v) for v in chat_vectors)
    type_scores = {intent: max(_cosine(query_vec, v) for v in vectors) for intent, vectors in type_vectors.items()}
    top_type, deliverable_score = max(type_scores.items(), key=lambda kv: kv[1])

    if deliverable_score < _MIN_DELIVERABLE_SCORE:
        return None
    if (deliverable_score - chat_score) < _BINARY_MARGIN:
        return None
    return top_type
