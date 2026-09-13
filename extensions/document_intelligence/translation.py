# -*- coding: utf-8 -*-
"""Bilingual translation pair extraction and lookup."""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass

from extensions.document_intelligence.index.lexical import LexicalIndex

_TRANSLATE_PATTERNS = re.compile(
    r"(?i)(translate|translation|interpret|how do you say|"
    r"翻译|翻譯|译成|譯成|翻译成|翻譯成|怎么说|怎麼說|的意思|"
    r"traducir|cómo se dice|übersetzen|wie sagt man)"
)


@dataclass
class TranslationPair:
    source_text: str
    target_text: str
    file_path: str = ""
    source_name: str = ""


def is_translation_query(query: str) -> bool:
    return bool(_TRANSLATE_PATTERNS.search((query or "").strip()))


def extract_phrase_for_translation(query: str) -> str:
    text = (query or "").strip()
    for pat in (
        r"(?i)translate\s+(.+)",
        r"翻译(.+)",
        r"翻譯(.+)",
        r"(.+?)的意思",
        r"(?i)how do you say\s+(.+)",
    ):
        m = re.search(pat, text)
        if m:
            return m.group(1).strip(" ?：:\"'")
    return text


def _detect_lang_pair(a: str, b: str) -> bool:
    if not a or not b or a.strip() == b.strip():
        return False
    latin_a = bool(re.search(r"[a-zA-Z]", a))
    latin_b = bool(re.search(r"[a-zA-Z]", b))
    cjk_a = bool(re.search(r"[\u4e00-\u9fff]", a))
    cjk_b = bool(re.search(r"[\u4e00-\u9fff]", b))
    if cjk_a and cjk_b:
        return a.strip() != b.strip()
    if latin_a and cjk_b:
        return True
    if latin_b and cjk_a:
        return True
    return a.strip().lower() != b.strip().lower()


def _split_bilingual_lines(a: str, b: str) -> list[tuple[str, str]]:
    """When both cells share line count, emit per-line sub-pairs."""
    a_lines = [ln.strip() for ln in a.splitlines() if ln.strip()]
    b_lines = [ln.strip() for ln in b.splitlines() if ln.strip()]
    if len(a_lines) >= 2 and len(a_lines) == len(b_lines):
        return list(zip(a_lines, b_lines))
    return [(a, b)]


def pairs_from_file(file_path: str, *, display_path: str = "") -> list[TranslationPair]:
    import os

    ext = os.path.splitext(file_path)[1].lower()
    source = os.path.splitext(os.path.basename(file_path))[0]
    raw: list[tuple[str, str]] = []

    if ext == ".txt":
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "|" in line:
                    parts = line.split("|", 1)
                    if len(parts) == 2:
                        raw.append((parts[0].strip(), parts[1].strip()))
    elif ext in (".docx", ".doc"):
        from extensions.document_intelligence.extract import open_docx_for_reading

        with open_docx_for_reading(file_path) as docx_path:
            if not docx_path:
                return []
            from docx import Document as DocxReader

            doc = DocxReader(docx_path)
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if len(cells) >= 2:
                        raw.append((cells[0], cells[1]))

    out: list[TranslationPair] = []
    disp = display_path or file_path
    for a, b in raw:
        for src, tgt in _split_bilingual_lines(a, b):
            if not _detect_lang_pair(src, tgt):
                continue
            try:
                from services.formslator.vault_alignment import INGEST_PAIR_MIN, translation_pair_score

                if translation_pair_score(src, tgt) < INGEST_PAIR_MIN:
                    continue
            except Exception:
                pass
            out.append(
                TranslationPair(
                    source_text=src,
                    target_text=tgt,
                    file_path=disp,
                    source_name=source,
                )
            )
    return out


class TranslationIndex:
    def __init__(self) -> None:
        self.pairs: list[TranslationPair] = []
        self._lexical = LexicalIndex()

    def build(self, pairs: list[TranslationPair]) -> None:
        self.pairs = list(pairs)
        from extensions.document_intelligence.corpus.types import ChunkRecord

        chunks = [
            ChunkRecord(
                chunk_id=f"t{i}",
                text=p.source_text,
                source=p.source_name or "translation",
                vault_path=p.file_path,
                file_path=p.file_path,
            )
            for i, p in enumerate(self.pairs)
        ]
        self._lexical.build(chunks)

    def lookup(self, phrase: str, *, limit: int = 5) -> list[TranslationPair]:
        phrase = (phrase or "").strip()
        if not phrase or not self.pairs:
            return []
        exact = [
            p
            for p in self.pairs
            if phrase == p.source_text or phrase in p.source_text
        ]
        if exact:
            return exact[:limit]
        hits = self._lexical.search(phrase, limit=limit, score_cutoff=0.0)
        out: list[TranslationPair] = []
        for h in hits:
            idx = next(
                (i for i, p in enumerate(self.pairs) if p.source_text == h.chunk.text),
                -1,
            )
            if idx >= 0:
                out.append(self.pairs[idx])
        return out[:limit]


