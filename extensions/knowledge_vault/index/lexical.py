# -*- coding: utf-8 -*-
"""BM25 lexical index with CJK partial matching."""
from __future__ import annotations

import pickle
import re
import os
from typing import Sequence

from extensions.knowledge_vault.corpus.types import ChunkRecord, HitRecord
from extensions.knowledge_vault.index.text_match import (
    _MIN_COVERAGE,
    cjk_bigrams as _cjk_bigrams,
    phrase_match_coverage,
)

# No hardcoded stopword list: BM25's own IDF naturally discounts words that
# appear in most documents of a corpus (down to an epsilon floor — see
# rank_bm25's _calc_idf), which handles "the"/"and"/"的"/"了" etc. as a
# byproduct of the scoring math itself, for whatever language the corpus is
# actually in, instead of via a small, incomplete, English/Chinese-only list.

# How many of the BM25-ranked chunks get the expensive phrase_match_coverage()
# treatment per search() call. BM25's get_scores() is vectorized and cheap even
# against tens of thousands of chunks; phrase_match_coverage() is not (CJK
# queries do an O(query_len^2) nested substring search per chunk) — running it
# against the whole corpus regardless of BM25 rank, as this used to do, is what
# makes search() slow at large vault sizes. Ranking by the already-computed
# BM25 score first and only scoring this many of the top candidates keeps
# quality intact (a real match will essentially always land in the top few
# hundred by shared-token overlap) while cutting the expensive step's cost from
# O(corpus size) to O(this constant). If the shortlist doesn't clear
# _MIN_COVERAGE, the unchanged fallback loop below still scans the entire
# corpus exhaustively, so nothing is silently missed either way.
_COVERAGE_CANDIDATE_CAP = 200


