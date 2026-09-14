# -*- coding: utf-8 -*-
"""Search result formatting and highlighting."""
from __future__ import annotations

from extensions.knowledge_vault.index.text_match import (
    Significant,
    _MIN_COVERAGE,
    highlight_spans as _highlight_spans,
    is_cjk_query as _is_cjk_query,
    phrase_match_coverage,
    query_phrases as _query_phrases,
)

_SNIP_MAX = 160

# Re-exported for existing importers (index/lexical.py, tests): the actual
# implementation lives in index/text_match.py, shared with BM25 indexing so
# there's one definition of "meaningful matching unit" instead of two that
# can drift apart across languages.
__all__ = [
    "phrase_match_coverage",
    "excerpt_snippet",
    "highlight_terms",
    "format_no_hits_message",
    "format_search_chat",
]


def excerpt_snippet(text: str, query: str, *, max_len: int = _SNIP_MAX) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    pos = 0
    q = (query or "").strip()
    if q and q in text:
        pos = text.find(q)
    else:
        best_len = 0
        for phrase in _query_phrases(query):
            idx = text.find(phrase)
            if idx >= 0 and len(phrase) > best_len:
                best_len = len(phrase)
                pos = idx
    half = max_len // 2
    start = max(0, pos - half)
    end = min(len(text), start + max_len)
    if end - start < max_len:
        start = max(0, end - max_len)
    snippet = text[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def highlight_terms(text: str, query: str, *, significant: Significant | None = None) -> str:
    spans = _highlight_spans(text, query, significant=significant)
    if not spans:
        return text
    parts: list[str] = []
    last = 0
    for start, end in spans:
        parts.append(text[last:start])
        parts.append(f'<mark class="loma-search-hit">{text[start:end]}</mark>')
        last = end
    parts.append(text[last:])
    return "".join(parts)


from extensions.knowledge_vault.ui.path_links import format_file_path_links


def format_no_hits_message(
    *,
    task: str = "search",
    mode: str = "fast",
    semantic_suggest: bool = False,
) -> str:
    from pipeline.i18n import t as tr

    lines = [tr("knowledge_vault.search_no_hits")]
    if task == "search":
        lines.append(tr("knowledge_vault.search_no_hits_try_ask"))
    if mode == "fast":
        lines.append(tr("knowledge_vault.search_no_hits_try_deep"))
    if semantic_suggest:
        lines.append(tr("knowledge_vault.search_no_hits_try_semantic"))
    return "\n\n".join(lines)


def format_search_chat(
    query: str,
    rows: list[dict],
    *,
    mode: str = "fast",
    semantic_suggest: bool = False,
) -> str:
    from pipeline.i18n import t as tr

    n = len(rows)
    lines = [
        tr("knowledge_vault.search_header", count=n),
        "",
        tr("knowledge_vault.search_query", query=query),
        "",
    ]
    if not rows:
        lines.append(format_no_hits_message(task="search", mode=mode, semantic_suggest=semantic_suggest))
        return "\n".join(lines)
    for i, row in enumerate(rows, start=1):
        if i > 1:
            lines.append("---")
            lines.append("")
        lines.append(
            tr(
                "knowledge_vault.search_result_line",
                index=i,
                file=row["file_name"],
                page=row["page"],
                score=row["score"],
            )
        )
        path_block = format_file_path_links(row["path"])
        if path_block:
            lines.append(path_block)
        lines.append("")
        lines.append(row.get("snippet_highlighted") or row["snippet"])
        lines.append("")
    return "\n".join(lines).strip()
