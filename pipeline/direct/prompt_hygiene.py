# -*- coding: utf-8 -*-
"""Keep roles/contracts in system messages only — never in user-visible prompts."""
from __future__ import annotations

import re

# Small/local models that receive a document-writing task but get confused (usually because
# a system prompt mentions the app's own file-saving mechanics — see profile_pack.py /
# deliverables/contracts.py) tend to fall back to a canned "give me the content" refusal
# instead of doing the task. Catching that pattern lets a caller retry once instead of
# silently shipping the refusal as if it were the real answer.
_REFUSAL_PATTERNS = tuple(
    re.compile(p, re.I)
    for p in (
        r"\bcannot\s+(?:directly\s+)?generate\b",
        r"\bcan'?t\s+(?:directly\s+)?generate\b",
        r"\bunable\s+to\s+generate\b",
        r"\bplease\s+(?:paste|provide|share)\b",
        r"\bi\s+am\s+ready\s+to\b",
        r"\bonce\s+you\s+provide\b",
        r"\bshare\s+the\s+(?:extracted|relevant)\b",
        r"\bwaiting\s+for\s+(?:you|the)\s+to\s+(?:provide|paste|share)\b",
    )
)


def looks_like_refusal(text: str) -> bool:
    """True if `text` reads like a "give me the content first" non-answer rather than the
    actual requested content. Model this on `_looks_untranslated` in batch_processor.py —
    same shape: a cheap heuristic a caller uses to decide whether to retry once."""
    body = (text or "").strip()
    if not body:
        return False
    return any(pat.search(body) for pat in _REFUSAL_PATTERNS)


def user_step_content(*, user_query: str, step_body: str = "", working_text: str = "") -> str:
    """Build user message body without role/contract metadata.

    `step_body` is the content to act on (a document chunk, partial summaries to
    merge, ...); `user_query` is the instruction. Callers whose step_body already
    has the instruction baked in as its first line (build_step_input_payload does
    this) avoid a duplicate automatically via the startswith check below — every
    other caller was silently losing the instruction entirely whenever step_body
    was non-empty, which is what let batched/merged LLM calls run with no task
    attached to the pasted content.
    """
    query = (user_query or "").strip()
    body = (step_body or "").strip()
    parts: list[str] = []
    if body:
        if query and not body.startswith(query):
            parts.append(query)
        parts.append(body)
    elif query:
        parts.append(query)
    if working_text:
        wt = working_text.strip()
        if wt and wt not in body and "Input from previous step:" not in body:
            parts.append(f"Previous step output:\n{wt}")
    return "\n\n".join(parts) if parts else (user_query or "").strip()
