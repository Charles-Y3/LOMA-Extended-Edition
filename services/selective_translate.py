# -*- coding: utf-8 -*-
"""Partial translation driven by user instruction (no hardcoded language pairs)."""
from __future__ import annotations

import re

_SCRIPT_PATTERNS: dict[str, re.Pattern[str]] = {
    "chinese": re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+"),
    "mandarin": re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+"),
    "cantonese": re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]+"),
    "japanese": re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]+"),
    "korean": re.compile(r"[\uac00-\ud7af\u1100-\u11ff]+"),
    "thai": re.compile(r"[\u0e00-\u0e7f]+"),
    "arabic": re.compile(r"[\u0600-\u06ff]+"),
    "hindi": re.compile(r"[\u0900-\u097f]+"),
}

_VIETNAMESE_DIACRITIC = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.IGNORECASE,
)

_LATIN_WORD = re.compile(r"\b[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ0-9''-]*\b")
_ASCII_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9'-]+\b")

_LANGUAGE_NAMES = (
    "english",
    "spanish",
    "french",
    "german",
    "italian",
    "portuguese",
    "chinese",
    "mandarin",
    "cantonese",
    "japanese",
    "korean",
    "vietnamese",
    "arabic",
    "hindi",
    "thai",
    "malay",
    "indonesian",
)


def _lang_names_pattern() -> str:
    return "|".join(re.escape(name) for name in _LANGUAGE_NAMES)


def _parse_selective_scope(instruction: str) -> tuple[str | None, str | None]:
  lower = (instruction or "").lower()
  lang_alt = _lang_names_pattern()

  lang_pair = re.search(
      rf"\b({lang_alt})\s+(?:to|into)\s+({lang_alt})\b",
      lower,
  )
  if lang_pair:
      src = lang_pair.group(1).lower()
      tgt = lang_pair.group(2).lower()
      if src in ("mandarin", "cantonese"):
          src = "chinese"
      if tgt in ("mandarin", "cantonese"):
          tgt = "chinese"
      if src != tgt:
          return src, tgt

  from pipeline.query_intent_i18n import matches

  # Beyond this point, source/target language extraction is English-language-name
  # only (walks _LANGUAGE_NAMES against "to/into/in <language>" prepositions) — a
  # known, documented limitation: fully localizing this needs per-language
  # preposition grammar, not just translated language names. The "only"/"just" gate
  # itself is localized via the shared concept matcher.
  if not matches(lower, "selective_scope"):
      return None, None

  target: str | None = None
  for lang in _LANGUAGE_NAMES:
      if re.search(rf"\b(?:to|into|in)\s+{re.escape(lang)}\b", lower):
          target = lang
          break

  source: str | None = None
  m = re.search(
      r"(?:only|just)\s+(?:translate\s+)?(?:the\s+)?(\w+)(?:\s+words?|\s+text|\s+parts?|\s+spans?)?",
      lower,
  )
  if not m:
      m = re.search(r"translate\s+only\s+(?:the\s+)?(\w+)", lower)
  if m:
      cand = m.group(1).lower()
      if cand in _LANGUAGE_NAMES:
          source = cand
  if not source:
      for lang in _LANGUAGE_NAMES:
          if re.search(rf"\bonly\s+{re.escape(lang)}\b", lower):
              source = lang
              break
          if re.search(rf"\b{re.escape(lang)}\s+words?\b", lower) and "only" in lower:
              source = lang
              break

  if not target:
      for lang in _LANGUAGE_NAMES:
          if lang != source and re.search(rf"\b{re.escape(lang)}\b", lower):
              if any(
                  p in lower
                  for p in (f"to {lang}", f"into {lang}", f"in {lang}", f"{lang} translation")
              ):
                  target = lang
                  break

  return source, target


def _span_pattern(source_lang: str) -> re.Pattern[str]:
    key = (source_lang or "").lower()
    if key in _SCRIPT_PATTERNS:
        return _SCRIPT_PATTERNS[key]
    if key == "vietnamese":
        return re.compile(
            r"\b[^\s\d\W]*"
            r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]"
            r"[^\s]*",
            re.IGNORECASE,
        )
    if key in ("english", "indonesian", "malay"):
        return _ASCII_WORD
    return _LATIN_WORD


def _is_actionable_span(span: str, source_lang: str) -> bool:
    s = (span or "").strip()
    if not s:
        return False
    if len(s) < 2 and source_lang in ("english", "indonesian", "malay"):
        return False
    key = (source_lang or "").lower()
    if key in ("english", "indonesian", "malay"):
        if _VIETNAMESE_DIACRITIC.search(s):
            return False
        return bool(re.fullmatch(r"[A-Za-z0-9'-]+", s))
    if key == "vietnamese":
        return bool(_VIETNAMESE_DIACRITIC.search(s))
    return True


def collect_spans(text: str, instruction: str) -> list[str]:
    source, _ = _parse_selective_scope(instruction)
    if not source:
        return []
    pattern = _span_pattern(source)
    return [
        m.group(0)
        for m in pattern.finditer(text or "")
        if _is_actionable_span(m.group(0), source)
    ]


def _significant_source_span(span: str, source_lang: str) -> bool:
    s = (span or "").strip()
    if not s:
        return False
    key = (source_lang or "").lower()
    if key in ("english", "indonesian", "malay"):
        if len(s) >= 5:
            return True
        if len(s) >= 3 and (any(c.isupper() for c in s) or "-" in s or "'" in s):
            return True
        return False
    return _is_actionable_span(s, source_lang)


