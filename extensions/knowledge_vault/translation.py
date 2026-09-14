# -*- coding: utf-8 -*-
"""Bilingual translation pair extraction and lookup."""
from __future__ import annotations

import re
import threading
import uuid
from dataclasses import dataclass, field

from extensions.knowledge_vault.index.lexical import LexicalIndex

_TRANSLATE_PATTERNS = re.compile(
    r"(?i)(translate|translation|interpret|how do you say|"
    r"翻译|翻譯|译成|譯成|翻译成|翻譯成|怎么说|怎麼說|的意思|"
    r"traducir|cómo se dice|übersetzen|wie sagt man)"
)

# Strips a leading "to/into <language>[:]" clause an instruction glues onto the phrase
# ("translate to english: 你好" -> "你好") — without this, extract_phrase_for_translation
# below returns "to english: 你好" and it can never exact/substring-match a cached pair.
_LANG_CLAUSE = re.compile(
    r"(?i)^(?:to|into|in)\s+(?:the\s+)?(?:traditional\s+|simplified\s+)?"
    r"(?:chinese|mandarin|cantonese|english|japanese|korean|french|spanish|german|"
    r"malay|italian|portuguese|vietnamese|indonesian)\b"
    r"|^(?:翻(?:譯|译)?成|成)(?:繁體中文|简体中文|中文|英文|日文|韓文|韩文|法文|德文|西班牙文)"
)
# Same clause, but glued onto the END of the phrase instead ("translate 你好 to
# english" already extracts fine via the leading-trigger pattern below, but
# "translate The True Essence of Life to chinese" would otherwise leave the
# trailing "to chinese" attached — never matches source_text OR target_text).
_TRAILING_LANG_CLAUSE = re.compile(
    r"(?i)\s+(?:to|into|in)\s+(?:the\s+)?(?:traditional\s+|simplified\s+)?"
    r"(?:chinese|mandarin|cantonese|english|japanese|korean|french|spanish|german|"
    r"malay|italian|portuguese|vietnamese|indonesian)\s*$"
    r"|(?:翻(?:譯|译)?成|成)(?:繁體中文|简体中文|中文|英文|日文|韓文|韩文|法文|德文|西班牙文)\s*$"
)
_LEADING_JUNK = re.compile(r'^[\s:：\-–—,，。]+')
_TRAILING_JUNK = re.compile(r'[\s:：\-–—,，。]+$')


@dataclass
class TranslationPair:
    source_text: str
    target_text: str
    file_path: str = ""
    source_name: str = ""
    # Stable identity for the Translation Vault tab's edit/delete actions — a plain
    # list index isn't stable across edits/deletes/rebuilds. Pairs unpickled from a
    # translations.pkl saved before this field existed won't have it set at all
    # (pickle restores only the attributes that were actually saved, dataclass
    # defaults don't apply on unpickling) — callers that load pairs from disk must
    # backfill via ensure_pair_id().
    pair_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # Manual "I looked at this flagged pair, it's fine" dismissal for the
    # Translation Vault tab's quality-review filter (see needs_review below) — not
    # a general audit flag, so it only matters for pairs needs_review() would
    # otherwise flag. Same pre-existing-pickle caveat as pair_id: a translations.pkl
    # saved before this field existed won't have it set at all, so callers loading
    # pairs from disk must backfill via ensure_reviewed().
    reviewed: bool = False


def ensure_pair_id(pair: "TranslationPair") -> None:
    """Backfill pair_id on a TranslationPair unpickled from a pre-pair_id
    translations.pkl (see the field's docstring above)."""
    if not getattr(pair, "pair_id", ""):
        pair.pair_id = uuid.uuid4().hex


def ensure_reviewed(pair: "TranslationPair") -> None:
    """Backfill reviewed on a TranslationPair unpickled from a pre-reviewed
    translations.pkl (see the field's docstring above)."""
    if not hasattr(pair, "reviewed"):
        pair.reviewed = False


