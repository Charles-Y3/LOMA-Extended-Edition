# -*- coding: utf-8 -*-
"""LLM synthesis with citations."""
from __future__ import annotations

import re
from typing import Callable

from extensions.knowledge_vault.corpus.types import HitRecord, ReasoningMode, TaskKind
from extensions.knowledge_vault.retrieval.context_builder import (
    _CHARS_PER_TOKEN,
    group_hits_by_file,
)
from extensions.knowledge_vault.retrieval.search_format import format_no_hits_message
from extensions.knowledge_vault.ui.path_links import format_file_path_links
from extensions.ludicity_shared.llm import ludicity_chat, ludicity_chat_stream


_MAX_LISTED_PAGES = 6


def _page_label(group: list[HitRecord]) -> str:
    """Page(s) actually spanned by a group's chunks — a group can hold more than one
    chunk from the same file (e.g. expand_neighbor_chunks pulling in an adjacent page,
    or "deep" mode's whole-file/section-merge additions pulling in many), so showing
    only the first chunk's page silently hid which extra pages were pulled in.

    A dash range ("p.24–83") is only used when those pages are truly contiguous —
    otherwise it reads as "every page from 24 to 83" when it might really be just
    pages 24 and 83 with nothing in between, so a sparse set is listed explicitly
    instead (capped, to avoid a citation line longer than the answer)."""
    pages = sorted({h.chunk.page_number for h in group if h.chunk.page_number is not None})
    if not pages:
        return "—"
    if len(pages) == 1:
        return str(pages[0])
    if pages[-1] - pages[0] + 1 == len(pages):
        return f"{pages[0]}–{pages[-1]}"
    if len(pages) <= _MAX_LISTED_PAGES:
        return ", ".join(str(p) for p in pages)
    shown = ", ".join(str(p) for p in pages[:_MAX_LISTED_PAGES])
    return f"{shown}, +{len(pages) - _MAX_LISTED_PAGES} more"


def _format_context(hits: list[HitRecord]) -> str:
    parts: list[str] = []
    for i, (_key, group) in enumerate(group_hits_by_file(hits), start=1):
        ch = group[0].chunk
        page = _page_label(group)
        body = "\n\n---\n\n".join(h.chunk.text for h in group)
        parts.append(f"[{i}] {ch.source} (p.{page}) — {ch.file_path}\n{body}")
    return "\n\n".join(parts)


def _fit_hits_to_hardware(
    hits: list[HitRecord], *, model: str, reserve_chars: int
) -> tuple[list[HitRecord], int]:
    """Deep/agent retrieval can pull dozens of chunks across several documents —
    unlike the direct pipeline's document generation, ludicity_chat's own context
    fitting only widens num_ctx up to this machine's hardware ceiling and then
    CAPS it there (no chunking fallback), so a retrieval set too large for that
    ceiling would build one prompt Ollama then hard-rejects. Groups are already
    best-match-first (fast_select/deep_select sort by score), so keep including
    whole file-groups until the next one would exceed the hardware budget, and
    report how many groups had to be dropped.

    Returns (kept_hits, dropped_group_count).
    """
    from extensions.knowledge_vault.retrieval.cpu_budget import (
        effective_ctx_ceiling_tokens,
    )

    hw_ceiling_tokens = effective_ctx_ceiling_tokens(model)
    max_chars = max(1000, int(hw_ceiling_tokens * _CHARS_PER_TOKEN) - reserve_chars)

    groups = list(group_hits_by_file(hits))
    kept: list[HitRecord] = []
    used_chars = 0
    for idx, (_key, group) in enumerate(groups):
        ch = group[0].chunk
        page = _page_label(group)
        body = "\n\n---\n\n".join(h.chunk.text for h in group)
        group_chars = len(f"[{len(kept)}] {ch.source} (p.{page}) — {ch.file_path}\n{body}\n\n")
        if kept and used_chars + group_chars > max_chars:
            # Stop at the first group that doesn't fit rather than skipping ahead —
            # groups are relevance-ranked, so a later, smaller-but-less-relevant group
            # isn't a better use of the remaining budget than just stopping here.
            return kept, len(groups) - idx
        kept.extend(group)
        used_chars += group_chars
    return kept, 0


def _format_citations(indexed_groups: list[tuple[int, tuple]], *, actually_cited: bool = True) -> str:
    # Not a markdown list ("- [n] ...") — some renderers coalesce consecutive
    # "-"-prefixed lines into one list block even across intervening plain
    # paragraphs, which regroups every title together first and every
    # folder/file link after, instead of keeping each citation's link with it.
    # Bold-number style (matching format_search_chat's search results) keeps
    # each entry a standalone paragraph so order is always preserved.
    from pipeline.i18n import t as tr

    header = tr("knowledge_vault.sources_header") if actually_cited else tr("knowledge_vault.sources_header_uncited")
    lines = [header, ""]
    for i, (_key, group) in indexed_groups:
        ch = group[0].chunk
        page = _page_label(group)
        path_block = format_file_path_links(ch.file_path)
        lines.append(f"**[{i}]** **{ch.source}** — p.{page}")
        if path_block:
            lines.append(path_block)
        lines.append("")
    return "\n".join(lines).strip()


def _cited_indices(body: str, count: int) -> set[int]:
    return {n for n in (int(m) for m in re.findall(r"\[(\d+)\]", body)) if 1 <= n <= count}