def tokenize(text: str) -> list[str]:
    """Index-time tokenizer: CJK runs get bigram-expanded (no whitespace word
    boundaries to rely on); everything else is \\w{2,} -- Unicode-aware, so
    Cyrillic/Greek/Arabic/Hebrew/Devanagari/Hangul/accented Latin all produce
    real tokens instead of the previous ASCII-only [a-z0-9]{3,}, which
    silently dropped every non-English, non-CJK language's text entirely."""
    clean = (text or "").lower().strip()
    raw = re.findall(r"[一-鿿]+|\w{2,}", clean, re.UNICODE)
    expanded: list[str] = []
    for w in raw:
        if re.fullmatch(r"[一-鿿]+", w):
            expanded.extend(_cjk_bigrams(w))
        else:
            expanded.append(w)
    seen: set[str] = set()
    out: list[str] = []
    for t in expanded:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _partial_score(query: str, text: str) -> float:
    q = (query or "").strip()
    if not q or not text:
        return 0.0
    if q in text:
        return 1.0
    terms = tokenize(q)
    if not terms:
        return 0.0
    text_lower = text.lower()
    hits = sum(1 for t in terms if t in text_lower)
    min_hits = max(2, (len(terms) + 2) // 3)
    if hits < min_hits:
        return 0.0
    ratio = hits / len(terms)
    if ratio < 0.45:
        return 0.0
    return ratio


def _display_branch_path(chunk: ChunkRecord) -> str:
    label = ""
    if chunk.ingest_root:
        label = os.path.basename(chunk.ingest_root.rstrip("/\\"))
    vp = chunk.vault_path.replace("\\", "/")
    if label and not vp.lower().startswith(label.lower() + "/"):
        if vp.lower() != label.lower():
            return f"{label}/{vp}" if vp else label
    return vp


def _branch_matches(display_path: str, branch: str) -> bool:
    branch = (branch or "").strip().replace("\\", "/").strip("/")
    if not branch:
        return True
    vp = display_path.replace("\\", "/")
    bl = branch.lower()
    vl = vp.lower()
    if vl == bl:
        return True
    if vl.startswith(bl + "/"):
        return True
    leaf = branch.split("/")[-1].lower()
    if leaf.endswith((".docx", ".pdf", ".txt")) and vl.endswith("/" + leaf):
        return True
    if leaf.endswith((".docx", ".pdf", ".txt")) and os.path.basename(vl) == leaf:
        return True
    folder = "/".join(vp.split("/")[:-1]) if "/" in vp else ""
    return folder.lower() == bl


def branch_matches_chunk(chunk: ChunkRecord, branch: str) -> bool:
    if not branch:
        return True
    return _branch_matches(_display_branch_path(chunk), branch)


class LexicalIndex:
    def __init__(self) -> None:
        self.chunks: list[ChunkRecord] = []
        self._bm25 = None
        self._corpus_tokens: list[list[str]] = []

    def build(self, chunks: Sequence[ChunkRecord]) -> None:
        self.chunks = list(chunks)
        self._corpus_tokens = [tokenize(c.text) for c in self.chunks]
        # BM25Okapi divides idf_sum by the corpus's unique-word count in its own
        # __init__ (rank_bm25._calc_idf) — if every chunk tokenizes to zero words
        # (e.g. a workspace of short numeric/stopword-only chunks: slide titles,
        # TOC pages, dates), that count is 0 and construction raises
        # ZeroDivisionError, which build_index() then surfaces as a bare
        # "Error: division by zero". Skip BM25 in that case; search() already
        # falls back to _partial_score/phrase matching when self._bm25 is None.
        if not self._corpus_tokens or not any(self._corpus_tokens):
            self._bm25 = None
            return
        from rank_bm25 import BM25Okapi

        self._bm25 = BM25Okapi(self._corpus_tokens)

    def term_is_significant(self, term: str) -> bool:
        """False for a functional/common word in *this* corpus (the "and"/
        "its" standalone-highlight problem) — decided from the BM25 index's
        own per-term IDF instead of a hardcoded per-language stopword list,
        so it works the same way for any language the corpus is actually in.
        A word that appears in nearly every chunk gets a low (or epsilon-
        floored) IDF regardless of what language it's in; a distinctive
        content word or proper noun gets a high one."""
        term = (term or "").strip().lower()
        if not term or self._bm25 is None:
            return True
        idf = self._bm25.idf.get(term)
        if idf is None:
            return True
        avg = getattr(self._bm25, "average_idf", None)
        if not avg:
            return True
        return idf > avg * 0.3

    def search(
        self,
        query: str,
        *,
        branch: str = "",
        limit: int = 50,
        score_cutoff: float = 0.0,
    ) -> list[HitRecord]:
        if not self.chunks:
            return []
        ranked: list[HitRecord] = []
        scored: list[tuple[float, float, HitRecord]] = []
        if self._bm25:
            q_tokens = tokenize(query)
            if q_tokens:
                scores = self._bm25.get_scores(q_tokens)
                # Cheap first: branch-filter, then rank by the BM25 score already
                # computed above, then cap — only this shortlist pays for
                # phrase_match_coverage() below (see _COVERAGE_CANDIDATE_CAP).
                candidate_indices = [
                    i for i in range(len(scores)) if branch_matches_chunk(self.chunks[i], branch)
                ]
                candidate_indices.sort(key=lambda i: scores[i], reverse=True)
                candidate_indices = candidate_indices[:_COVERAGE_CANDIDATE_CAP]
                for idx in candidate_indices:
                    chunk = self.chunks[idx]
                    text = chunk.text
                    coverage = phrase_match_coverage(
                        query, text, significant=self.term_is_significant
                    )
                    if coverage < _MIN_COVERAGE:
                        continue
                    bm25 = float(scores[idx])
                    snippet = text[:280] + ("…" if len(text) > 280 else "")
                    scored.append(
                        (coverage, bm25, HitRecord(chunk=chunk, score=coverage, snippet=snippet))
                    )

        if not scored:
            for chunk in self.chunks:
                if not branch_matches_chunk(chunk, branch):
                    continue
                text = chunk.text
                coverage = phrase_match_coverage(
                    query, text, significant=self.term_is_significant
                )
                if coverage < _MIN_COVERAGE:
                    ps = _partial_score(query, text)
                    if ps <= 0:
                        continue
                    coverage = ps * 0.5
                if coverage < _MIN_COVERAGE:
                    continue
                snippet = text[:280] + ("…" if len(text) > 280 else "")
                scored.append((coverage, 0.0, HitRecord(chunk=chunk, score=coverage, snippet=snippet)))

        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        ranked = [hit for _, _, hit in scored]
        # score_cutoff is compared against `h.score` (phrase coverage, 0-1) —
        # correct, consistent units. (A previous version of this method also
        # compared the *raw* BM25 magnitude against this same 0-1 setting
        # inside the loop above — wrong units, since BM25 scores are
        # unbounded; that comparison has been removed. This coverage-based
        # cutoff is also relied on directly by translation.py's
        # match_translation, independent of the engine.py retrieval path.)
        if score_cutoff > 0:
            ranked = [h for h in ranked if h.score >= min(score_cutoff, 1.0)]
        return ranked[:limit]

    def save(self, path: str) -> None:
        with open(path, "wb") as f:
            pickle.dump(
                {"chunks": self.chunks, "corpus_tokens": self._corpus_tokens},
                f,
                protocol=pickle.HIGHEST_PROTOCOL,
            )

    def load(self, path: str) -> None:
        with open(path, "rb") as f:
            data = pickle.load(f)
        self.chunks = data.get("chunks") or []
        self._corpus_tokens = data.get("corpus_tokens") or []
        if self._corpus_tokens and any(self._corpus_tokens):
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self._corpus_tokens)
        else:
            self._bm25 = None