_HAS_WORD_CHAR = re.compile(r"[A-Za-z一-鿿]")
# Leading punctuation that's junk regardless of what the other side looks like — no
# legitimate translation intentionally opens with a stray comma/closing-bracket/etc.
_LEADING_PLAIN_PUNCT = re.compile(r"^[,.;:!?，。；：！？\)\]\}”’\"']")
# Leading "enumeration marker" chars — ASCII digits, CJK numerals, and the ">"
# blockquote marker. Unlike plain punctuation above, these are only suspicious when
# they DON'T mirror a marker on the other side — see the asymmetry check below.
_CJK_NUMERALS = "一二三四五六七八九十百千零〇"
_LEADING_ENUM_MARKER = re.compile(rf"^[0-9{_CJK_NUMERALS}>]")


def needs_review(pair: "TranslationPair") -> bool:
    """Cheap, embedding-free heuristics flagging a pair worth a human look: an
    empty side, an untranslated pair (source == target), a translation with no
    actual letters (digits/symbols only — usually an extraction artifact), a stray
    leading punctuation/list-marker from a bad table-cell split, or a translation
    truncated down to a couple of characters against a full-sentence source.

    The leading-marker check is asymmetry-based, not "any leading digit is
    suspicious": a numbered-outline document ("一、人生的旅程" translated as
    "2. The journey of life") legitimately carries a leading marker on BOTH
    sides — the source in CJK numerals, the translation in Arabic digits — and
    that's a completely normal translation choice, not a defect (confirmed: an
    earlier version of this check flagged an entire numbered-chapter document's
    worth of correct translations for exactly this reason). A genuine extraction
    artifact — a stray leftover character from a bad table-cell split — almost
    always lands on only ONE side, the side where the split broke, with nothing
    matching on the other. So an enumeration-style marker (digit/CJK numeral/">")
    only counts as suspicious when it appears on exactly one side with no marker
    of any kind on the other; plain punctuation (a stray leading comma, closing
    bracket, etc.) has no such legitimate mirrored case and still flags
    unconditionally on either side.

    Deliberately conservative — this scans every pair in scope on each
    Translation Vault render (see render_translation_vault_tab's "needs review"
    filter and find_conflicting_pairs for the vault-wide duplicate check), so it
    stays to plain string checks rather than the semantic translation_pair_score
    used for a single edit's before/after comparison, and it favors few, sharp
    rules over a general quality score so it doesn't bury real issues under false
    positives (raw character-count ratios, for example, are NOT used here —
    zh/en pairs are routinely ~3x apart in length simply because Chinese
    characters carry more meaning per character, which would flag most normal
    pairs)."""
    src = (pair.source_text or "").strip()
    tgt = (pair.target_text or "").strip()
    if not src or not tgt:
        return True
    if src == tgt:
        return True
    if not _HAS_WORD_CHAR.search(tgt):
        return True
    if _LEADING_PLAIN_PUNCT.match(tgt) or _LEADING_PLAIN_PUNCT.match(src):
        return True
    if bool(_LEADING_ENUM_MARKER.match(src)) != bool(_LEADING_ENUM_MARKER.match(tgt)):
        return True
    long_side, short_side = (src, tgt) if len(src) >= len(tgt) else (tgt, src)
    if len(long_side) >= 15 and len(short_side) <= 2:
        return True
    return False


