# -*- coding: utf-8 -*-
"""Translate mutation units without JSON planning (matches chat translate quality)."""
from __future__ import annotations

from services.session import state


def _translate_once(
    *,
    instruction: str,
    chunk: str,
    model_name: str,
    extra_options: dict,
) -> str:
    from pipeline.capability_runtime.chat_runner import generate_text_sync
    from pipeline.direct.batch_processor import (
        _looks_untranslated,
        _target_language,
        _translation_user_body,
        _translator_system,
        finalize_translation_output,
    )

    system = _translator_system("", instruction)
    user_content = _translation_user_body(instruction, chunk)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    out = generate_text_sync(
        {},
        model_name,
        messages,
        disable_thinking=True,
        extra_options=extra_options,
    )
    if _target_language(instruction) != "Chinese" and _looks_untranslated(out, chunk):
        # Same per-chunk echo-back failure as the direct pipeline's batch translator
        # (small local models occasionally return one chunk verbatim instead of
        # translating it) — retry this one chunk with an explicit correction before
        # accepting it, instead of silently keeping the untranslated source.
        state.add_log("  translate unit chunk came back untranslated — retrying")
        strict_system = (
            f"{system}\n"
            "- Your previous attempt returned this text UNCHANGED in the original "
            "language. Translate every sentence of it now — do not echo the source."
        )
        retry_out = generate_text_sync(
            {},
            model_name,
            [
                {"role": "system", "content": strict_system},
                {"role": "user", "content": user_content},
            ],
            disable_thinking=True,
            extra_options=extra_options,
        )
        if retry_out.strip() and not _looks_untranslated(retry_out, chunk):
            out = retry_out
    return finalize_translation_output(out)


def _translate_long_text(
    text: str,
    *,
    instruction: str,
    model_name: str,
    extra_options: dict,
    chunk_chars: int,
    max_paragraphs: int,
) -> str:
    from pipeline.direct.batch_processor import (
        _hard_split_chars,
        _split_paragraphs,
        strip_context_source_headers,
    )

    clean = strip_context_source_headers(text)
    chunks = _split_paragraphs(clean, chunk_chars, max_paragraphs=max_paragraphs)
    if len(chunks) <= 1 and len(clean) > chunk_chars:
        chunks = _hard_split_chars(clean, chunk_chars)
    parts: list[str] = []
    for i, chunk in enumerate(chunks):
        state.add_log(f"  translate unit chunk {i + 1}/{len(chunks)} ({len(chunk):,} chars)")
        out = _translate_once(
            instruction=instruction,
            chunk=chunk,
            model_name=model_name,
            extra_options=extra_options,
        )
        if out.strip():
            parts.append(out.strip())
    return "\n\n".join(parts).strip()


def translate_units_map(
    units: list[dict],
    instruction: str,
    model_name: str,
    *,
    profile: dict | None = None,
) -> dict[str, str]:
    """
    Return id→translated text for mutation apply.
    Selective language pairs use span replacement; full translate uses per-unit LLM calls.
    """
    from services.selective_translate import apply_selective_to_units, has_explicit_source_lang_pair
    from pipeline.direct.batch_budget import (
        batch_chunk_chars,
        llm_extra_options,
        max_paragraphs_per_chunk,
        resolve_batch_budget,
    )

    if has_explicit_source_lang_pair(instruction):
        state.add_log(f"Mutation translate: selective spans ({len(units)} fragment(s))")
        return apply_selective_to_units(units, instruction, model=model_name)

    budget = resolve_batch_budget(profile, model_name)
    extra = llm_extra_options(budget)
    chunk_chars = min(batch_chunk_chars(budget), 2_000)
    max_paras = max_paragraphs_per_chunk(budget)
    state.add_log(
        f"Mutation translate: full text per fragment ({len(units)} unit(s), "
        f"{chunk_chars:,} chars/chunk, ctx={extra['num_ctx']:,})"
    )

    out: dict[str, str] = {}
    for u in units:
        uid = u["id"]
        orig = str(u.get("text") or u.get("value") or "")
        if not orig.strip():
            out[uid] = orig
            continue
        if len(orig) <= chunk_chars:
            translated = _translate_once(
                instruction=instruction,
                chunk=orig,
                model_name=model_name,
                extra_options=extra,
            )
            out[uid] = translated.strip() or orig
        else:
            translated = _translate_long_text(
                orig,
                instruction=instruction,
                model_name=model_name,
                extra_options=extra,
                chunk_chars=chunk_chars,
                max_paragraphs=max_paras,
            )
            out[uid] = translated or orig
    return out
