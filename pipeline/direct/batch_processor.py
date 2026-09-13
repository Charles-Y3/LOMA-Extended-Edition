# -*- coding: utf-8 -*-
"""Batch LLM processing for long workspace context — streams each batch to chat."""
from __future__ import annotations

import re
from typing import Any, Callable

from pipeline.capability_runtime.chat_runner import generate_text_sync, stream_chat_response
from pipeline.direct.batch_budget import (
    batch_chunk_chars,
    fit_budget_to_prompt,
    llm_extra_options,
    max_paragraphs_per_chunk,
    prompt_fits_hardware,
    resolve_batch_budget,
    single_pass_char_limit,
)
from pipeline.direct.prompt_hygiene import looks_like_refusal, user_step_content

_CJK_RE = re.compile(r"[㐀-鿿豈-﫿]")


def _looks_untranslated(text: str, source_chunk: str) -> bool:
    """A translated chunk that's still mostly CJK almost certainly means the model
    echoed that one chunk's source back instead of translating it — small local
    models occasionally do this on a handful of chunks in an otherwise fine long
    translation (dense quote/citation content seems to trigger it). A single bad
    chunk barely moves the aggregate too-short retry check further down, since
    every other chunk's real translation dilutes the ratio — this catches it
    per-chunk instead, right where it happened."""
    if not text or not source_chunk:
        return False
    if len(_CJK_RE.findall(source_chunk)) < 10:
        return False
    return len(_CJK_RE.findall(text)) / max(1, len(text)) > 0.3


_TARGET_LANG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:to|into)\s+english\b", re.I), "English"),
    (re.compile(r"\b(?:to|into)\s+chinese\b", re.I), "Chinese"),
    (re.compile(r"\b(?:to|into)\s+(?:mandarin|cantonese)\b", re.I), "Chinese"),
    (re.compile(r"\b(?:to|into)\s+(spanish|french|german|japanese|korean)\b", re.I), ""),
]

# Chinese-language instructions (e.g. "翻譯成英語") never match the English-only
# patterns above, so every batch fell back to the vague "the requested target
# language" — each per-paragraph LLM call then guessed independently, producing
# documents that were only partially translated. Mirrors the marker words
# query_planner.py's _TRANSLATE_WORDS/_LANG_PAIR already use for intent detection.
_ZH_TRANSLATE_MARKER = re.compile(r"(?:翻譯成|翻译成|譯成|译成|翻成)\s*([^\s，。,.！!？?]{1,6})")
_ZH_LANG_NAME_MAP: tuple[tuple[str, str], ...] = (
    ("西班牙", "Spanish"),
    ("繁體", "Chinese"),
    ("繁体", "Chinese"),
    ("簡體", "Chinese"),
    ("简体", "Chinese"),
    ("中文", "Chinese"),
    ("國語", "Chinese"),
    ("国语", "Chinese"),
    ("英", "English"),
    ("日", "Japanese"),
    ("韓", "Korean"),
    ("韩", "Korean"),
    ("法", "French"),
    ("德", "German"),
)


def _target_language(intent: str) -> str:
    text = (intent or "").strip()
    for pat, lang in _TARGET_LANG_PATTERNS:
        m = pat.search(text)
        if m:
            if lang:
                return lang
            return m.group(1).capitalize()
    if re.search(r"\btranslate\b", text, re.I) and re.search(r"\benglish\b", text, re.I):
        return "English"
    zh_match = _ZH_TRANSLATE_MARKER.search(text)
    if zh_match:
        snippet = zh_match.group(1)
        for needle, lang in _ZH_LANG_NAME_MAP:
            if needle in snippet:
                return lang
    return "the requested target language"


def _translator_system(system: str, intent: str) -> str:
    lang = _target_language(intent)
    return (
        f"{system}\n\n"
        f"CRITICAL TRANSLATION RULES:\n"
        f"- Translate the ENTIRE excerpt into {lang}.\n"
        f"- Do NOT summarize, condense, analyze, or omit sentences.\n"
        f"- Preserve paragraph breaks, headings, lists, and quotation marks.\n"
        f"- Output ONLY the translation — no preface, no batch labels, no source headers."
    )