def find_conflicting_pairs(pairs: list["TranslationPair"]) -> set[str]:
    """pair_ids of pairs whose source or target text is also used (on either side)
    by another pair with a DIFFERENT counterpart — e.g. "前言"->"Foreword" and
    "前言"->"Preface" both in the vault: the same source phrase translated two
    different ways, so Format's vault prefill (search_vault_translation) has no
    principled way to choose between them and effectively picks one at random by
    alignment-score tiebreak. Can't be judged from a single pair in isolation like
    needs_review()'s checks, so this is a separate whole-scope pass rather than
    one more needs_review() rule — same swap-tolerance as _pair_dedup_key (a
    pair's source and target are interchangeable for matching purposes), so
    "Foreword"->"前言" elsewhere in the vault also conflicts with "前言"->"Preface"
    even though neither text sits in the same source/target slot."""
    text_to_keys: dict[str, set[frozenset[str]]] = {}
    for p in pairs:
        key = _pair_dedup_key(p)
        for text in (p.source_text.strip(), p.target_text.strip()):
            if text:
                text_to_keys.setdefault(text, set()).add(key)

    conflicting: set[str] = set()
    for p in pairs:
        for text in (p.source_text.strip(), p.target_text.strip()):
            if text and len(text_to_keys.get(text, ())) > 1:
                conflicting.add(p.pair_id)
                break
    return conflicting


def describe_languages(pair: "TranslationPair") -> str:
    """Coarse source->target language label for display (Translation Vault tab).
    Only distinguishes CJK vs Latin script — matches the granularity _detect_lang_pair
    already uses elsewhere in this module; a finer per-language detector isn't
    available here and isn't needed for a display label."""
    def _script(text: str) -> str:
        return "zh" if re.search(r"[一-鿿]", text or "") else "en"

    return f"{_script(pair.source_text)} → {_script(pair.target_text)}"


def is_translation_query(query: str) -> bool:
    return bool(_TRANSLATE_PATTERNS.search((query or "").strip()))


# Same vocabulary as _LANG_CLAUSE/_TRAILING_LANG_CLAUSE above, grouped into the
# two script families this module can actually tell apart (see describe_languages'
# docstring — no finer per-language detector is available). Used to reject a vault
# match whose target side is obviously the wrong family for what was asked (e.g.
# "translate Foreword to spanish" must not return the pair's Chinese side just
# because Foreword happens to be in the vault at all).
_LANG_FAMILY = {
    "chinese": "cjk", "mandarin": "cjk", "cantonese": "cjk",
    "japanese": "cjk", "korean": "cjk",
    "english": "latin_en",
    "french": "latin_other", "spanish": "latin_other", "german": "latin_other",
    "malay": "latin_other", "italian": "latin_other", "portuguese": "latin_other",
    "vietnamese": "latin_other", "indonesian": "latin_other",
}
_TARGET_LANG_WORD = re.compile(
    r"(?i)\b(?:to|into|in)\s+(?:the\s+)?(?:traditional\s+|simplified\s+)?"
    r"(chinese|mandarin|cantonese|english|japanese|korean|french|spanish|german|"
    r"malay|italian|portuguese|vietnamese|indonesian)\b"
)
_TARGET_LANG_CJK = re.compile(
    r"(?:翻(?:譯|译)?成|成)(繁體中文|简体中文|中文|英文|日文|韓文|韩文|法文|德文|西班牙文)"
)
_CJK_TARGET_WORD_MAP = {
    "繁體中文": "chinese", "简体中文": "chinese", "中文": "chinese",
    "英文": "english", "日文": "japanese", "韓文": "korean", "韩文": "korean",
    "法文": "french", "德文": "german", "西班牙文": "spanish",
}


def extract_target_language(query: str) -> str:
    """Best-effort target-language keyword ("chinese", "spanish", ...) explicitly
    named in a translate query — e.g. "translate X to spanish" -> "spanish" — or
    "" if the query doesn't name one at all ("translate X")."""
    text = query or ""
    m = _TARGET_LANG_WORD.search(text)
    if m:
        return m.group(1).lower()
    m = _TARGET_LANG_CJK.search(text)
    if m:
        return _CJK_TARGET_WORD_MAP.get(m.group(1), "")
    return ""


