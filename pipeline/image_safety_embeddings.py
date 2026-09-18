# -*- coding: utf-8 -*-
"""Embedding-based explicit-content gate for image generation/edit prompts.

Second line of defense behind image_prompt_author's own instruction to omit
explicit content (pipeline/direct/task_roles.py) — catches a prompt the
authoring LLM was talked into writing anyway, and covers the image-edit/
composite paths (pipeline/direct/step_executor.py's _run_image_mutation /
_run_image_composite), which send the user's own instruction straight to the
model with no authoring step in between at all.

Reuses the same multilingual E5 model already loaded for
pipeline/intent_embeddings.py (see services/rag_embeddings.py), so this adds
no new dependency or model download.

Uses a single absolute similarity threshold against the explicit anchors, not a
margin against a competing benign-anchor set — a margin approach was tried first
(to keep swimwear/kissing/breastfeeding/medical content from tripping the same
net) but proved actively wrong on real test content: a sentence describing actual
"graphic sexual intercourse" between "a couple" scored HIGHER on the benign "a
couple kissing romantically" anchor than genuinely benign content did, giving it
a NEGATIVE margin and letting real explicit content through, purely because it
mentioned a couple. A 42-case sweep (short/long, benign/explicit/adjacent, 4
languages) found a clean absolute-threshold window (0.82 too low, 0.88 too high)
with zero false positives or negatives — see the sweep in this module's test
history for the actual numbers before changing this value.

This check fails open (lets the request through) whenever embeddings are
unavailable, since it is a supplementary check behind image_prompt_author's own
instruction, not the only gate — unavailability must never block generation.
"""
from __future__ import annotations

import re

_EXPLICIT_ANCHORS = (
    "explicit sexual intercourse",
    "pornographic image",
    "genitalia exposed in a sexual act",
    "explicit fetish or bondage content",
    "露骨的性行為",
    "色情圖片",
    "露骨的性内容",
    "色情图片",
    "relaciones sexuales explícitas",
    "imagen pornográfica",
    "genitales expuestos en un acto sexual",
    "contenido fetichista o de bondage explícito",
    "explizite sexuelle Handlungen",
    "pornografisches Bild",
    "Genitalien in einer sexuellen Handlung sichtbar",
    "explizite Fetisch- oder Bondage-Inhalte",
)
# No nudity/nakedness-root anchors here on purpose ("graphic nudity...", "裸露的性
# 器官", "desnudez...", "Nacktheit...") — confirmed a real problem keeping them:
# the literal nude/nudity token dominates the embedding regardless of context, so
# "nude lipstick" and "nude-colored heels" scored just as close to these as actual
# nudity did. Bare nudity ("a nude woman", "a naked man") is instead handled by
# the separate, precise _has_bare_nudity_words() proximity check below, which
# disambiguates color/product usage from an actual body by word distance instead
# of relying on embedding similarity to tell them apart.

# See module docstring for why there's no competing benign-anchor set here.
_MIN_EXPLICIT_SCORE = 0.85

_explicit_vectors: list[list[float]] | None = None


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


# Deterministic backstop for blatant nudity words, alongside the embedding check
# above — confirmed a real gap: "a nude woman lying on a golden sandy beach" scores
# ABOVE the swimsuit-at-the-beach benign anchor once elaborate scene-setting prose
# surrounds it, because scenery vocabulary (beach/sunset) dominates the sentence
# embedding more than a single word does. A plain keyword hit doesn't have this
# dilution problem — but a bare co-occurrence check does have a DIFFERENT one,
# confirmed: "a woman wearing nude-colored high heels" contains both "nude" and
# "woman" anywhere in the sentence despite "nude" modifying the heels, not the
# woman. Requiring the person-word within a small WORD WINDOW of the nudity word
# (not just "somewhere in the same sentence") fixes that — "nude" and "woman" sit
# right next to each other when actually describing a body, but far apart when
# "nude" is really describing a color/product elsewhere in the sentence.
_NUDITY_WORD_RE = re.compile(r"\b(nude|naked)\b|裸(?:體|体)?|desnud[oa]|nackt", re.IGNORECASE)
# Includes art-depiction nouns (painting, portrait, photo, ...) alongside body
# words — "a nude painting" describes a naked figure just as much as "a nude
# woman" does, even though "nude" grammatically modifies the artwork, not a
# person-word directly.
_PERSON_WORD_RE = re.compile(
    r"\b(woman|man|person|girl|boy|figure|body|human|lady|guy|people|couple|model|"
    r"painting|portrait|photo|photograph|picture|sculpture|statue|drawing|artwork)\b"
    r"|人|女|男|身體|身体|畫|画|肖像|照片|雕像|素描"
    r"|persona|mujer|hombre|cuerpo|chica|chico|pintura|retrato|foto|escultura|dibujo"
    r"|Person|Frau|Mann|Körper|Gemälde|Porträt|Foto|Skulptur|Zeichnung",
    re.IGNORECASE,
)
_NUDITY_PROXIMITY_WORDS = 3