def _translation_user_body(intent: str, chunk: str) -> str:
    lang = _target_language(intent)
    return (
        f"Translate every word of the following text into {lang}. "
        f"Do not summarize. Output ONLY the translation body.\n\n{chunk}"
    )


def strip_context_source_headers(text: str) -> str:
    """Remove workspace/PDF labels before LLM sees or echoes them."""
    out = (text or "").strip()
    out = re.sub(r"(?im)^#{1,2}\s*source:\s*[^\n]+\n*", "", out)
    out = re.sub(r"(?im)^source:\s*\d+\s+[^\n]+\n*", "", out)
    out = re.sub(r"(?im)^source:\s*[^\n]+\n*", "", out)
    out = re.sub(r"(?im)^---\s*page\s+\d+\s*---\s*", "", out)
    out = re.sub(r"(?im)\bsource:\s*[^\n]+", "", out)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def finalize_translation_output(text: str) -> str:
    """Post-process streamed or batched translation for chat display."""
    out = _strip_batch_artifacts(text)
    out = _collapse_repeated_lines(out)
    out = _dedupe_paragraphs(out)
    blocks = [b.strip() for b in re.split(r"(?im)(?:^|\n)source:\s*[^\n]+\s*", out) if b.strip()]
    if len(blocks) >= 2:
        keys = [" ".join(b.lower().split())[:400] for b in blocks]
        from collections import Counter

        common, count = Counter(keys).most_common(1)[0]
        if count >= 2 and len(common) > 40:
            for b in blocks:
                if " ".join(b.lower().split())[:400] == common:
                    return b
    return out


def _strip_batch_artifacts(text: str) -> str:
    """Remove batch/source labels the model may echo into chat output."""
    out = strip_context_source_headers(text)
    out = re.sub(r"(?im)^part\s+\d+\s+of\s+\d+\s*$", "", out)
    out = re.sub(r"(?im)^batch\s+\d+\s+of\s+\d+\s*$", "", out)
    out = re.sub(r"(?im)^workspace excerpt[^\n]*$", "", out)
    out = re.sub(r"\[internal batch \d+/\d+[^\]]*\]", "", out, flags=re.I)
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def _collapse_repeated_lines(text: str, *, min_repeats: int = 3) -> str:
    """Drop runaway repeated lines (common with small models at chunk end)."""
    lines = (text or "").splitlines()
    if len(lines) < min_repeats:
        return (text or "").strip()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        key = " ".join(line.lower().split())
        if key and len(key) > 20:
            j = i + 1
            while j < len(lines) and " ".join(lines[j].lower().split()) == key:
                j += 1
            if j - i >= min_repeats:
                out.append(line)
                i = j
                continue
        out.append(line)
        i += 1
    return "\n".join(out).strip()


