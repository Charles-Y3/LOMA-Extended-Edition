# -*- coding: utf-8 -*-
"""SOPA-style vault alignment: anchor mapping, refinement, and search."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from extensions.knowledge_vault.translation import TranslationIndex, TranslationPair

LogFn = Callable[[str], None]

_CJK_RE = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF]")
_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[\w']+|[。，；：？！「」.,!?;:\"“”－—]")
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?。！？])\s+")
_FUZZY_RATIO = 0.9
_LEXICAL_MIN_SCORE = 0.85
_LONG_RATIO = 2.0

ALIGN_STRICT = 0.9
ALIGN_BORDERLINE = 0.85
REFINEMENT_MIN = 0.45
INGEST_PAIR_MIN = 0.75

_NOT_FOUND = "Not Found"

_ATTRIBUTION_RULES: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (re.compile(r"\bthe buddha said\b", re.I), re.compile(r"佛[云曰说]|佛祖")),
    (re.compile(r"\ba proverb says\b", re.I), re.compile(r"俗語|谚语")),
    (re.compile(r"\bconfucius said\b", re.I), re.compile(r"孔子|子曰")),
)


@dataclass
class VaultHit:
    translation_text: str
    alignment_score: float
    pair: "TranslationPair"
    method: str
    vault_source: str
    vault_target: str


def _normalize_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip())


def _flat(text: str) -> str:
    return "".join((text or "").split()).lower()


def _cjk_ratio(text: str) -> float:
    t = text or ""
    if not t:
        return 0.0
    return len(_CJK_RE.findall(t)) / max(len(t), 1)


def is_valid_vault_output(source: str, translation: str) -> bool:
    """Reject empty, source-echo, or still-Chinese outputs for English column."""
    src = (source or "").strip()
    tr = (translation or "").strip()
    if not tr or tr == src:
        return False
    if tr in src and _cjk_ratio(tr) > 0.3:
        return False
    src_cjk = _cjk_ratio(src)
    tr_cjk = _cjk_ratio(tr)
    if src_cjk > 0.15 and tr_cjk >= max(0.15, src_cjk * 0.65):
        return False
    return True


def _count_units(text: str) -> int:
    units = _TOKEN_PATTERN.findall(text or "")
    return len([u for u in units if u.strip()])


def _sentence_count(text: str) -> int:
    parts = [p.strip() for p in _SENTENCE_SPLIT.split((text or "").strip()) if p.strip()]
    return max(1, len(parts))


def _is_contiguous_substring(haystack: str, needle: str) -> bool:
    h = _normalize_phrase(haystack)
    n = _normalize_phrase(needle)
    if not h or not n:
        return False
    if n in h:
        return True
    return _flat(n) in _flat(h)


def _attribution_bleed(source: str, translation: str) -> bool:
    for en_pat, zh_pat in _ATTRIBUTION_RULES:
        if en_pat.search(translation or "") and not zh_pat.search(source or ""):
            return True
    return False


def _is_literal_source_match(source: str, vault_source: str) -> bool:
    return _normalize_phrase(source) == _normalize_phrase(vault_source)


def is_obviously_wrong(
    source: str,
    translation: str,
    vault_source: str = "",
    vault_target: str = "",
) -> bool:
    """Conservative gate: flag only egregious span errors (not mild length drift)."""
    src = (source or "").strip()
    tr = (translation or "").strip()
    if not src or not tr or not is_valid_vault_output(src, tr):
        return True

    if _is_literal_source_match(src, vault_source):
        return False

    if _attribution_bleed(src, tr):
        return True

    src_u = _count_units(src)
    tr_u = _count_units(tr)
    if src_u <= 0:
        return True

    if src_u <= 35:
        if tr_u / src_u > 5.0:
            return True
        src_sent = _sentence_count(src)
        tr_sent = _sentence_count(tr)
        if src_sent <= 2 and tr_sent >= src_sent + 3:
            return True

    return False


def is_plausible_span(
    source: str,
    translation: str,
    vault_source: str = "",
    vault_target: str = "",
) -> bool:
    """Back-compat alias: plausible when not obviously wrong."""
    return not is_obviously_wrong(source, translation, vault_source, vault_target)


def _expected_word_hint(source: str, vault_source: str, vault_target: str) -> str:
    src_u = _count_units(source)
    if not vault_source or not vault_target:
        return f"{max(3, src_u)}-{max(6, int(src_u * 2.5))}"
    _, s_units, t_units, g_ratio = _generate_anchor_map(vault_source, vault_target)
    if not s_units:
        return f"{max(3, src_u)}-{max(6, int(src_u * 2.5))}"
    est = max(3, int(src_u * g_ratio))
    return f"{est}-{max(est + 2, int(est * 1.4))}"


def _llm_extract_span(source: str, vault_source: str, vault_target: str) -> str:
    try:
        from extensions.ludicity_shared.llm import ludicity_chat

        word_hint = _expected_word_hint(source, vault_source, vault_target)
        src_lines = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(vault_source.splitlines()) if ln.strip())
        tgt_lines = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(vault_target.splitlines()) if ln.strip())
        prompt = (
            "Extract ONLY the English from the vault translation that translates the document phrase.\n"
            "Rules:\n"
            "- Must be a single contiguous verbatim substring of the vault translation.\n"
            "- English clause order may differ from Chinese; still pick one contiguous span.\n"
            "- Do NOT include unrelated preceding or following sentences.\n"
            "- Never return Chinese. No quotes or explanation.\n"
            f"- Approximate length: {word_hint} words.\n\n"
            f"Document phrase:\n{source}\n\n"
            f"Vault source (numbered):\n{src_lines or vault_source}\n\n"
            f"Vault translation (numbered):\n{tgt_lines or vault_target}"
        )
        trimmed = (ludicity_chat(
            [
                {"role": "system", "content": "You extract the minimal corresponding English translation span only."},
                {"role": "user", "content": prompt},
            ]
        ) or "").strip()
        if not trimmed or not is_valid_vault_output(source, trimmed):
            return ""
        if vault_target and not _is_contiguous_substring(vault_target, trimmed):
            return ""
        return trimmed
    except Exception:
        return ""


def _llm_trim_span(source: str, overlong: str, vault_source: str, vault_target: str) -> str:
    try:
        from extensions.ludicity_shared.llm import ludicity_chat

        word_hint = _expected_word_hint(source, vault_source, vault_target)
        prompt = (
            "The following English is too long or includes unrelated sentences.\n"
            "Return ONLY the contiguous substring (verbatim from the vault translation) "
            "that translates the Chinese phrase.\n"
            "English clause order may differ from Chinese; still return one contiguous span.\n"
            f"Approximate length: {word_hint} words. No explanation.\n\n"
            f"Chinese:\n{source}\n\n"
            f"Over-long English:\n{overlong}\n\n"
            f"Vault translation:\n{vault_target}"
        )
        trimmed = (ludicity_chat(
            [
                {"role": "system", "content": "You trim English to the minimal valid translation span only."},
                {"role": "user", "content": prompt},
            ]
        ) or "").strip()
        if not trimmed or not is_valid_vault_output(source, trimmed):
            return ""
        if vault_target and not _is_contiguous_substring(vault_target, trimmed):
            return ""
        return trimmed
    except Exception:
        return ""


def _llm_validate(source: str, candidate: str) -> bool:
    try:
        from extensions.ludicity_shared.llm import ludicity_chat

        reply = (ludicity_chat(
            [
                {
                    "role": "system",
                    "content": "Reply with exactly YES or NO. Does the English accurately translate the Chinese phrase?",
                },
                {"role": "user", "content": f"Chinese:\n{source}\n\nEnglish:\n{candidate}"},
            ]
        ) or "").strip().upper()
        return reply.startswith("YES")
    except Exception:
        return False


def _llm_validate_tightness(source: str, candidate: str) -> bool:
    try:
        from extensions.ludicity_shared.llm import ludicity_chat

        reply = (ludicity_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Reply with exactly YES or NO. Does the English translate ONLY the Chinese phrase "
                        "with no extra preceding or following sentences?"
                    ),
                },
                {"role": "user", "content": f"Chinese:\n{source}\n\nEnglish:\n{candidate}"},
            ]
        ) or "").strip().upper()
        return reply.startswith("YES")
    except Exception:
        return False


def _llm_accept(source: str, candidate: str, *, require_tightness: bool) -> bool:
    if not _llm_validate(source, candidate):
        return False
    if require_tightness and not _llm_validate_tightness(source, candidate):
        return False
    return True


def _resolve_vault_span(
    source: str,
    vault_source: str,
    vault_target: str,
    candidate: str = "",
    *,
    allow_llm: bool = True,
    llm_validate: bool = True,
) -> tuple[str, str]:
    """Accept good extractions as-is; LLM only when obviously wrong."""
    src = (source or "").strip()
    v_src = (vault_source or "").strip()
    v_tgt = (vault_target or "").strip()
    if not src or not v_tgt:
        return "", ""

    cand = (candidate or "").strip()
    if cand and is_valid_vault_output(src, cand) and not is_obviously_wrong(src, cand, v_src, v_tgt):
        return cand, ""

    if not allow_llm:
        return "", ""

    llm_text = ""
    if cand and is_valid_vault_output(src, cand):
        llm_text = _llm_trim_span(src, cand, v_src, v_tgt)
    if not llm_text:
        llm_text = _llm_extract_span(src, v_src, v_tgt)
    if not llm_text:
        return "", ""

    if llm_validate and not _llm_accept(src, llm_text, require_tightness=True):
        return "", ""

    if is_obviously_wrong(src, llm_text, v_src, v_tgt):
        return "", ""

    return llm_text, "+llm"


def _cosine_sim(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def _embed_query(text: str) -> list[float] | None:
    try:
        from services.rag_embeddings import get_embedding_backend, rag_dependencies_available

        ok, _ = rag_dependencies_available()
        if not ok:
            return None
        return get_embedding_backend().embed_query(text)
    except Exception:
        return None


def translation_pair_score(source: str, target: str) -> float:
    """SOPA bert_translation_score equivalent for ingest filtering."""
    q_raw = _normalize_phrase(source)
    t_raw = _normalize_phrase(target)
    if not q_raw or not t_raw:
        return 0.0

    emb_a = _embed_query(q_raw)
    emb_b = _embed_query(t_raw)
    if emb_a and emb_b:
        sim_score = _cosine_sim(emb_a, emb_b)
    else:
        sim_score = 0.55 if _flat(q_raw) != _flat(t_raw) else 0.0

    u_a, u_b = _count_units(q_raw), _count_units(t_raw)
    max_u = max(u_a, u_b)
    if max_u <= 5:
        w_sim, w_len = 0.90, 0.10
    elif max_u <= 20:
        w_sim, w_len = 0.75, 0.25
    else:
        w_sim, w_len = 0.60, 0.40

    len_ratio = min(u_a, u_b) / max_u if max_u > 0 else 0.0
    return round(float(sim_score * w_sim + len_ratio * w_len), 3)


def _generate_anchor_map(s_text: str, t_text: str):
    s_raw = str(s_text).strip()
    t_raw = str(t_text).strip()

    def get_units(text: str) -> list[str]:
        units = _TOKEN_PATTERN.findall(text)
        return [u for u in units if u.strip()]

    s_units = get_units(s_raw)
    t_units = get_units(t_raw)
    s_len, t_len = len(s_units), len(t_units)
    golden_ratio = t_len / s_len if s_len > 0 else 1.0
    tolerance = 0.25

    ata_points = [(0, 0, "ATA_START", "[START]", "[START]")]
    rules = [
        (["：", ":"], [":", "："], "COLON"),
        (["；", ";"], [";", "；", "。", "."], "SEMICOLON"),
        (["。", "."], [".", "。"], "PERIOD"),
        (["？", "?"], ["?", "？"], "QUESTION"),
        (["！", "!"], ["!", "！"], "EXCLAMATION"),
        (["「", '"', "“"], ['"', "“", "「"], "QUOTE_OPEN"),
        (["」", '"', "”"], ['"', "”", "」"], "QUOTE_CLOSE"),
    ]

    for i, s_unit in enumerate(s_units):
        for s_candidates, t_candidates, p_type in rules:
            if not any(cand in s_unit for cand in s_candidates):
                continue
            matched_s_char = next(cand for cand in s_candidates if cand in s_unit)
            expected_t = i * golden_ratio
            t_window = max(10, int(expected_t * tolerance))
            search_start = max(0, int(expected_t - t_window))
            search_end = min(t_len, int(expected_t + t_window))
            best_match_idx = -1
            min_drift = float("inf")
            for t_idx in range(search_start, search_end):
                if any(cand in t_units[t_idx] for cand in t_candidates):
                    drift = abs(t_idx - expected_t)
                    if drift < min_drift:
                        min_drift = drift
                        best_match_idx = t_idx
            if best_match_idx != -1:
                ata_points.append((i, best_match_idx, f"ATA_{p_type}", matched_s_char, t_units[best_match_idx]))

    ata_points.append((s_len, t_len, "ATA_END", "[END]", "[END]"))
    ata_points.sort(key=lambda x: (x[0], x[1]))
    clean_ata = [ata_points[0]]
    for p in ata_points[1:-1]:
        if p[0] > clean_ata[-1][0] and p[1] > clean_ata[-1][1]:
            clean_ata.append(p)
    if ata_points[-1][0] >= clean_ata[-1][0] and ata_points[-1][1] >= clean_ata[-1][1]:
        if ata_points[-1] != clean_ata[-1]:
            clean_ata.append(ata_points[-1])
    return clean_ata, s_units, t_units, golden_ratio


def _refinement_balanced(text_slice: str, query: str, golden_ratio: float) -> tuple[str, str]:
    if not text_slice or not text_slice.strip():
        return query, _NOT_FOUND

    clean_text = text_slice.strip()
    clean_query = query.strip()

    emb_q = _embed_query(clean_query)
    emb_t = _embed_query(clean_text)
    if emb_q and emb_t:
        sim_score = _cosine_sim(emb_q, emb_t)
    else:
        sim_score = 0.5

    q_len = _count_units(clean_query)
    t_len = _count_units(clean_text)
    expected_t_len = q_len * golden_ratio
    if expected_t_len > 0:
        len_diff = abs(t_len - expected_t_len)
        len_ratio_score = max(0.0, 1.0 - (len_diff / expected_t_len))
    else:
        len_ratio_score = 0.0

    final_score = (sim_score * 0.5) + (len_ratio_score * 0.5)
    if final_score > REFINEMENT_MIN:
        return clean_query, clean_text
    return clean_query, _NOT_FOUND


def get_precise_pair(col1_text: str, col2_text: str, query: str) -> tuple[str, str]:
    c1_raw = str(col1_text).strip()
    c2_raw = str(col2_text).strip()
    query_clean = str(query).strip()
    query_flat = _flat(query_clean)
    col1_flat = _flat(c1_raw)
    col2_flat = _flat(c2_raw)

    if query_flat in col1_flat:
        s_doc, t_doc = c1_raw, c2_raw
    elif query_flat in col2_flat:
        s_doc, t_doc = c2_raw, c1_raw
    else:
        return query_clean, _NOT_FOUND

    anchors, s_units, t_units, g_ratio = _generate_anchor_map(s_doc, t_doc)
    source_flat_units = "".join(s_units).lower()
    char_start = source_flat_units.find(query_flat)
    if char_start == -1:
        return query_clean, t_doc

    current_char_count = 0
    q_start_idx = -1
    q_last_idx = -1
    target_char_len = len(query_flat)

    for i, unit in enumerate(s_units):
        unit_len = len(unit)
        if q_start_idx == -1 and current_char_count >= char_start:
            q_start_idx = i
        if q_start_idx != -1 and current_char_count + unit_len >= char_start + target_char_len:
            q_last_idx = i
            break
        current_char_count += unit_len
    if q_last_idx == -1:
        q_last_idx = len(s_units) - 1

    start_a = anchors[0]
    for a in anchors:
        if a[0] <= q_start_idx:
            start_a = a
        else:
            break
    end_a = anchors[-1]
    for a in anchors:
        if a[0] >= q_last_idx:
            end_a = a
            break

    if len(anchors) <= 2:
        t_start, t_end_final = anchors[0][1], anchors[-1][1]
    else:
        t_start, t_end = start_a[1], end_a[1]
        left_inclusive = (start_a[3] in s_units[q_start_idx]) if start_a[0] == q_start_idx else False
        right_inclusive = (end_a[3] in s_units[q_last_idx]) if end_a[0] == q_last_idx else False
        if not left_inclusive and "START" not in start_a[2]:
            t_start += 1
        t_end_final = t_end + 1 if right_inclusive else t_end
        if t_start >= t_end_final and t_start > 0:
            t_start, t_end_final = max(0, start_a[1]), min(len(t_units), end_a[1])

    extracted_tokens = t_units[t_start:t_end_final]
    if not extracted_tokens:
        return query_clean, t_doc

    target_sample = "".join(extracted_tokens[:10])
    is_cjk_target = any("\u4e00" <= c <= "\u9fff" for c in target_sample)
    if is_cjk_target:
        extracted = "".join(extracted_tokens).strip()
    else:
        extracted = " ".join(extracted_tokens).strip()
        extracted = re.sub(r"\s+([.,!?;:”)])", r"\1", extracted)
        extracted = re.sub(r"([“(])\s+", r"\1", extracted)

    _, winner = _refinement_balanced(extracted, query_clean, g_ratio)
    return query_clean, winner


def _line_index_extract(source: str, vault_source: str, translation: str) -> str:
    src = (source or "").strip()
    src_lines = [ln.strip() for ln in vault_source.splitlines() if ln.strip()]
    tgt_lines = [ln.strip() for ln in translation.splitlines() if ln.strip()]
    if not src_lines or len(src_lines) != len(tgt_lines):
        return ""
    for i, line in enumerate(src_lines):
        if _normalize_phrase(line) == _normalize_phrase(src):
            candidate = tgt_lines[i]
            return candidate if is_valid_vault_output(src, candidate) else ""
    return ""


def _keyword_overlap_score(query: str, vault_src: str) -> float:
    token_pattern = r"[\u4e00-\u9fff]|\b\w{3,}\b"
    query_tokens = set(re.findall(token_pattern, query.lower()))
    vault_tokens = set(re.findall(token_pattern, vault_src.lower()))
    if not query_tokens:
        return 0.0
    common = query_tokens.intersection(vault_tokens)
    overlap_ratio = len(common) / len(query_tokens)
    required = 1.0 if len(query_tokens) <= 6 else 0.7
    length_ratio = len(query) / max(1, len(vault_src))
    if overlap_ratio >= required and length_ratio >= 0.30:
        return max(overlap_ratio, 0.95)
    return overlap_ratio * 0.5


def collect_vault_candidates(index: "TranslationIndex", phrase: str) -> list["TranslationPair"]:
    text = _normalize_phrase(phrase)
    if not text or not getattr(index, "pairs", None):
        return []

    seen: set[tuple[str, str]] = set()
    out: list[TranslationPair] = []

    def _add(pair: "TranslationPair") -> None:
        key = (_normalize_phrase(pair.source_text), _normalize_phrase(pair.target_text))
        if key in seen:
            return
        seen.add(key)
        out.append(pair)

    for pair in index.pairs:
        if _normalize_phrase(pair.source_text) == text:
            _add(pair)
    for pair in index.pairs:
        src = _normalize_phrase(pair.source_text)
        if not src:
            continue
        if text in src or src in text:
            shorter = min(len(text), len(src))
            longer = max(len(text), len(src))
            if longer > 0 and shorter / longer >= _FUZZY_RATIO:
                _add(pair)
    hits = index._lexical.search(text, limit=8, score_cutoff=_LEXICAL_MIN_SCORE)
    for hit in hits:
        for pair in index.pairs:
            if _normalize_phrase(pair.source_text) == _normalize_phrase(hit.chunk.text):
                _add(pair)
    return out


def _score_candidate(pair: "TranslationPair", phrase: str) -> VaultHit | None:
    phrase = phrase.strip()
    for src, tgt in (
        (pair.source_text.strip(), pair.target_text.strip()),
        (pair.target_text.strip(), pair.source_text.strip()),
    ):
        if not src or not tgt:
            continue

        if src.lower() == phrase.lower():
            if is_valid_vault_output(phrase, tgt):
                return VaultHit(tgt, 1.0, pair, "literal", src, tgt)

        _, winner = get_precise_pair(src, tgt, phrase)
        if winner != _NOT_FOUND and is_valid_vault_output(phrase, winner):
            return VaultHit(winner, 1.0, pair, "anchor", src, tgt)

        line_hit = _line_index_extract(phrase, src, tgt)
        if line_hit:
            return VaultHit(line_hit, 1.0, pair, "line_index", src, tgt)

        exact_full = _normalize_phrase(src) == _normalize_phrase(phrase)
        short_enough = len(tgt) <= max(int(len(phrase) * _LONG_RATIO), len(phrase) + 80)
        if exact_full and short_enough and is_valid_vault_output(phrase, tgt):
            return VaultHit(tgt, 1.0, pair, "exact_full", src, tgt)

        alignment = _keyword_overlap_score(phrase, src)
        if phrase.lower() in src.lower():
            alignment = max(alignment, 1.0)

        if alignment >= ALIGN_BORDERLINE:
            return VaultHit("", alignment, pair, "candidate", src, tgt)

    return None


def _finalize_hit(
    text: str,
    hit: VaultHit,
    *,
    allow_llm: bool,
    llm_validate: bool,
    raw_override: str = "",
) -> VaultHit | None:
    raw = (raw_override or hit.translation_text or "").strip()
    if not raw and hit.alignment_score >= ALIGN_BORDERLINE:
        _, extracted = get_precise_pair(hit.vault_source, hit.vault_target, text)
        if extracted != _NOT_FOUND:
            raw = extracted.strip()

    refined, suffix = _resolve_vault_span(
        text,
        hit.vault_source,
        hit.vault_target,
        raw,
        allow_llm=allow_llm,
        llm_validate=llm_validate,
    )
    if not refined or not is_valid_vault_output(text, refined):
        return None

    method = hit.method
    if suffix == "+llm":
        method = "llm"
    score = hit.alignment_score
    if suffix == "+llm":
        score = max(score, ALIGN_STRICT)
    return VaultHit(refined, score, hit.pair, method, hit.vault_source, hit.vault_target)


def search_vault_translation(
    phrase: str,
    index: "TranslationIndex",
    *,
    min_alignment: float = ALIGN_STRICT,
    allow_llm: bool = True,
    borderline_llm: bool = True,
    llm_validate_borderline: bool = True,
) -> VaultHit | None:
    """Find aligned English for a phrase; blank-worthy if below min_alignment after extraction."""
    text = (phrase or "").strip()
    if not text:
        return None

    candidates: list[VaultHit] = []
    for pair in collect_vault_candidates(index, text):
        hit = _score_candidate(pair, text)
        if hit:
            candidates.append(hit)

    if not candidates:
        return None

    candidates.sort(key=lambda h: h.alignment_score, reverse=True)
    best = candidates[0]

    raw = (best.translation_text or "").strip()
    if not raw and best.alignment_score >= ALIGN_BORDERLINE:
        _, extracted = get_precise_pair(best.vault_source, best.vault_target, text)
        if extracted != _NOT_FOUND:
            raw = extracted.strip()

    if (
        raw
        and best.alignment_score >= min_alignment
        and is_valid_vault_output(text, raw)
        and not is_obviously_wrong(text, raw, best.vault_source, best.vault_target)
    ):
        return VaultHit(raw, best.alignment_score, best.pair, best.method, best.vault_source, best.vault_target)

    if best.alignment_score < min_alignment and not (
        borderline_llm and ALIGN_BORDERLINE <= best.alignment_score < min_alignment
    ):
        return None

    llm_validate = llm_validate_borderline and (
        best.alignment_score < ALIGN_STRICT or borderline_llm
    )
    finalized = _finalize_hit(
        text,
        best,
        allow_llm=allow_llm,
        llm_validate=llm_validate,
        raw_override=raw,
    )
    if finalized and finalized.alignment_score >= min_alignment:
        return finalized

    if allow_llm and borderline_llm and best.alignment_score >= ALIGN_BORDERLINE:
        finalized = _finalize_hit(
            text,
            best,
            allow_llm=True,
            llm_validate=True,
            raw_override=raw,
        )
        if finalized and finalized.alignment_score >= ALIGN_BORDERLINE:
            return finalized

    return None


# Back-compat shims used by translate_engine imports
def match_vault_pair(index, phrase: str, *, min_score: float = _LEXICAL_MIN_SCORE):
    hit = search_vault_translation(phrase, index, min_alignment=ALIGN_STRICT, allow_llm=True)
    return hit.pair if hit else None


def resolve_vault_translation(
    source: str,
    translation: str,
    *,
    vault_source: str = "",
) -> str:
    from extensions.knowledge_vault.translation import TranslationIndex, TranslationPair

    vault_src = (vault_source or source).strip()
    vault_tgt = (translation or "").strip()
    pair = TranslationPair(source_text=vault_src, target_text=vault_tgt)
    idx = TranslationIndex()
    idx.build([pair])
    hit = search_vault_translation(source, idx, min_alignment=ALIGN_STRICT, allow_llm=True)
    return hit.translation_text if hit else ""