def _has_bare_nudity_words(text: str) -> bool:
    words = re.findall(r"\S+", text)
    # A hyphenated compound ("nude-colored", "nude-toned") is a color/style
    # modifier, not a body — exclude it rather than let it match as a bare word.
    nudity_idx = [
        i for i, w in enumerate(words) if "-" not in w and _NUDITY_WORD_RE.search(w)
    ]
    if not nudity_idx:
        return False
    person_idx = [i for i, w in enumerate(words) if _PERSON_WORD_RE.search(w)]
    if not person_idx:
        return False
    return any(
        abs(ni - pi) <= _NUDITY_PROXIMITY_WORDS for ni in nudity_idx for pi in person_idx
    )


def _load_vectors() -> list[list[float]]:
    global _explicit_vectors
    if _explicit_vectors is not None:
        return _explicit_vectors
    from services.rag_embeddings import get_embedding_backend

    backend = get_embedding_backend()
    _explicit_vectors = backend.embed_documents(list(_EXPLICIT_ANCHORS))
    return _explicit_vectors


def warm_image_safety_classifier() -> None:
    """Pre-load the E5 model and anchor vectors so the first real check doesn't pay
    that cost. Safe to call unconditionally — no-op if deps aren't installed."""
    if not _deps_available():
        return
    try:
        _load_vectors()
    except Exception:
        pass


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?。！？])\s+")


def _split_sentences(text: str) -> list[str]:
    """image_prompt_author writes full multi-sentence paragraphs (e.g. 4 sentences,
    ~60 words) — comparing that whole paragraph as one embedding against short
    anchor phrases is unreliable, confirmed: a completely benign 4-sentence prompt
    ('a boy playing basketball...') scored closest to the generic 'pornographic
    image' anchor purely because a long paragraph doesn't match ANY short phrase
    well, not because of its actual content. Scoring sentence-by-sentence keeps
    each comparison length-matched to the anchors, so one bad sentence still trips
    the check without a long benign paragraph diluting into a false match."""
    parts = [p.strip() for p in _SENTENCE_SPLIT_RE.split(text.strip()) if p.strip()]
    return parts or [text.strip()]


def _sentence_is_explicit(sentence: str, explicit_vectors: list) -> bool:
    from services.rag_embeddings import get_embedding_backend

    query_vec = get_embedding_backend().embed_query(sentence)
    return max(_cosine(query_vec, v) for v in explicit_vectors) >= _MIN_EXPLICIT_SCORE


def is_explicit_prompt(text: str) -> bool:
    """True when any single sentence in `text` scores above _MIN_EXPLICIT_SCORE
    against the explicit anchors — see _split_sentences for why this is scored
    per-sentence rather than on the whole text at once. Returns False (lets the
    request through) if embeddings are unavailable or the text is empty — this
    check is a supplementary net behind image_prompt_author's own instruction, not
    the only gate, so unavailability must never block generation outright."""
    stripped = (text or "").strip()
    if not stripped:
        return False
    if _has_bare_nudity_words(stripped):
        return True
    if not _deps_available():
        return False
    try:
        explicit_vectors = _load_vectors()
        return any(
            _sentence_is_explicit(sentence, explicit_vectors)
            for sentence in _split_sentences(stripped)
        )
    except Exception:
        return False