def has_selective_spans(text: str, instruction: str) -> bool:
    source, _ = _parse_selective_scope(instruction)
    spans = collect_spans(text, instruction)
    if not spans or not source:
        return bool(spans)
    return any(_significant_source_span(s, source) for s in spans)


def _translate_span(span: str, *, source_lang: str, target_lang: str, model: str) -> str:
    if not span.strip() or source_lang == target_lang:
        return span
    from services import llm_bridge as chat_client
    from services.resource_governor import ResourceGovernor

    prompt = (
        f"Translate this text into {target_lang}. "
        f"Return ONLY the translation, no quotes or explanation.\n\n"
        f"Text:\n{span}"
    )
    try:
        with ResourceGovernor.acquire("llm_chat"):
            resp = chat_client.generate(model=model, prompt=prompt, stream=False)
        out = (resp.get("response") or "").strip()
        return out or span
    except Exception:
        return span


def _translate_spans_batch(
    spans: list[str],
    *,
    source_lang: str,
    target_lang: str,
    model: str,
) -> dict[str, str]:
    unique = [s for s in dict.fromkeys(spans) if (s or "").strip()]
    if not unique:
        return {}
    if source_lang == target_lang:
        return {s: s for s in unique}

    from services import llm_bridge as chat_client
    from services.resource_governor import ResourceGovernor

    payload = "\n".join([f"{i+1}. {text}" for i, text in enumerate(unique)])
    prompt = (
        f"Translate each numbered item into {target_lang}.\n"
        "Return ONLY a JSON object mapping each original item text to its translation.\n\n"
        f"Items:\n{payload}"
    )
    try:
        with ResourceGovernor.acquire("llm_chat"):
            resp = chat_client.generate(model=model, prompt=prompt, stream=False)
        raw = (resp.get("response") or "").strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            import json

            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                out: dict[str, str] = {}
                for key, value in parsed.items():
                    k = str(key)
                    v = str(value)
                    if k in unique and v.strip():
                        out[k] = v
                if out:
                    return out
        numbered = re.findall(r"^\s*\d+[\).\]:]\s*(.+)$", raw, re.MULTILINE)
        if len(numbered) == len(unique):
            return {src: dst.strip() for src, dst in zip(unique, numbered) if dst.strip()}
        return {}
    except Exception:
        return {}


def apply_selective_translation(
    original: str,
    llm_replacement: str,
    instruction: str,
    *,
    model: str,
    span_cache: dict[tuple[str, str, str], str] | None = None,
) -> str:
    source, target = _parse_selective_scope(instruction)
    if not source:
        return llm_replacement

    pattern = _span_pattern(source)
    spans = [
        m.group(0)
        for m in pattern.finditer(original or "")
        if _is_actionable_span(m.group(0), source)
    ]
    if not spans:
        return original

    translated = _translate_spans_batch(
        spans,
        source_lang=source,
        target_lang=target or "english",
        model=model,
    )
    cache = span_cache if span_cache is not None else {}
    tgt = target or "english"

    def _sub(match: re.Match) -> str:
        chunk = match.group(0)
        if not _is_actionable_span(chunk, source):
            return chunk
        key = (source, tgt, chunk)
        if key in cache:
            return cache[key]
        out = translated.get(chunk)
        if not out:
            out = _translate_span(chunk, source_lang=source, target_lang=tgt, model=model)
        cache[key] = out or chunk
        return cache[key]

    return pattern.sub(_sub, original or "")


def apply_selective_to_units(
    units: list[dict],
    instruction: str,
    *,
    model: str,
) -> dict[str, str]:
    """Batch selective translation across units — one span batch per unique text."""
    source, target = _parse_selective_scope(instruction)
    baseline = {u["id"]: str(u.get("text") or u.get("value") or "") for u in units}
    if not source:
        return baseline

    pattern = _span_pattern(source)
    tgt = target or "english"
    all_spans: list[str] = []
    for u in units:
        text = str(u.get("text") or u.get("value") or "")
        for m in pattern.finditer(text):
            span = m.group(0)
            if _is_actionable_span(span, source):
                all_spans.append(span)

    unique_spans = list(dict.fromkeys(all_spans))
    if not unique_spans:
        return baseline

    translated = _translate_spans_batch(
        unique_spans,
        source_lang=source,
        target_lang=tgt,
        model=model,
    )
    cache: dict[tuple[str, str, str], str] = {}
    out: dict[str, str] = {}
    for u in units:
        uid = u["id"]
        text = baseline[uid]

        def _sub(match: re.Match, _source=source, _tgt=tgt) -> str:
            chunk = match.group(0)
            if not _is_actionable_span(chunk, _source):
                return chunk
            key = (_source, _tgt, chunk)
            if key in cache:
                return cache[key]
            val = translated.get(chunk)
            if not val:
                val = _translate_span(chunk, source_lang=_source, target_lang=_tgt, model=model)
            cache[key] = val or chunk
            return cache[key]

        new_text = pattern.sub(_sub, text)
        out[uid] = new_text
    return out


def has_explicit_source_lang_pair(instruction: str) -> bool:
    """True when user names a source language (e.g. chinese to english, spanish to english)."""
    source, target = _parse_selective_scope(instruction)
    return bool(source and target)


def should_apply_selective(instruction: str, role_ids: list[str] | None) -> bool:
    from pipeline.query_intent_i18n import matches

    if role_ids and "mutation_selective_translator" in role_ids:
        return True
    if has_explicit_source_lang_pair(instruction):
        return True
    lower = (instruction or "").lower()
    if matches(lower, "selective_scope"):
        return matches(lower, "verb_translate")
    return False
