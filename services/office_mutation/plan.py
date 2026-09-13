# -*- coding: utf-8 -*-
"""Plan Office text mutations (multi-role sequential passes)."""
from __future__ import annotations

import re

from services.office_mutation.plan_text import plan_text_map
from services.session import state


def plan_mutation_map(
    units: list[dict],
    instruction: str,
    model_name: str,
    *,
    profile_hint: str = "",
    capability_id: str = "document_mutation",
    role_ids: list[str] | None = None,
    all_units: list[dict] | None = None,
) -> dict[str, str]:
    """
    Apply each mutation role in sequence so summarizer → selective translator composes correctly.
    `units` = filtered fragments to plan; `all_units` = full extract for unchanged baseline.
    """
    roles = role_ids or ["mutation_editor"]
    baseline = all_units if all_units is not None else units
    working = {u["id"]: u.get("text") or u.get("value") or "" for u in baseline}
    span_cache: dict[tuple[str, str, str], str] = {}

    for idx, role_id in enumerate(roles):
        from services.office_mutation.unit_filter import filter_mutation_units

        batch = filter_mutation_units(
            [
                {**u, "text": working.get(u["id"], u.get("text") or u.get("value") or "")}
                for u in units
            ],
            instruction,
        )
        if len(batch) < len(baseline):
            state.add_log(
                f"Mutation filter: {len(batch)}/{len(baseline)} fragment(s) need changes"
            )
        if not batch:
            state.add_log("Mutation filter: no matching fragments — skipping pass.")
            continue
        state.add_log(f"Mutation plan pass {idx + 1}/{len(roles)} ({role_id})…")
        from services.selective_translate import apply_selective_translation, should_apply_selective

        if role_id in ("mutation_selective_translator", "mutation_full_translator", "mutation_translator"):
            from services.office_mutation.translate_units import translate_units_map

            mapped = translate_units_map(
                batch,
                instruction,
                model_name,
            )
        else:
            mapped = plan_text_map(
                batch,
                instruction,
                model_name,
                profile_hint=profile_hint,
                capability_id=capability_id,
                role_ids=[role_id],
            )
            if role_id != "mutation_full_translator" and should_apply_selective(
                instruction, [role_id]
            ):
                for u in batch:
                    uid = u["id"]
                    orig = u.get("text") or u.get("value") or ""
                    llm_text = mapped.get(uid, orig)
                    mapped[uid] = apply_selective_translation(
                        orig,
                        llm_text,
                        instruction,
                        model=model_name,
                        span_cache=span_cache,
                    )
        for uid, text in mapped.items():
            working[uid] = text

    return working


def count_changed(units: list[dict], text_map: dict[str, str]) -> int:
    return sum(
        1
        for u in units
        if text_map.get(u["id"], u.get("text") or u.get("value") or "")
        != (u.get("text") or u.get("value") or "")
    )


def instruction_expects_selective_change(instruction: str) -> bool:
    q = (instruction or "").lower()
    if "only" not in q and "just" not in q:
        return False
    return any(w in q for w in ("translate", "translation", "summar", "summary"))


def verify_selective_plan(
    units: list[dict],
    text_map: dict[str, str],
    instruction: str,
) -> list[str]:
    """Return warning messages when selective instructions likely were not applied."""
    warnings: list[str] = []
    if not instruction_expects_selective_change(instruction):
        return warnings
    if count_changed(units, text_map) == 0:
        warnings.append("mutation plan produced zero text changes")
    return warnings
