# -*- coding: utf-8 -*-
"""Context selection for LLM tasks."""
from __future__ import annotations

from extensions.document_intelligence.corpus.types import ChunkRecord, HitRecord
from extensions.document_intelligence.settings import load_settings


def fast_select(hits: list[HitRecord]) -> list[HitRecord]:
    """Top 1 if dominant else top 3, max 2 per document."""
    if not hits:
        return []
    cfg = load_settings()
    gap_ratio = float(cfg.get("fast_score_gap_ratio") or 1.5)
    max_per_doc = int(cfg.get("chunks_per_document") or 2)

    sorted_hits = sorted(hits, key=lambda h: h.score, reverse=True)
    if len(sorted_hits) == 1:
        return sorted_hits
    top, second = sorted_hits[0], sorted_hits[1]
    if second.score <= 0 or top.score / second.score >= gap_ratio:
        return [top]

    out: list[HitRecord] = []
    per_doc: dict[str, int] = {}
    for h in sorted_hits:
        src = h.chunk.file_path or h.chunk.source
        if per_doc.get(src, 0) >= max_per_doc:
            continue
        out.append(h)
        per_doc[src] = per_doc.get(src, 0) + 1
        if len(out) >= 3:
            break
    return out


def deep_select(hits: list[HitRecord], *, depth: int | None = None) -> list[HitRecord]:
    cfg = load_settings()
    target = depth or int(cfg.get("retrieval_depth") or 40)
    max_sources = int(cfg.get("max_sources") or 5)
    max_per_doc = int(cfg.get("chunks_per_document") or 2)
    sorted_hits = sorted(hits, key=lambda h: h.score, reverse=True)
    out: list[HitRecord] = []
    per_doc: dict[str, int] = {}
    sources: set[str] = set()
    for h in sorted_hits:
        src = h.chunk.file_path or h.chunk.source
        if len(sources) >= max_sources and src not in sources:
            continue
        if per_doc.get(src, 0) >= max_per_doc:
            continue
        out.append(h)
        per_doc[src] = per_doc.get(src, 0) + 1
        sources.add(src)
        if len(out) >= target:
            break
    return out


_CHARS_PER_TOKEN = 3.5


def _by_file_sorted(all_chunks: list[ChunkRecord]) -> dict[str, list[ChunkRecord]]:
    """Every chunk of every file, sorted by segment_index — shared by
    expand_neighbor_chunks and merge_same_section, both of which need a file's full
    chunk list to look up siblings of a hit."""
    by_file: dict[str, list[ChunkRecord]] = {}
    for ch in all_chunks:
        key = ch.file_path or ch.source
        by_file.setdefault(key, []).append(ch)
    for chunks in by_file.values():
        chunks.sort(key=lambda c: c.segment_index)
    return by_file


def _match_offset(query: str, text: str) -> int:
    q = (query or "").strip()
    if not q or not text:
        return 0
    if q in text:
        return text.find(q)
    best_len = 0
    best_pos = 0
    qlen = len(q)
    for length in range(qlen, 1, -1):
        for start in range(qlen - length + 1):
            sub = q[start : start + length]
            if len(sub) < 2:
                continue
            idx = text.find(sub)
            if idx >= 0 and length > best_len:
                best_len = length
                best_pos = idx
    return best_pos


def expand_neighbor_chunks(
    hits: list[HitRecord],
    all_chunks: list[ChunkRecord],
    query: str,
) -> list[HitRecord]:
    """Include previous/next segment when the match sits in the top/bottom 25% of a chunk."""
    if not hits or not all_chunks:
        return hits
    by_file = _by_file_sorted(all_chunks)

    seen = {h.chunk.chunk_id for h in hits}
    extras: list[HitRecord] = []
    for h in hits:
        ch = h.chunk
        siblings = by_file.get(ch.file_path or ch.source) or []
        idx = next((i for i, c in enumerate(siblings) if c.chunk_id == ch.chunk_id), -1)
        if idx < 0:
            continue
        text = ch.text or ""
        rel = _match_offset(query, text) / max(len(text), 1)
        if rel <= 0.25 and idx > 0:
            prev = siblings[idx - 1]
            if prev.chunk_id not in seen and not prev.is_low_content:
                seen.add(prev.chunk_id)
                extras.append(
                    HitRecord(
                        chunk=prev,
                        score=h.score * 0.85,
                        snippet=prev.text[:280],
                    )
                )
        if rel >= 0.75 and idx < len(siblings) - 1:
            nxt = siblings[idx + 1]
            if nxt.chunk_id not in seen and not nxt.is_low_content:
                seen.add(nxt.chunk_id)
                extras.append(
                    HitRecord(
                        chunk=nxt,
                        score=h.score * 0.85,
                        snippet=nxt.text[:280],
                    )
                )
    if not extras:
        return hits
    merged = list(hits) + extras
    merged.sort(key=lambda x: (x.chunk.file_path, x.chunk.segment_index))
    return merged


