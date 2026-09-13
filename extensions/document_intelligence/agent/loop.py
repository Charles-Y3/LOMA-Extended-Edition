# -*- coding: utf-8 -*-
"""Agent mode investigation loop."""
from __future__ import annotations

import json
import re
from typing import Callable

from extensions.document_intelligence.corpus.types import ReasoningMode
from extensions.document_intelligence.retrieval.analyze_action import run_analyze
from extensions.document_intelligence.retrieval.engine import CorpusBackend
from extensions.document_intelligence.settings import load_settings
from extensions.ludicity_shared.llm import ludicity_chat
from pipeline.i18n import t as tr

StopFn = Callable[[], bool]


def _review_sufficiency(query: str, answer: str, iteration: int) -> dict:
    prompt = (
        "Review whether the answer sufficiently addresses the query using retrieved evidence.\n"
        'Return JSON only: {"sufficient": bool, "confidence": 0-1, "follow_up_queries": ["..."]}\n\n'
        f"Query: {query}\n\nDraft answer:\n{answer}\n\nIteration: {iteration}"
    )
    try:
        raw = ludicity_chat(
            [
                {"role": "system", "content": "You output JSON only."},
                {"role": "user", "content": prompt},
            ]
        )
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            return json.loads(m.group())
    except Exception:
        pass
    return {"sufficient": True, "confidence": 0.8, "follow_up_queries": []}


def run_agent(
    backend: CorpusBackend,
    query: str,
    *,
    branch: str = "",
    library_id: str | None = None,
    semantic_ready: bool = False,
    should_stop: StopFn | None = None,
    on_chunk: Callable[[str], None] | None = None,
) -> str:
    """Multi-pass investigation — each pass uses analyse-tier retrieval settings
    (deep_select, single-hop neighbor expansion, no whole-file/section-merge/recursive
    extras) but keeps AGENT's LLM-driven query expansion for richer per-pass rewrites."""
    cfg = load_settings()
    max_iter = int(cfg.get("max_agent_iterations") or 5)
    threshold = float(cfg.get("agent_confidence_threshold") or 0.75)
    current_query = query
    best_answer = ""
    iterations = 0

    for i in range(max_iter):
        iterations = i + 1
        if should_stop and should_stop():
            break
        answer = run_analyze(
            backend,
            current_query,
            branch=branch,
            mode=ReasoningMode.AGENT,
            library_id=library_id,
            semantic_ready=semantic_ready,
            deep_extras=False,
            on_chunk=on_chunk,
        )
        best_answer = answer
        review = _review_sufficiency(query, answer, i + 1)
        if review.get("sufficient") and float(review.get("confidence") or 0) >= threshold:
            break
        followups = [str(q).strip() for q in (review.get("follow_up_queries") or []) if str(q).strip()]
        if not followups:
            break
        current_query = followups[0]

    header = tr("di.agentic_header", iterations=iterations)
    return header + best_answer