def synthesize(
    query: str,
    hits: list[HitRecord],
    *,
    task: TaskKind,
    citation_required: bool = True,
    mode: ReasoningMode = ReasoningMode.FAST,
    semantic_suggest: bool = False,
    model: str = "",
    on_chunk: Callable[[str], None] | None = None,
    locale: str = "",
) -> str:
    if not hits:
        return format_no_hits_message(
            task=task.value,
            mode=mode.value,
            semantic_suggest=semantic_suggest,
        )

    # Reserve room for the query/instructions text and the model's own reply —
    # these are small and fixed-ish relative to a deep retrieval's excerpt volume.
    hits, dropped_groups = _fit_hits_to_hardware(hits, model=model, reserve_chars=len(query) + 2000)
    context = _format_context(hits)
    cite_rule = (
        "Cite sources with a space before the bracket (e.g. 實現 [1]。), "
        "using one number per document."
    )
    depth_rule = (
        "Write a substantive answer in full sentences with multiple paragraphs when "
        "the evidence supports it. Explain, interpret, and connect ideas across "
        "excerpts. Never reply with only the question phrase, a single sentence, "
        "or a bare quotation."
    )
    if mode in (ReasoningMode.DEEP, ReasoningMode.AGENT):
        depth_rule += " Provide a detailed, well-structured analysis."
        # Deep/Agentic feed the model a much larger excerpt block than Ask/Analyse
        # (whole-file fallback, neighbor-chunk expansion, section merging) —
        # confirmed empirically that under that much more context, the model can
        # write a full, well-structured answer and cite nothing at all by the end
        # of it, not just miss a source here and there. Restating citation as a
        # hard constraint (not a style note) keeps it from getting lost.
        cite_rule += (
            " This is a hard requirement, not a style preference — every "
            "substantive claim must carry a citation, even in a long answer "
            "drawing on many excerpts."
        )

    # Generic instead of an enumerated language list (the app's fixed UI-locale
    # rule only covers en/zh_tw/zh_cn/es/de) — a KV question can be asked in any
    # language, and the document excerpts themselves may be in yet another
    # language, so the reply must follow the *question*, not the UI setting or
    # the source material's language. This self-judging instruction is unreliable
    # (confirmed: identical Chinese-excerpt-only, no-instruction queries got a
    # Chinese answer from Ask mode and an English one from Analyse mode) whenever
    # the "question" is really just quoted source text with no real instruction to
    # read intent from — callers that can tell this is the case (e.g. the Ask LOMA
    # highlight popup, where the instruction field was left empty and defaulted to
    # the excerpt itself) should pass `locale` so the app's configured language
    # wins instead of leaving it to the model to guess from foreign-language content.
    if locale:
        from pipeline.i18n import language_system_rule

        language_rule = language_system_rule(locale)
    else:
        language_rule = "Respond in the same language the question below is written in."

    # A citation instruction stated only in the system prompt, before a very large
    # excerpts block, is easy for the model to lose sight of by the time it starts
    # writing — repeating it right after the excerpts (closest to generation time)
    # measurably helps under Deep/Agentic's larger context sizes.
    trailing_cite_reminder = (
        f"\n\nRemember: {cite_rule}" if mode in (ReasoningMode.DEEP, ReasoningMode.AGENT) else ""
    )

    if task == TaskKind.ASK:
        system = (
            "You answer questions using only the provided document excerpts. "
            f"{depth_rule} {cite_rule} {language_rule}"
        )
        user = f"Question:\n{query}\n\nExcerpts:\n{context}{trailing_cite_reminder}"
    else:
        system = (
            "You perform multi-document analysis: summarization, comparison, themes, or reports. "
            f"{depth_rule} Use only provided excerpts. {cite_rule} {language_rule}"
        )
        user = f"Analysis request:\n{query}\n\nExcerpts:\n{context}{trailing_cite_reminder}"

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if on_chunk is not None:
        answer = ludicity_chat_stream(
            messages, model=model, on_chunk=on_chunk, apply_locale=False
        )
    else:
        answer = ludicity_chat(messages, model=model, apply_locale=False)
    body = (answer or "").strip()
    if not body or body == query.strip():
        body = (
            "The model returned an empty or minimal reply. "
            "Check that a chat model is configured in your profile and try again."
        )
    if dropped_groups:
        body += (
            f"\n\n*Note: {dropped_groups} lower-ranked source(s) were left out — the "
            "full retrieved set was too large for this machine's context window. "
            "Narrow the query or increase retrieval depth on capable hardware for "
            "more coverage.*"
        )
    if citation_required:
        groups = list(group_hits_by_file(hits))
        # Only show sources the model actually cited ([n] in the body) — group_hits_by_file
        # produces one group per retrieved file, but the model may have ignored some (e.g.
        # a low-signal excerpt it correctly declined to use) despite being told to cite
        # every excerpt it draws on; listing every retrieved group regardless previously
        # made "Sources" claim things that weren't actually used to write the answer.
        cited = _cited_indices(body, len(groups))
        if cited:
            # Dropping the uncited groups leaves gaps in the numbering ([1], [3], no
            # [2]) unless the survivors are renumbered sequentially — but the body's
            # own inline "[3]" markers were written against the ORIGINAL numbering, so
            # renumbering the Sources list alone would just move the mismatch instead
            # of fixing it. Both have to be rewritten together from the same mapping.
            renumber = {old: new for new, old in enumerate(sorted(cited), start=1)}
            body = re.sub(
                r"\[(\d+)\]",
                lambda m: f"[{renumber[int(m.group(1))]}]"
                if int(m.group(1)) in renumber
                else m.group(0),
                body,
            )
            indexed = [(renumber[i], g) for i, g in enumerate(groups, start=1) if i in cited]
        else:
            indexed = list(enumerate(groups, start=1))
        return body + "\n\n" + _format_citations(indexed, actually_cited=bool(cited))
    return body