def target_language_matches(target_text: str, requested_lang: str) -> bool:
    """True unless the query named a specific target language AND the candidate
    pair's target side can't be the answer to it — e.g. an English/Chinese pair's
    Chinese side can't be the answer to "...to spanish", and (since this vault's
    Latin-script side is always English, per _detect_lang_pair/describe_languages)
    neither can its English side. Still coarse for the CJK bucket only (can't tell
    Japanese from Korean); the Latin bucket now distinguishes English specifically
    from every other Latin-script language instead of lumping them together."""
    if not requested_lang:
        return True
    family = _LANG_FAMILY.get(requested_lang)
    if not family:
        return True
    is_cjk = bool(re.search(r"[一-鿿぀-ヿ가-힯]", target_text or ""))
    if family == "cjk":
        return is_cjk
    if family == "latin_en":
        return not is_cjk
    return False


def _clean_phrase(phrase: str) -> str:
    phrase = phrase.strip(" ?：:\"'")
    phrase = _LANG_CLAUSE.sub("", phrase, count=1)
    phrase = _TRAILING_LANG_CLAUSE.sub("", phrase, count=1)
    phrase = _LEADING_JUNK.sub("", phrase)
    phrase = _TRAILING_JUNK.sub("", phrase)
    return phrase.strip(" ?：:\"'")


def extract_phrase_for_translation(query: str) -> str:
    text = (query or "").strip()
    if not text:
        return text

    # Trigger-word-first phrasing ("translate X", "X的意思", "how do you say X") —
    # the common case, and cheap to isolate directly via capture group.
    for pat in (
        r"(?i)translate\s+(.+)",
        r"翻译(.+)",
        r"翻譯(.+)",
        r"(.+?)的意思",
        r"(?i)how do you say\s+(.+)",
    ):
        m = re.search(pat, text)
        if m:
            phrase = _clean_phrase(m.group(1))
            if phrase:
                return phrase

    # Trigger word elsewhere in the string — trailing ("人生真諦: translate to
    # english", "人生真諦, 翻译") or otherwise not caught above. Strip every
    # trigger-word occurrence and any language clause; whatever remains is the
    # phrase. (The loop above can reach here with an empty `phrase` too — e.g.
    # "人生真諦: translate to english" matches "translate\s+(.+)" but captures
    # only "to english", which _clean_phrase reduces to "".)
    stripped = _TRANSLATE_PATTERNS.sub("", text)
    stripped = _clean_phrase(stripped)
    return stripped or text


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
        from extensions.knowledge_vault.extract import open_docx_for_reading

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
        from extensions.knowledge_vault.corpus.types import ChunkRecord

        # One chunk per side (source AND target), chunk_id-encoded as "t<pair index>s"/
        # "t<pair index>t" — so the lexical fallback below can match a query against
        # either side of a pair, not just source_text, same as the exact-match branch.
        chunks = []
        for i, p in enumerate(self.pairs):
            chunks.append(
                ChunkRecord(
                    chunk_id=f"t{i}s",
                    text=p.source_text,
                    source=p.source_name or "translation",
                    vault_path=p.file_path,
                    file_path=p.file_path,
                )
            )
            if p.target_text.strip() != p.source_text.strip():
                chunks.append(
                    ChunkRecord(
                        chunk_id=f"t{i}t",
                        text=p.target_text,
                        source=p.source_name or "translation",
                        vault_path=p.file_path,
                        file_path=p.file_path,
                    )
                )
        self._lexical.build(chunks)

    def lookup(self, phrase: str, *, limit: int = 5) -> list[TranslationPair]:
        """Bidirectional: a pair stored as source="前言"/target="Foreword" must be
        found by querying either "前言" or "Foreword" — _pair_dedup_key already
        treats the two sides as interchangeable for storage, this makes lookup
        match that. When the phrase matches the target side, the returned pair is
        swapped (source/target flipped) so callers (format_translation_result,
        the highlight-translate shortcut) always display query-language -> other
        language, not whatever happened to be the original ingest order."""
        phrase = (phrase or "").strip()
        if not phrase or not self.pairs:
            return []

        def _side_match(text: str) -> bool:
            return phrase == text or phrase in text or text in phrase

        def _oriented(p: TranslationPair) -> TranslationPair:
            if _side_match(p.source_text):
                return p
            return TranslationPair(
                source_text=p.target_text,
                target_text=p.source_text,
                file_path=p.file_path,
                source_name=p.source_name,
                pair_id=p.pair_id,
                reviewed=p.reviewed,
            )

        exact = [
            _oriented(p)
            for p in self.pairs
            if _side_match(p.source_text) or _side_match(p.target_text)
        ]
        if exact:
            return exact[:limit]
        hits = self._lexical.search(phrase, limit=limit, score_cutoff=0.0)
        out: list[TranslationPair] = []
        seen_idx: set[int] = set()
        for h in hits:
            # chunk_id is "t<pair index><side>" ("s"=source, "t"=target) from build()
            # above — decode it directly instead of re-matching by text, so a hit on
            # the target-side chunk still resolves to (and orients) the right pair.
            cid = h.chunk.chunk_id
            if not (cid.startswith("t") and cid[-1] in ("s", "t")):
                continue
            try:
                idx = int(cid[1:-1])
            except ValueError:
                continue
            if idx in seen_idx or not (0 <= idx < len(self.pairs)):
                continue
            seen_idx.add(idx)
            p = self.pairs[idx]
            if cid[-1] == "s":
                out.append(p)
            else:
                out.append(
                    TranslationPair(
                        source_text=p.target_text,
                        target_text=p.source_text,
                        file_path=p.file_path,
                        source_name=p.source_name,
                        pair_id=p.pair_id,
                        reviewed=p.reviewed,
                    )
                )
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
_index_cache: dict[
    tuple[str, tuple[str, ...], bool], tuple[tuple[int, int], "TranslationIndex"]
] = {}


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


