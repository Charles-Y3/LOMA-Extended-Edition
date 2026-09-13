# -*- coding: utf-8 -*-
"""Pre-filter mutation units so only affected fragments reach the LLM."""
from __future__ import annotations

import re

from pipeline.direct.query_planner import StepScope
from services.selective_translate import (
    _parse_selective_scope,
    has_selective_spans,
    should_apply_selective,
)


def _unit_text(unit: dict) -> str:
    return str(unit.get("text") or unit.get("value") or "")


def _slide_index(unit: dict) -> int | None:
    if unit.get("slide") is not None:
        try:
            return int(unit["slide"]) - 1
        except (TypeError, ValueError):
            pass
    ref = unit.get("ref") or ()
    if len(ref) > 1 and ref[0] in ("paragraph", "table_cell"):
        return int(ref[1])
    return None


def _sheet_index(unit: dict) -> int | None:
    sheet = unit.get("sheet")
    if isinstance(sheet, dict) and sheet.get("index") is not None:
        return int(sheet["index"])
    ref = unit.get("ref") or ()
    if len(ref) > 1 and ref[0] == "cell":
        return int(ref[1])
    return None


def unit_matches_instruction(unit: dict, instruction: str) -> bool:
    """True when this fragment may need mutation for the given instruction."""
    text = _unit_text(unit)
    if not text.strip():
        return False

    if should_apply_selective(instruction, None):
        source, _ = _parse_selective_scope(instruction)
        if source:
            return has_selective_spans(text, instruction)

    lower = (instruction or "").lower()
    if scope_slide_nums := re.findall(r"\bslide\s+(\d+)\b", lower):
        idx = _slide_index(unit)
        if idx is not None:
            wanted = {int(n) - 1 for n in scope_slide_nums}
            return idx in wanted

    if sheet_nums := re.findall(r"\bsheet\s+(\d+)\b", lower):
        idx = _sheet_index(unit)
        if idx is not None:
            wanted = {int(n) - 1 for n in sheet_nums}
            return idx in wanted

    return True


def filter_mutation_units(
    units: list[dict],
    instruction: str,
    *,
    scope: StepScope | None = None,
) -> list[dict]:
    """Return units that plausibly need mutation. Selective filters never fall back to all."""
    if not units:
        return []
    scoped = list(units)
    if scope and scope.slides:
        slide_set = {s - 1 for s in scope.slides if s > 0}
        scoped = [u for u in scoped if _slide_index(u) in slide_set]
    if scope and scope.sheets:
        sheet_set = {s - 1 for s in scope.sheets if s > 0}
        scoped = [u for u in scoped if _sheet_index(u) in sheet_set]

    selective = should_apply_selective(instruction, None) and _parse_selective_scope(instruction)[0]
    filtered = [u for u in scoped if unit_matches_instruction(u, instruction)]
    if selective:
        return filtered
    return filtered if filtered else scoped


def group_units_by_slide(units: list[dict]) -> dict[int, list[dict]]:
    groups: dict[int, list[dict]] = {}
    for u in units:
        idx = _slide_index(u)
        key = idx if idx is not None else -1
        groups.setdefault(key, []).append(u)
    return dict(sorted(groups.items(), key=lambda kv: kv[0]))


def affected_slide_numbers(units: list[dict], instruction: str) -> list[int]:
    filtered = filter_mutation_units(units, instruction)
    nums: list[int] = []
    for u in filtered:
        if u.get("slide") is not None:
            nums.append(int(u["slide"]))
            continue
        idx = _slide_index(u)
        if idx is not None:
            nums.append(idx + 1)
    return sorted(set(nums))


def count_spans_in_units(units: list[dict], instruction: str) -> int:
    from services.selective_translate import collect_spans

    return sum(len(collect_spans(_unit_text(u), instruction)) for u in units)