def global_translation_index() -> TranslationIndex:
    """Merge translation pairs from workspace and all indexed catalogues."""
    return translation_index_for_scope("all")


_index_cache_lock = threading.Lock()
# Keyed by (scope, sorted library_ids); value is (fingerprint, TranslationIndex).
# Building a TranslationIndex means tokenizing every pair's source text and
# constructing a BM25Okapi index — cheap per-pair but adds up at tens of
# thousands of vault segments, and this function used to redo it from scratch
# on every single call (every Workspace document load, every reload triggered
# by a target-language or vault-cutoff change, every Translate/Format batch
# run). Gathering `pairs` below is comparatively cheap (in-memory iteration +
# dedup), so it still happens on every call; only the expensive build() is
# skipped when the underlying data hasn't changed.
_index_cache: dict[tuple[str, tuple[str, ...]], tuple[tuple[int, int], "TranslationIndex"]] = {}


def _pairs_fingerprint(pairs: list[TranslationPair]) -> tuple[int, int]:
    """Cheap (not perfect) change signal: pair count plus total character
    length. Catches the common case — a document was added to or removed from
    the vault — without hashing every pair's full text on every call. Editing
    an existing pair's text in place without changing the corpus's pair count
    or total length is the one case this won't detect; that's not how vault
    ingestion works today (pairs are added wholesale from ingested documents,
    never edited in place), so it's an acceptable trade for avoiding a much
    heavier fingerprint computation on every call."""
    return (len(pairs), sum(len(p.source_text) + len(p.target_text) for p in pairs))


def translation_index_for_scope(
    scope: str = "all",
    library_ids: list[str] | None = None,
) -> TranslationIndex:
    """Build translation index from workspace and/or selected catalogues.

    scope: "all" | "workspace" | "selected"
    library_ids: used when scope == "selected" (empty → workspace only)
    """
    from extensions.document_intelligence.corpus.library import get_library, list_libraries
    from extensions.document_intelligence.corpus.workspace import get_workspace

    pairs: list[TranslationPair] = []
    seen: set[tuple[str, str]] = set()

    def _add(src_pairs: list[TranslationPair]) -> None:
        for p in src_pairs:
            key = (p.source_text.strip(), p.target_text.strip())
            if key in seen:
                continue
            seen.add(key)
            pairs.append(p)

    _add(get_workspace().translations.pairs)

    scope_n = (scope or "all").strip().lower()
    if scope_n != "workspace":
        selected = {str(x) for x in (library_ids or []) if x}
        for lib in list_libraries():
            if not lib.lexical_ready:
                continue
            if scope_n == "selected" and lib.library_id not in selected:
                continue
            _add(get_library(lib.library_id).translations.pairs)

    cache_key = (scope_n, tuple(sorted(str(x) for x in (library_ids or []) if x)))
    fingerprint = _pairs_fingerprint(pairs)
    with _index_cache_lock:
        cached = _index_cache.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

    index = TranslationIndex()
    index.build(pairs)
    with _index_cache_lock:
        _index_cache[cache_key] = (fingerprint, index)
    return index


def clear_translation_index_cache() -> None:
    """Drop all cached indices, forcing the next translation_index_for_scope()
    call to rebuild from the current vault contents. Not currently wired to
    any ingestion event — the fingerprint check already invalidates
    automatically when a document is added to or removed from the vault
    (pair count/length changes); this exists for callers that want to force a
    rebuild explicitly (tests, or a future "reindex" action)."""
    with _index_cache_lock:
        _index_cache.clear()


def match_translation(
    index: TranslationIndex,
    phrase: str,
    *,
    min_score: float = 0.85,
) -> TranslationPair | None:
    """Best vault match for a phrase (exact, then lexical)."""
    text = (phrase or "").strip()
    if not text or not index.pairs:
        return None
    for pair in index.pairs:
        if pair.source_text.strip() == text:
            return pair
    for pair in index.pairs:
        src = pair.source_text.strip()
        if not src:
            continue
        if text in src or src in text:
            shorter = min(len(text), len(src))
            longer = max(len(text), len(src))
            if shorter / longer >= 0.9:
                return pair
    hits = index._lexical.search(text, limit=3, score_cutoff=min_score)
    for hit in hits:
        for pair in index.pairs:
            if pair.source_text.strip() == hit.chunk.text.strip():
                return pair
    return None


def format_translation_result(query: str, pairs: list[TranslationPair]) -> str:
    if not pairs:
        return (
            f"**Document Intelligence** (Translation)\n\n"
            f"No translation pair found for: {query}"
        )
    best = pairs[0]
    lines = [
        "**Document Intelligence** (Translation)",
        "",
        f"**Query:** {query}",
        "",
        f"**{best.source_text}** → **{best.target_text}**",
    ]
    if best.file_path:
        from extensions.document_intelligence.ui.path_links import format_file_path_links

        lines.extend(["", format_file_path_links(best.file_path)])
    if len(pairs) > 1:
        lines.append("")
        lines.append("Other close matches:")
        for p in pairs[1:4]:
            lines.append(f"- {p.source_text} → {p.target_text}")
    return "\n".join(lines)