def _pair_dedup_key(pair: "TranslationPair") -> frozenset[str]:
    """Order-independent identity for a translation pair — {source, target} rather
    than (source, target) — so a Chinese->English pair and the same words re-ingested
    as the mirrored English->Chinese pair (common when a bilingual document/table
    lists terms in both row orders, or gets ingested into more than one catalogue)
    collapse to a single entry instead of showing up twice in the Translation Vault.
    Language-agnostic: works for any script pair, not just zh/en, since it only
    compares the two text values against each other, never which one is "source"."""
    return frozenset({pair.source_text.strip(), pair.target_text.strip()})


def append_unique_pairs(
    pairs: list[TranslationPair],
    new_pairs: list[TranslationPair],
    seen: set[frozenset[str]] | None = None,
) -> set[frozenset[str]]:
    """Append new_pairs to pairs in place, skipping any whose _pair_dedup_key is
    already present in `pairs` or earlier in `new_pairs` — the ingest-time half of
    duplicate prevention (the Translation Vault tab's delete-by-key still handles
    duplicates already on disk from before this existed). Callers doing a
    multi-file re-index should keep reusing the returned `seen` set across calls
    so duplicates are caught across files, not just within one file's pairs."""
    if seen is None:
        seen = {_pair_dedup_key(p) for p in pairs}
    for p in new_pairs:
        key = _pair_dedup_key(p)
        if key in seen:
            continue
        seen.add(key)
        pairs.append(p)
    return seen


def translation_index_for_scope(
    scope: str = "all",
    library_ids: list[str] | None = None,
    *,
    include_workspace: bool = True,
) -> TranslationIndex:
    """Build translation index from workspace and/or selected catalogues.

    scope: "all" | "workspace" | "selected"
    library_ids: used when scope == "selected" (empty → workspace only)
    include_workspace: False keeps the current session's own documents out of
        the result even under scope "all" — Formslator's Format/Translate/Workspace
        tabs and Document Editor's highlight popup pass this so their vault-prefill
        can never silently draw on session-only content; Knowledge Vault's own tabs
        keep the default (True) since searching the current session is exactly
        what they're for.
    """
    from extensions.knowledge_vault.corpus.library import get_library, list_libraries
    from extensions.knowledge_vault.corpus.workspace import get_workspace

    pairs: list[TranslationPair] = []
    seen: set[frozenset[str]] = set()

    def _add(src_pairs: list[TranslationPair]) -> None:
        for p in src_pairs:
            key = _pair_dedup_key(p)
            if key in seen:
                continue
            seen.add(key)
            pairs.append(p)

    if include_workspace:
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

    cache_key = (
        scope_n,
        tuple(sorted(str(x) for x in (library_ids or []) if x)),
        include_workspace,
    )
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


