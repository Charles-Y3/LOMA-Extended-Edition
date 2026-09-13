# -*- coding: utf-8 -*-
"""Assemble ContextBundle fields from digests + strategy."""
from __future__ import annotations

from dataclasses import dataclass

from pipeline.context.budget import context_char_budget
from pipeline.context.digest_store import digest_index_markdown, join_digest_full_text
from pipeline.context.strategy import resolve_context_strategy
from pipeline.context.types import ContextStrategy, SourceDigest
from pipeline.context_retrieval import select_workspace_context
from services.types import SourceDocument


@dataclass
class AssembledContext:
    unified_text: str
    digest_index_md: str
    strategy: ContextStrategy
    total_source_chars: int
    char_budget: int
    was_truncated: bool
    selection_mode: str


def _sources_from_digests(digests: list[SourceDigest]) -> list[SourceDocument]:
    out: list[SourceDocument] = []
    for d in digests:
        if d.kind == "image" or not (d.full_text or "").strip():
            continue
        kind = "web" if d.kind == "web" else "document"
        out.append(
            SourceDocument(
                name=d.name,
                text=f"## Source: {d.name}\n\n{d.full_text}",
                source_kind=kind,
            )
        )
    return out


def assemble_workspace_context(
    digests: list[SourceDigest],
    *,
    query: str,
    profile: dict,
    extra_sources: list[SourceDocument] | None = None,
    image_notes: str = "",
) -> AssembledContext:
    total = sum(d.char_count for d in digests)
    budget = context_char_budget(profile)
    strategy = resolve_context_strategy(
        query,
        digests,
        total_chars=total,
        char_budget=budget,
        file_count=len([d for d in digests if d.kind not in ("web", "media")]),
        link_count=len([d for d in digests if d.kind == "web"]),
    )
    index_md = digest_index_markdown(digests, strategy=strategy, total_chars=total)

    sources = _sources_from_digests(digests)
    for s in extra_sources or []:
        sources.append(s)

    was_truncated = False
    selection_mode = strategy

    if strategy == "fit":
        selection = select_workspace_context(sources, query, profile=profile)
        unified = selection.text
        was_truncated = selection.was_truncated
        selection_mode = selection.mode if selection.was_truncated else "fit"
    elif strategy == "retrieve":
        selection = select_workspace_context(sources, query, profile=profile)
        unified = selection.text
        was_truncated = True
        selection_mode = selection.mode or "qa"
    elif strategy == "map_reduce":
        unified = (
            f"{index_md}\n\n"
            "[Full source text will be processed in batches — do not ask the user to paste content.]"
        )
        was_truncated = True
    else:
        # per_source: planners see index + short previews; workers digest each source
        preview_blocks = [index_md, "", "## Per-source previews"]
        for d in digests:
            preview_blocks.append(f"### {d.name} ({d.kind})")
            preview_blocks.append(d.preview or "(no text extracted)")
            preview_blocks.append("")
        unified = "\n".join(preview_blocks).strip()
        was_truncated = total > budget

    if image_notes:
        unified = f"{unified}\n\n{image_notes}" if unified.strip() else image_notes

    return AssembledContext(
        unified_text=unified.strip(),
        digest_index_md=index_md,
        strategy=strategy,
        total_source_chars=total,
        char_budget=budget,
        was_truncated=was_truncated,
        selection_mode=selection_mode,
    )


def full_text_for_map_reduce(digests: list[SourceDigest]) -> str:
    return join_digest_full_text(digests)
