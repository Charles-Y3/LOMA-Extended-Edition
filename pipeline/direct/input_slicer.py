# -*- coding: utf-8 -*-
"""Filter parsed input to only units/content requiring mutation per step."""
from __future__ import annotations

import os
import re
from typing import Any

from pipeline.direct.query_planner import PlannedStep, StepScope


def digest_text_for_step(step: PlannedStep, bundle: Any) -> str:
    """Full text for one source when step intent names that file (per-source summaries)."""
    digests = getattr(bundle, "source_digests", None) or []
    intent = (step.intent or "").strip()
    intent_lower = intent.lower()
    if not intent_lower or not digests:
        return ""

    m = re.match(r"^summarize\s+(.+)$", intent_lower, re.I)
    if not m:
        m = re.match(r"^extract(?:\s+\w+)*\s+from\s+(.+)$", intent_lower, re.I)
    if m:
        target = m.group(1).strip().lower()
        for d in digests:
            name = (d.name or "").strip().lower()
            if name and name == target:
                return (d.full_text or "").strip()

    best = ""
    best_len = 0
    best_score = 0
    for d in digests:
        name = (d.name or "").strip()
        if not name:
            continue
        nl = name.lower()
        base = os.path.splitext(nl)[0]
        score = 0
        if nl in intent_lower:
            score = 100 + len(nl)
        elif base and len(base) > 3 and base in intent_lower:
            score = 80 + len(base)
        elif any(part in intent_lower for part in base.split() if len(part) > 4):
            score = 40 + len(base)
        text = (d.full_text or "").strip()
        if score > best_score or (score == best_score and len(text) > best_len):
            if score > 0 and text:
                best = text
                best_len = len(text)
                best_score = score
    return best

def _slide_idx(unit: dict) -> int | None:
    ref = unit.get("ref") or ()
    if len(ref) > 1 and ref[0] in ("paragraph", "table_cell"):
        return int(ref[1])
    return None


def _sheet_idx(unit: dict) -> int | None:
    ref = unit.get("ref") or ()
    if len(ref) > 1 and ref[0] == "cell":
        return int(ref[1])
    return None


def _unit_column(unit: dict) -> str | None:
    ref = unit.get("ref") or ()
    uid = str(unit.get("id") or "")
    if len(ref) > 3 and ref[0] == "cell":
        col = int(ref[3])
        return _col_letter(col)
    m = re.search(r"_([A-Z]+)\d+", uid)
    if m:
        return m.group(1)
    return None


def _col_letter(col_idx: int) -> str:
    n = col_idx + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def slice_mutation_units(
    units: list[dict],
    scope: StepScope,
    *,
    selection_text: str = "",
) -> list[dict]:
    """Return subset of mutation units for this step."""
    if not units:
        return []
    filtered = list(units)

    if scope.slides:
        slide_set = {s - 1 for s in scope.slides if s > 0}
        filtered = [u for u in filtered if _slide_idx(u) in slide_set]

    if scope.sheets:
        sheet_set = {s - 1 for s in scope.sheets if s > 0}
        filtered = [u for u in filtered if _sheet_idx(u) in sheet_set]

    if scope.columns:
        col_set = {c.upper() for c in scope.columns}
        filtered = [u for u in filtered if (_unit_column(u) or "").upper() in col_set]

    if scope.selection and selection_text:
        needle = selection_text.strip().lower()
        if needle:
            matched = [
                u
                for u in filtered
                if needle in str(u.get("text") or u.get("value") or "").lower()
            ]
            if matched:
                filtered = matched

    return filtered or units


def build_step_input_payload(
    *,
    step: PlannedStep,
    mode: str,
    output_type: str,
    bundle: Any,
    working_text: str = "",
    mutation_units: list[dict] | None = None,
    selection_text: str = "",
) -> str:
    """Build user-message body for a pipeline step."""
    parts: list[str] = []
    if step.intent:
        parts.append(step.intent)

    if mode == "mutation" and mutation_units:
        sliced = slice_mutation_units(
            mutation_units, step.scope, selection_text=selection_text
        )
        import json

        from services.office_mutation.unit_enrich import unit_to_plan_payload

        fragments = [unit_to_plan_payload(u) for u in sliced]
        parts.append(f"TEXT FRAGMENTS (JSON):\n{json.dumps(fragments, ensure_ascii=False)}")
    else:
        source_only = digest_text_for_step(step, bundle)
        if source_only:
            parts.append(f"Source text (this file only):\n{source_only}")
        elif getattr(bundle, "unified_text", None):
            ctx = (bundle.unified_text or "").strip()
            if ctx:
                parts.append(f"Workspace Context:\n{ctx}")

    return "\n\n".join(parts)