def _all_pair_owners() -> list:
    """Every store that can hold TranslationPairs: the workspace session plus every
    ready library. Used by the Translation Vault tab's edit/delete actions, which
    operate on a pair found by id rather than a pre-selected scope — a pair belongs
    to exactly one store, so ownership is resolved by searching, not asked for."""
    from extensions.knowledge_vault.corpus.library import get_library, list_libraries
    from extensions.knowledge_vault.corpus.workspace import get_workspace

    owners = [get_workspace()]
    for lib in list_libraries():
        if lib.lexical_ready:
            owners.append(get_library(lib.library_id))
    return owners


def _persist_owner(owner) -> None:
    """Workspace pairs are session-only (matches WorkspaceSession.delete_file /
    set_retrieval_excluded, which are also in-memory-only); a LibraryCorpus has a
    save_translations() to write its translations.pkl back to disk."""
    save = getattr(owner, "save_translations", None)
    if callable(save):
        save()


@dataclass
class PairUpdateResult:
    success: bool
    old_score: float | None = None
    new_score: float | None = None


def update_translation_pair(pair_id: str, source_text: str, target_text: str) -> PairUpdateResult:
    """Edit one pair's text in place, wherever it lives. Also scores the pair
    before and after the edit with the same semantic scorer used to filter pairs
    at ingest time (services/formslator/vault_alignment.translation_pair_score),
    so the Translation Vault tab can warn when an edit made the source/target
    alignment worse — a manual edit can't be validated by the ingest-time
    threshold check the way a freshly-extracted pair is."""
    source_text = (source_text or "").strip()
    target_text = (target_text or "").strip()
    from services.formslator.vault_alignment import translation_pair_score

    for owner in _all_pair_owners():
        pairs = owner.translations.pairs
        for p in pairs:
            if p.pair_id == pair_id:
                try:
                    old_score = translation_pair_score(p.source_text, p.target_text)
                except Exception:
                    old_score = None
                p.source_text = source_text
                p.target_text = target_text
                try:
                    new_score = translation_pair_score(source_text, target_text)
                except Exception:
                    new_score = None
                owner.translations.build(pairs)
                _persist_owner(owner)
                clear_translation_index_cache()
                return PairUpdateResult(True, old_score, new_score)
    return PairUpdateResult(False)


def mark_pair_reviewed(pair_id: str) -> bool:
    """Dismiss a pair from the Translation Vault tab's "needs review" filter without
    touching its text — see TranslationPair.reviewed. Persisted like an edit, but
    skips clear_translation_index_cache(): reviewed doesn't affect lexical search
    (search results are unaffected either way), and the cached TranslationIndex
    holds these same pair objects by reference, so the mutation below is visible to
    it immediately without a rebuild."""
    for owner in _all_pair_owners():
        for p in owner.translations.pairs:
            if p.pair_id == pair_id:
                p.reviewed = True
                _persist_owner(owner)
                return True
    return False


def delete_translation_pairs(pair_ids: set[str]) -> int:
    """Remove the given pairs from whichever store(s) hold them, along with any
    other physical pair sharing the same {source, target} content key — the
    Translation Vault tab shows one row per dedup key (see translation_index_for_scope's
    _add()), so a store that still holds pre-append_unique_pairs duplicate rows would
    otherwise need one delete click per hidden duplicate before the row actually
    disappears (each click removing only the one pair_id the UI happened to have
    for that render). Returns the number of physical pairs actually removed."""
    if not pair_ids:
        return 0
    removed = 0
    for owner in _all_pair_owners():
        pairs = owner.translations.pairs
        target_keys = {
            _pair_dedup_key(p) for p in pairs if p.pair_id in pair_ids
        }
        if not target_keys:
            continue
        kept = [p for p in pairs if _pair_dedup_key(p) not in target_keys]
        if len(kept) != len(pairs):
            removed += len(pairs) - len(kept)
            owner.translations.build(kept)
            _persist_owner(owner)
    if removed:
        clear_translation_index_cache()
    return removed


