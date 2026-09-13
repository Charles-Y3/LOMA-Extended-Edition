# -*- coding: utf-8 -*-
"""Correlate columns across different sheets so they can be overlaid.

Two-stage, schema-only (full row data is never sent to the model):
  1. Heuristic pre-pass ranks candidate column pairs by name similarity, dtype match
     and — for potential join keys — value overlap.
  2. One LLM call refines/labels the candidates into confirmed join keys and
     semantically-matching value-column pairs, returning compact JSON.
The UI then lets the user confirm/edit; nothing here mutates state.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable


@dataclass
class ColumnRef:
    dataset: str          # frame key, e.g. "sales.xlsx::Q1"
    column: str


@dataclass
class MappingPair:
    left: ColumnRef
    right: ColumnRef
    role: str             # "join_key" | "value"
    confidence: float
    note: str = ""        # "exact" marks an authoritative pair the LLM may not drop
    name_sim: float = 0.0
    overlap: float = 0.0


@dataclass
class MappingSuggestion:
    pairs: list[MappingPair] = field(default_factory=list)


def _norm(name: str) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def _name_sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _dtype_kind(series) -> str:
    k = series.dtype.kind
    if k in "if":
        return "number"
    if str(series.dtype).startswith("datetime"):
        return "datetime"
    return "text"


def _value_overlap(sa, sb) -> float:
    """Jaccard overlap of distinct string values — signals a shared key."""
    va = set(sa.dropna().astype(str).str.strip().head(2000))
    vb = set(sb.dropna().astype(str).str.strip().head(2000))
    if not va or not vb:
        return 0.0
    inter = len(va & vb)
    union = len(va | vb)
    return inter / union if union else 0.0


def heuristic_candidates(frames: dict[str, Any], *, max_pairs: int = 40) -> list[MappingPair]:
    """Rank cross-dataset column pairs. Only pairs between *different* datasets.

    Evidence-gated: join keys need real value overlap, value pairs need a strong
    name match on the same dtype kind. Each column joins at most one pair
    (greedy best-match). Pairs with exact names and near-total overlap are marked
    note="exact" — authoritative, never dropped by LLM refinement.
    """
    keys = list(frames.keys())
    scored: list[MappingPair] = []
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            da, db = keys[i], keys[j]
            fa, fb = frames[da], frames[db]
            for ca in fa.columns:
                ka = _dtype_kind(fa[ca])
                for cb in fb.columns:
                    kb = _dtype_kind(fb[cb])
                    name = _name_sim(ca, cb)
                    exact = _norm(str(ca)) == _norm(str(cb))
                    overlap = 0.0
                    if ka == kb or name > 0.7:
                        overlap = _value_overlap(fa[ca], fb[cb])
                    if overlap >= 0.30 or (overlap >= 0.15 and exact):
                        role = "join_key"
                        conf = 0.5 * overlap + 0.5 * name
                    elif name >= 0.75 and ka == kb:
                        role = "value"
                        conf = name
                    else:
                        continue
                    if conf < 0.60:
                        continue
                    scored.append(
                        MappingPair(
                            left=ColumnRef(da, str(ca)),
                            right=ColumnRef(db, str(cb)),
                            role=role,
                            confidence=round(float(conf), 3),
                            note="exact" if exact and overlap >= 0.8 else f"{ka}/{kb}",
                            name_sim=round(float(name), 3),
                            overlap=round(float(overlap), 3),
                        )
                    )
    # Greedy best match: one pair per column endpoint.
    scored.sort(key=lambda p: p.confidence, reverse=True)
    taken: set[tuple[str, str]] = set()
    cand: list[MappingPair] = []
    for p in scored:
        le = (p.left.dataset, p.left.column)
        re_ = (p.right.dataset, p.right.column)
        if le in taken or re_ in taken:
            continue
        taken.add(le)
        taken.add(re_)
        cand.append(p)
    return cand[:max_pairs]


def _schema_digest(frames: dict[str, Any]) -> str:
    parts: list[str] = []
    for name, df in frames.items():
        cols = ", ".join(f"{c}({df[c].dtype})" for c in df.columns)
        parts.append(f"- {name}: {cols}")
    return "\n".join(parts)


def _refine_messages(frames: dict[str, Any], cand: list[MappingPair]) -> list[dict[str, str]]:
    schema = _schema_digest(frames)
    cand_json = json.dumps(
        [
            {
                "id": i,
                "left": f"{p.left.dataset}::{p.left.column}",
                "right": f"{p.right.dataset}::{p.right.column}",
                "role": p.role,
                "value_overlap_pct": int(p.overlap * 100),
                "name_similarity_pct": int(p.name_sim * 100),
            }
            for i, p in enumerate(cand)
        ],
        ensure_ascii=False,
    )
    system = (
        "You review candidate column alignments between different tabular datasets. "
        "For each candidate decide: keep it or drop it, and whether its role is correct.\n"
        "role=join_key when both columns identify the same entity/axis (ids, names, dates) "
        "to merge rows on; role=value when they measure the same quantity in different sheets.\n"
        "Drop pairs whose columns mean different things (e.g. a department id vs an employee "
        "id), even if the names look similar. You may NOT add new pairs.\n"
        'Return ONLY JSON: {"decisions":[{"id":<int>,"keep":true|false,"role":"join_key|value"}]}'
    )
    user = f"Datasets:\n{schema}\n\nCandidate pairs:\n{cand_json}"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _heuristic_fallback(cand: list[MappingPair]) -> list[MappingPair]:
    """Safe subset when the LLM refine is unavailable or unparseable."""
    return [p for p in cand if p.note == "exact" or p.confidence >= 0.7][:20]


def suggest_mappings(
    frames: dict[str, Any],
    *,
    chat_fn: Callable[[list[dict[str, str]]], str] | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> MappingSuggestion:
    """Heuristic candidates, optionally refined by one LLM call.

    The LLM may only drop or relabel heuristic candidates, never add pairs.
    Authoritative candidates (note="exact") always survive.
    """
    if len(frames) < 2:
        return MappingSuggestion()
    cand = heuristic_candidates(frames)
    if not cand or chat_fn is None:
        return MappingSuggestion(pairs=cand[:20])

    if log_fn:
        log_fn(f"Refining {len(cand)} candidate column pairs…")
    from services.data_studio.llm import strip_json_fences

    try:
        raw = chat_fn(_refine_messages(frames, cand))
        data = json.loads(strip_json_fences(raw))
        rows = data.get("decisions") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise ValueError("no decisions list")
    except Exception as ex:  # noqa: BLE001 — fall back to heuristics on any LLM/JSON error
        if log_fn:
            log_fn(f"Mapping refine failed, using heuristics: {ex}")
        return MappingSuggestion(pairs=_heuristic_fallback(cand))

    decisions: dict[int, tuple[bool, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        role = str(row.get("role") or "")
        decisions[idx] = (bool(row.get("keep")), role)

    pairs: list[MappingPair] = []
    for i, p in enumerate(cand):
        keep, role = decisions.get(i, (p.confidence >= 0.7, p.role))
        if p.note == "exact":
            keep = True  # authoritative — the LLM may not drop these
        if not keep:
            continue
        if role in ("join_key", "value") and role != p.role:
            # Promote to join_key only with value-overlap evidence.
            if role == "value" or p.overlap >= 0.15:
                p.role = role
        pairs.append(p)
    return MappingSuggestion(pairs=pairs[:20] or _heuristic_fallback(cand))