def _dedupe_paragraphs(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        return ""
    paras = [p.strip() for p in raw.split("\n\n") if p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    seen_prefixes: list[str] = []
    for p in paras:
        key = " ".join(p.lower().split())
        if key in seen:
            continue
        prefix = key[:180]
        if any(
            prefix.startswith(s[:120]) or s.startswith(prefix[:120])
            for s in seen_prefixes
            if len(s) > 80
        ):
            continue
        seen.add(key)
        seen_prefixes.append(key)
        out.append(p)
    return "\n\n".join(out).strip()


def _dedupe_paragraphs_by_segment(parts: list[str], segment_idx: list[int]) -> str:
    """Dedupe each source document's own output independently, then join documents in
    order. A plain global `_dedupe_paragraphs(joined)` compares paragraphs across every
    document's output — two unrelated documents sharing similar boilerplate/openings is
    common, and that comparison can silently discard real content from the second/third
    document. Falls back to a single group (identical to `_dedupe_paragraphs`) when every
    part shares one segment index, so single-document callers are unaffected."""
    if len(parts) != len(segment_idx):
        return _dedupe_paragraphs("\n\n".join(parts))
    groups: dict[int, list[str]] = {}
    order: list[int] = []
    for part, idx in zip(parts, segment_idx):
        if idx not in groups:
            groups[idx] = []
            order.append(idx)
        groups[idx].append(part)
    blocks = [_dedupe_paragraphs("\n\n".join(groups[idx])) for idx in order]
    return "\n\n".join(b for b in blocks if b).strip()


def _hard_split_chars(text: str, chunk_chars: int) -> list[str]:
    """Force char-budget chunks when there are no paragraph breaks."""
    raw = (text or "").strip()
    if not raw or len(raw) <= chunk_chars:
        return [raw] if raw else []
    chunks: list[str] = []
    start = 0
    while start < len(raw):
        end = min(start + chunk_chars, len(raw))
        if end < len(raw):
            window = raw[start:end]
            cut = max(
                window.rfind("\n\n"),
                window.rfind("。"),
                window.rfind("."),
                window.rfind("！"),
                window.rfind("!"),
                window.rfind("？"),
                window.rfind("?"),
            )
            if cut > chunk_chars // 4:
                end = start + cut + 1
        piece = raw[start:end].strip()
        if piece:
            chunks.append(piece)
        start = end
    return chunks or [raw]


def _split_paragraphs(
    text: str,
    chunk_chars: int,
    *,
    max_paragraphs: int = 10,
) -> list[str]:
    """Split on paragraph boundaries only — no overlap between chunks."""
    raw = (text or "").strip()
    if not raw:
        return []
    if len(raw) <= chunk_chars:
        return [raw]
    paras = [p.strip() for p in raw.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for p in paras:
        plen = len(p) + (2 if buf else 0)
        over_chars = buf and size + plen > chunk_chars
        over_paras = buf and len(buf) >= max_paragraphs
        if over_chars or over_paras:
            chunks.append("\n\n".join(buf))
            buf = [p]
            size = len(p)
        else:
            buf.append(p)
            size += plen
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks or [raw]


def _sink_can_stream(sink: Any) -> bool:
    return sink is not None and hasattr(sink, "append_assistant_token")


def _ensure_assistant(sink: Any) -> None:
    if sink is not None and hasattr(sink, "ensure_assistant_message"):
        sink.ensure_assistant_message()


def _assistant_content_len() -> int:
    from services.session import state

    if state.messages and state.messages[-1].get("role") == "assistant":
        return len(state.messages[-1].get("content") or "")
    return 0


def _run_batch_llm(
    *,
    profile: dict,
    model: str,
    messages: list[dict],
    sink: Any,
    is_cancelled: Callable[[], bool],
    extra_options: dict,
    stream: bool,
    allow_refusal_retry: bool = True,
) -> str:
    """Run one batch — stream tokens to chat when the sink supports it.

    A small/local model occasionally answers a batch/merge call with a "give me the
    content first" refusal instead of doing the task (usually triggered by an unrelated
    system-prompt fragment mentioning the app's own file-saving mechanics). Every batched
    or merged call in the pipeline routes through this one function, so retrying here once
    with a sharper instruction protects all of them without duplicating the check at each
    call site.
    """
    if stream and _sink_can_stream(sink):
        _ensure_assistant(sink)
        prior_len = _assistant_content_len()
        stream_chat_response(
            profile=profile,
            model=model,
            messages=messages,
            sink=sink,
            is_cancelled=is_cancelled,
            disable_thinking=True,
            extra_options=extra_options,
        )
        from services.session import state

        current = ""
        if state.messages and state.messages[-1].get("role") == "assistant":
            current = state.messages[-1].get("content") or ""
        delta = current[prior_len:].strip() if len(current) >= prior_len else current.strip()
        out = _strip_batch_artifacts(delta)

        if allow_refusal_retry and looks_like_refusal(out) and not is_cancelled():
            # Roll the assistant message back to before this batch's (bad) output, so the
            # retry's streamed delta cleanly replaces the refusal instead of appending after it.
            if state.messages and state.messages[-1].get("role") == "assistant":
                state.messages[-1]["content"] = current[:prior_len]
            if hasattr(sink, "refresh_chat"):
                sink.refresh_chat()
            return _run_batch_llm(
                profile=profile,
                model=model,
                messages=_with_refusal_reminder(messages),
                sink=sink,
                is_cancelled=is_cancelled,
                extra_options=extra_options,
                stream=stream,
                allow_refusal_retry=False,
            )
        return out

    out = generate_text_sync(
        profile,
        model,
        messages,
        disable_thinking=True,
        sink=sink,
        extra_options=extra_options,
    )
    out = _strip_batch_artifacts(out)

    if allow_refusal_retry and looks_like_refusal(out) and not is_cancelled():
        return _run_batch_llm(
            profile=profile,
            model=model,
            messages=_with_refusal_reminder(messages),
            sink=sink,
            is_cancelled=is_cancelled,
            extra_options=extra_options,
            stream=stream,
            allow_refusal_retry=False,
        )
    return out


def _with_refusal_reminder(messages: list[dict]) -> list[dict]:
    out = list(messages)
    for i, m in enumerate(out):
        if m.get("role") == "system":
            out[i] = {
                **m,
                "content": (
                    f"{m.get('content', '')}\n\n"
                    "You already have the full source text in this message — do not ask for "
                    "it again and do not describe what you would do. Begin the actual answer now."
                ),
            }
            break
    return out


def _plan_writer_sections(
    *,
    profile: dict,
    model: str,
    system: str,
    step_intent: str,
    context_text: str,
    target_output_chars: int,
) -> list[str]:
    """Short outline call before drafting — keeps each section-generation call
    scoped to one heading instead of one giant from-scratch prompt."""
    approx_sections = max(2, min(8, target_output_chars // 3000))
    plan_prompt = (
        f"Plan an outline for the following writing request, in about {approx_sections} sections.\n"
        f"Output ONLY a numbered list of section headings, one per line — no other text.\n\n"
        f"Request: {step_intent}"
    )
    if context_text:
        plan_prompt += f"\n\nSource context:\n{context_text}"
    out = generate_text_sync(
        profile,
        model,
        [{"role": "system", "content": system}, {"role": "user", "content": plan_prompt}],
        disable_thinking=True,
        sink=None,
        extra_options={"num_ctx": 4096, "num_predict": 512},
    )
    lines = [re.sub(r"^\s*[\d.)-]+\s*", "", ln).strip() for ln in (out or "").splitlines()]
    sections = [ln for ln in lines if ln]
    return sections[:8] or ["Full content"]


def generate_sectioned_writer(
    *,
    step_intent: str,
    context_text: str,
    profile: dict,
    model: str,
    system: str,
    target_output_chars: int,
    sink: Any,
    is_cancelled: Callable[[], bool],
    stream: bool = True,
) -> str:
    """Draft a long from-scratch writer/synthesizer output section-by-section.

    Used only when even this machine's max num_ctx can't fit prompt + expected
    output in one call (see batch_budget.output_exceeds_hardware) — a plain
    single-shot completion would silently truncate regardless of num_predict
    tiering. Reuses the same per-call streaming/dedupe machinery as the
    translator/summarizer batch path above.
    """
    budget = resolve_batch_budget(profile, model)
    sections = _plan_writer_sections(
        profile=profile,
        model=model,
        system=system,
        step_intent=step_intent,
        context_text=context_text,
        target_output_chars=target_output_chars,
    )
    per_section_chars = max(1500, target_output_chars // max(1, len(sections)))
    prompt_chars = len(system) + len(step_intent) + len(context_text) + 500
    extra = llm_extra_options(
        fit_budget_to_prompt(budget, prompt_chars, per_section_chars, profile=profile)
    )

    use_stream = stream and _sink_can_stream(sink)
    if use_stream and sink is not None:
        _ensure_assistant(sink)
        sink.set_assistant_content("")
        sink.refresh_chat()
    sink.log(
        f"Long document: drafting {len(sections)} section(s) "
        f"(~{target_output_chars:,} char target, ctx={extra['num_ctx']:,})"
    )

    parts: list[str] = []
    for i, heading in enumerate(sections):
        if is_cancelled():
            break
        sink.log(f"  section {i + 1}/{len(sections)}: {heading}")
        if use_stream and i > 0:
            sink.append_assistant_token("\n\n")
            sink.refresh_chat_throttled()
        other_headings = [h for j, h in enumerate(sections) if j != i]
        section_prompt = (
            f"{step_intent}\n\n"
            f'Write ONLY the "{heading}" section of this document now — start with a '
            f"'## {heading}' heading. Do not write the other sections "
            f"({', '.join(other_headings) or 'none'}); they are drafted separately."
        )
        if context_text:
            section_prompt += f"\n\nSource context:\n{context_text}"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": section_prompt},
        ]
        out = _run_batch_llm(
            profile=profile,
            model=model,
            messages=messages,
            sink=sink,
            is_cancelled=is_cancelled,
            extra_options=extra,
            stream=use_stream,
        )
        if out.strip():
            parts.append(out.strip())

    if not parts:
        return ""
    merged = _dedupe_paragraphs("\n\n".join(parts))
    if use_stream and merged:
        sink.set_assistant_content(merged)
        sink.refresh_chat()
    return merged


def _label_parts(parts: list[str], labels: list[str] | None) -> str:
    if not labels:
        return "\n\n---\n\n".join(parts)
    return "\n\n---\n\n".join(
        f"### Source: {label}\n{part}" if label else part
        for part, label in zip(parts, labels)
    )


def _merge_partial_summaries(
    parts: list[str],
    *,
    profile: dict,
    model: str,
    system: str,
    step_intent: str,
    budget: dict,
    sink: Any,
    is_cancelled: Callable[[], bool],
    use_stream: bool,
    segment_labels: list[str] | None = None,
) -> str:
    """Merge partial batch summaries into one result, sizing num_ctx to the
    actual merge prompt (widening past the speed-tier default up to this
    machine's hardware ceiling when needed) instead of reusing the fixed
    per-chunk budget. If even the hardware ceiling can't hold every part in
    one call, reduce them in pairs first — so a document with many batches
    never overflows the merge step, it just merges in more stages.

    `segment_labels` (optional): the source-document name each part in `parts`
    came from, same length/order as `parts`. When given (2+ distinct labels),
    each part is tagged "### Source: {label}" in the merge prompt and the
    instruction explicitly demands coverage of every labeled source — without
    this, a lossy multi-stage reduce (especially in pairs) has no signal this
    is a multi-document task and silently drifts toward whichever source reads
    most coherently, dropping the others."""
    current = list(parts)
    labels = list(segment_labels) if segment_labels and len(set(segment_labels)) >= 2 else None
    coverage_note = ""
    if labels:
        all_sources = ", ".join(dict.fromkeys(labels))
        coverage_note = (
            f"\n\nThe partial summaries below are labeled by source document "
            f"({all_sources}). Preserve and cover every labeled source in your "
            f"merged result — do not focus on only one source and omit the others."
        )
    stage = 0
    while len(current) > 1:
        if is_cancelled():
            return "\n\n".join(current)
        merge_prompt = user_step_content(
            user_query=(
                f"{step_intent}\n\nMerge these partial summaries into one "
                f"coherent result.{coverage_note}"
            ),
            step_body=_label_parts(current, labels),
        )
        prompt_chars = len(system) + len(merge_prompt)
        if prompt_fits_hardware(budget, prompt_chars):
            sized = fit_budget_to_prompt(
                budget,
                prompt_chars,
                expected_output_chars=sum(len(p) for p in current) // 2,
                profile=profile,
            )
            extra = llm_extra_options(sized)
            if sink is not None:
                sink.log(f"  merging {len(current)} partial summaries… (ctx={extra['num_ctx']:,})")
            if use_stream and sink is not None:
                sink.append_assistant_token("\n\n")
                sink.refresh_chat_throttled()
            merged = _run_batch_llm(
                profile=profile,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": merge_prompt},
                ],
                sink=sink,
                is_cancelled=is_cancelled,
                extra_options=extra,
                stream=use_stream,
            )
            return merged.strip() if merged.strip() else "\n\n".join(current)

        stage += 1
        if sink is not None:
            sink.log(
                f"  merge stage {stage}: {len(current)} partial summaries exceed this "
                "machine's max context in one call — merging pairs first"
            )
        next_round: list[str] = []
        next_labels: list[str] | None = [] if labels else None
        i = 0
        while i < len(current):
            if is_cancelled():
                return "\n\n".join(current)
            if i + 1 >= len(current):
                next_round.append(current[i])
                if next_labels is not None:
                    next_labels.append(labels[i])
                break
            pair = current[i : i + 2]
            pair_labels = labels[i : i + 2] if labels else None
            pair_prompt = user_step_content(
                user_query=(
                    f"{step_intent}\n\nMerge these partial summaries into one "
                    f"coherent result.{coverage_note}"
                ),
                step_body=_label_parts(pair, pair_labels),
            )
            pair_chars = len(system) + len(pair_prompt)
            pair_budget = fit_budget_to_prompt(
                budget,
                pair_chars,
                expected_output_chars=sum(len(p) for p in pair) // 2,
                profile=profile,
            )
            out = _run_batch_llm(
                profile=profile,
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": pair_prompt},
                ],
                sink=sink,
                is_cancelled=is_cancelled,
                extra_options=llm_extra_options(pair_budget),
                stream=False,
            )
            next_round.append(out.strip() if out.strip() else "\n\n".join(pair))
            if next_labels is not None:
                next_labels.append(" + ".join(dict.fromkeys(pair_labels)))
            i += 2
        current = next_round
        labels = next_labels

    return current[0] if current else ""


def maybe_batched_transform(
    context_text: str,
    *,
    role_id: str,
    step_intent: str,
    profile: dict,
    model: str,
    system: str,
    sink: Any,
    is_cancelled: Callable[[], bool],
    stream: bool = True,
    source_segments: list[tuple[str, str]] | None = None,
) -> str:
    """
    Process long context in paragraph batches; stream each batch to chat.
    Returns merged output, or \"\" to defer to a single-pass stream call.

    `source_segments` (optional): the same context as `context_text`, but as (name, text)
    per source document instead of one concatenated blob. When given (2+ documents), each
    document is chunked independently so no chunk ever spans a document boundary, and the
    translator's dedupe pass runs per-document instead of over the whole merged result —
    a single global dedupe pass previously could (and did) discard real paragraphs from the
    second/third document just because they resembled something already emitted for the
    first. Falls back to the original single-blob behavior when omitted or single-source.
    """
    raw = strip_context_source_headers((context_text or "").strip())
    if not raw:
        return ""

    budget = resolve_batch_budget(profile, model)
    chunk_chars = batch_chunk_chars(budget)
    if role_id == "translator":
        chunk_chars = min(chunk_chars, 2_000)
    max_paras = max_paragraphs_per_chunk(budget)
    extra = llm_extra_options(budget)

    pass_limit = single_pass_char_limit(budget)
    if role_id == "translator" and len(raw) > 1_800:
        pass_limit = 0
    if len(raw) <= pass_limit:
        return ""

    need_hard = len(raw) > single_pass_char_limit(budget) or (
        role_id == "translator" and len(raw) > 1_800
    )

    segments = [(n, t) for n, t in (source_segments or []) if (t or "").strip()]
    chunk_segment_idx: list[int] = []
    if len(segments) >= 2:
        chunks = []
        for seg_i, (_name, seg_text) in enumerate(segments):
            seg_raw = strip_context_source_headers(seg_text.strip())
            if not seg_raw:
                continue
            seg_chunks = _split_paragraphs(seg_raw, chunk_chars, max_paragraphs=max_paras)
            if len(seg_chunks) <= 1 and need_hard and len(seg_raw) > chunk_chars:
                seg_chunks = _hard_split_chars(seg_raw, chunk_chars)
            chunks.extend(seg_chunks)
            chunk_segment_idx.extend([seg_i] * len(seg_chunks))
    else:
        chunks = _split_paragraphs(raw, chunk_chars, max_paragraphs=max_paras)
        if len(chunks) <= 1 and need_hard:
            chunks = _hard_split_chars(raw, chunk_chars)
        chunk_segment_idx = [0] * len(chunks)
    if len(chunks) <= 1:
        return ""

    use_stream = stream and _sink_can_stream(sink)
    if use_stream and sink is not None:
        _ensure_assistant(sink)
        if hasattr(sink, "set_assistant_content"):
            sink.set_assistant_content("")
            sink.refresh_chat()
    for note in budget.get("budget_notes") or []:
        sink.log(f"  batch budget: {note}")
    sink.log(
        f"Long document: {len(chunks)} batch(es) "
        f"({chunk_chars:,} chars/batch, ctx={extra['num_ctx']:,})"
    )

    batch_system = _translator_system(system, step_intent) if role_id == "translator" else system
    parts: list[str] = []
    part_segment_idx: list[int] = []

    for i, chunk in enumerate(chunks):
        if is_cancelled():
            break
        sink.log(f"  batch {i + 1}/{len(chunks)} ({len(chunk):,} chars)")
        if use_stream and i > 0:
            sink.append_assistant_token("\n\n")
            sink.refresh_chat_throttled()

        if role_id == "translator":
            user_content = _translation_user_body(step_intent, chunk)
        else:
            user_content = user_step_content(
                user_query=step_intent,
                step_body=chunk,
            )
        messages = [
            {"role": "system", "content": batch_system},
            {"role": "user", "content": user_content},
        ]
        out = _run_batch_llm(
            profile=profile,
            model=model,
            messages=messages,
            sink=sink,
            is_cancelled=is_cancelled,
            extra_options=extra,
            stream=use_stream,
        )
        if (
            role_id == "translator"
            and _target_language(step_intent) != "Chinese"
            and _looks_untranslated(out, chunk)
        ):
            sink.log(f"  batch {i + 1}/{len(chunks)} came back untranslated — retrying")
            strict_system = (
                f"{batch_system}\n"
                "- Your previous attempt returned this text UNCHANGED in the original "
                "language. Translate every sentence of it now — do not echo the source."
            )
            retry_out = _run_batch_llm(
                profile=profile,
                model=model,
                messages=[
                    {"role": "system", "content": strict_system},
                    {"role": "user", "content": user_content},
                ],
                sink=sink,
                is_cancelled=is_cancelled,
                extra_options=extra,
                stream=use_stream,
            )
            if retry_out.strip() and not _looks_untranslated(retry_out, chunk):
                out = retry_out
        if out.strip():
            parts.append(out.strip())
            part_segment_idx.append(chunk_segment_idx[i] if i < len(chunk_segment_idx) else 0)

    if not parts:
        return ""

    merged = "\n\n".join(parts)
    if role_id == "translator":
        merged = _dedupe_paragraphs_by_segment(parts, part_segment_idx)
        src_len = len(raw)
        if src_len > 0 and len(merged) < int(src_len * 0.45):
            sink.log("  translator output too short — retrying batches in literal mode")
            retry_parts: list[str] = []
            retry_seg_idx: list[int] = []
            strict = batch_system + "\n- Literal word-for-word translation required."
            for i, chunk in enumerate(chunks):
                if is_cancelled():
                    break
                if use_stream and i == 0:
                    sink.set_assistant_content("")
                    sink.refresh_chat()
                elif use_stream and i > 0:
                    sink.append_assistant_token("\n\n")
                    sink.refresh_chat_throttled()
                body = _translation_user_body(step_intent, chunk)
                out = _run_batch_llm(
                    profile=profile,
                    model=model,
                    messages=[
                        {"role": "system", "content": strict},
                        {"role": "user", "content": body},
                    ],
                    sink=sink,
                    is_cancelled=is_cancelled,
                    extra_options=extra,
                    stream=use_stream,
                )
                if out.strip():
                    retry_parts.append(out.strip())
                    retry_seg_idx.append(chunk_segment_idx[i] if i < len(chunk_segment_idx) else 0)
            if retry_parts:
                merged = _dedupe_paragraphs_by_segment(retry_parts, retry_seg_idx)

    elif role_id in ("summarizer", "synthesizer", "writer", "general_answer") and len(parts) > 1:
        part_labels = (
            [segments[idx][0] for idx in part_segment_idx] if len(segments) >= 2 else None
        )
        merged = _merge_partial_summaries(
            parts,
            profile=profile,
            model=model,
            system=system,
            step_intent=step_intent,
            budget=budget,
            sink=sink,
            is_cancelled=is_cancelled,
            use_stream=use_stream,
            segment_labels=part_labels,
        )
        if not merged.strip():
            merged = "\n\n".join(parts)

    if role_id in ("translator", "summarizer", "synthesizer"):
        merged = _dedupe_paragraphs(merged)
    if role_id == "translator":
        merged = _collapse_repeated_lines(merged)

    if role_id == "translator":
        merged = finalize_translation_output(merged)

    if use_stream and merged:
        sink.set_assistant_content(merged)
        sink.refresh_chat()

    return merged