def whole_file_fallback(
    selected: list[HitRecord],
    all_chunks: list[ChunkRecord],
    *,
    ctx_budget_tokens: int,
    fraction: float = 0.6,
) -> tuple[list[HitRecord], set[str]]:
    """Replace a file's selected chunks with its ENTIRE text when the whole file fits
    comfortably in the context budget — sidesteps chunk-boundary information loss for
    small-to-medium files entirely, rather than hoping neighbor/section expansion
    happens to reconstruct enough of it. Returns (new_selected, whole_filed_keys) so
    later steps (section merge, recursive neighbor expansion) can skip files already
    fully included."""
    if not selected:
        return selected, set()
    by_file = _by_file_sorted(all_chunks)
    budget_chars = ctx_budget_tokens * fraction * _CHARS_PER_TOKEN

    out: list[HitRecord] = []
    whole_filed: set[str] = set()
    for key, group in group_hits_by_file(selected):
        siblings = [c for c in by_file.get(key, []) if not c.is_low_content]
        total_chars = sum(len(c.text or "") for c in siblings)
        if siblings and total_chars <= budget_chars:
            base_score = max(h.score for h in group)
            out.extend(
                HitRecord(chunk=c, score=base_score, snippet=c.text[:280]) for c in siblings
            )
            whole_filed.add(key)
        else:
            out.extend(group)
    return out, whole_filed


def merge_same_section(
    selected: list[HitRecord],
    all_chunks: list[ChunkRecord],
    *,
    skip_keys: set[str],
    max_extra_per_hit: int = 6,
) -> list[HitRecord]:
    """Pull in every other chunk from the same file sharing a hit's section heading — a
    section split across 3+ chunks previously only got one adjacent neighbor at best
    from expand_neighbor_chunks. "General" (the default/unheaded section label) is
    skipped so undifferentiated documents don't get blanket-merged. Skips files already
    fully included by whole_file_fallback."""
    if not selected:
        return selected
    by_file = _by_file_sorted(all_chunks)
    seen = {h.chunk.chunk_id for h in selected}
    extras: list[HitRecord] = []
    for h in selected:
        ch = h.chunk
        key = ch.file_path or ch.source
        if key in skip_keys or not ch.section or ch.section == "General":
            continue
        added = 0
        for sib in by_file.get(key) or []:
            if added >= max_extra_per_hit:
                break
            if sib.chunk_id in seen or sib.is_low_content or sib.section != ch.section:
                continue
            seen.add(sib.chunk_id)
            extras.append(HitRecord(chunk=sib, score=h.score * 0.85, snippet=sib.text[:280]))
            added += 1
    if not extras:
        return selected
    merged = list(selected) + extras
    merged.sort(key=lambda x: (x.chunk.file_path, x.chunk.segment_index))
    return merged


def expand_neighbor_chunks_recursive(
    hits: list[HitRecord],
    all_chunks: list[ChunkRecord],
    query: str,
    *,
    skip_keys: set[str],
    ctx_budget_tokens: int,
    max_passes: int = 4,
) -> list[HitRecord]:
    """Budget-aware wrapper around expand_neighbor_chunks: keeps pulling neighbors
    outward across multiple hops (not just one) while a pass still finds new
    boundary-adjacent chunks and there's spare context budget. Skips files already
    fully included by whole_file_fallback."""
    if not hits or not all_chunks:
        return hits
    budget_chars = ctx_budget_tokens * _CHARS_PER_TOKEN
    current = hits
    for _ in range(max_passes):
        eligible = [h for h in current if (h.chunk.file_path or h.chunk.source) not in skip_keys]
        rest = [h for h in current if (h.chunk.file_path or h.chunk.source) in skip_keys]
        expanded = expand_neighbor_chunks(eligible, all_chunks, query)
        if len(expanded) == len(eligible):
            break
        total_chars = sum(len(h.chunk.text or "") for h in expanded) + sum(
            len(h.chunk.text or "") for h in rest
        )
        if total_chars > budget_chars:
            break
        current = expanded + rest
    return current


def group_hits_by_file(hits: list[HitRecord]) -> list[tuple[str, list[HitRecord]]]:
    groups: dict[str, list[HitRecord]] = {}
    order: list[str] = []
    for h in hits:
        key = h.chunk.file_path or h.chunk.chunk_id
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(h)
    return [(key, groups[key]) for key in order]
