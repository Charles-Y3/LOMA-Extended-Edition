# -*- coding: utf-8 -*-
"""Query expansion for Deep and Agent modes."""
from __future__ import annotations

import json
import re

from extensions.knowledge_vault.index.lexical import tokenize


def rule_expand(query: str) -> list[str]:
    base = (query or "").strip()
    if not base:
        return []
    terms = tokenize(base)
    variants = [base]
    if len(terms) > 1:
        variants.append(" ".join(terms))
        variants.extend(terms[:5])
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        k = v.lower()
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out


def llm_expand(query: str) -> list[str]:
    from extensions.ludicity_shared.llm import ludicity_chat

    prompt = (
        "Expand this search query into 3-6 short alternative phrases for document retrieval. "
        'Return JSON only: {"queries":["..."]}\n\nQuery: '
        + query
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
            data = json.loads(m.group())
            extra = [str(q).strip() for q in (data.get("queries") or []) if str(q).strip()]
            return rule_expand(query) + [q for q in extra if q.lower() != query.lower()]
    except Exception:
        pass
    return rule_expand(query)