def delete_all_translation_pairs(scope: str = "all", library_ids: list[str] | None = None) -> int:
    """Wipe every pair in scope ("all" | "workspace" | "selected"). Returns the
    number removed. Mirrors translation_index_for_scope's own scope semantics so
    "delete all" clears exactly what the same-scoped search would have shown."""
    from extensions.knowledge_vault.corpus.library import get_library, list_libraries
    from extensions.knowledge_vault.corpus.workspace import get_workspace

    scope_n = (scope or "all").strip().lower()
    owners = [get_workspace()]
    if scope_n != "workspace":
        selected = {str(x) for x in (library_ids or []) if x}
        for lib in list_libraries():
            if not lib.lexical_ready:
                continue
            if scope_n == "selected" and lib.library_id not in selected:
                continue
            owners.append(get_library(lib.library_id))

    removed = 0
    for owner in owners:
        removed += len(owner.translations.pairs)
        owner.translations.build([])
        _persist_owner(owner)
    if removed:
        clear_translation_index_cache()
    return removed


def match_translation(
    index: TranslationIndex,
    phrase: str,
    *,
    min_score: float = 0.85,
) -> TranslationPair | None:
    """Best vault match for a phrase (exact, then lexical). Bidirectional — checks
    both source_text and target_text, same as TranslationIndex.lookup() above, and
    orients the returned pair so its source_text is the side that matched."""
    text = (phrase or "").strip()
    if not text or not index.pairs:
        return None

    def _oriented(pair: "TranslationPair", matched_side: str) -> "TranslationPair":
        if matched_side == pair.source_text.strip():
            return pair
        return TranslationPair(
            source_text=pair.target_text,
            target_text=pair.source_text,
            file_path=pair.file_path,
            source_name=pair.source_name,
            pair_id=pair.pair_id,
            reviewed=pair.reviewed,
        )

    for pair in index.pairs:
        for side in (pair.source_text.strip(), pair.target_text.strip()):
            if side == text:
                return _oriented(pair, side)
    for pair in index.pairs:
        for side in (pair.source_text.strip(), pair.target_text.strip()):
            if not side:
                continue
            if text in side or side in text:
                shorter = min(len(text), len(side))
                longer = max(len(text), len(side))
                if shorter / longer >= 0.9:
                    return _oriented(pair, side)
    hits = index._lexical.search(text, limit=3, score_cutoff=min_score)
    for hit in hits:
        for pair in index.pairs:
            for side in (pair.source_text.strip(), pair.target_text.strip()):
                if side == hit.chunk.text.strip():
                    return _oriented(pair, side)
    return None


def format_translation_result(query: str, pairs: list[TranslationPair]) -> str:
    if not pairs:
        return (
            f"**Knowledge Vault** (Translation)\n\n"
            f"No translation pair found for: {query}"
        )
    best = pairs[0]
    lines = [
        "**Knowledge Vault** (Translation)",
        "",
        f"**Query:** {query}",
        "",
        f"**{best.source_text}** → **{best.target_text}**",
    ]
    if best.file_path:
        from extensions.knowledge_vault.ui.path_links import format_file_path_links

        lines.extend(["", format_file_path_links(best.file_path)])
    if len(pairs) > 1:
        lines.append("")
        lines.append("Other close matches:")
        lines.append("")
        for p in pairs[1:4]:
            lines.append(f"- {p.source_text} → {p.target_text}")
            lines.append("")
        lines.pop()
    return "\n".join(lines)
