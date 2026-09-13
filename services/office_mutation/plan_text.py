# -*- coding: utf-8 -*-
"""LLM planning for Office text mutations."""
from __future__ import annotations

import json
import re

from services import llm_bridge as chat_client
from services.resource_governor import ResourceGovernor
from services.session import state

DEFAULT_BATCH_SIZE = 35

_PLAN_SYSTEM = """You are a precise Office document text mutation engine.
You receive a user instruction and a JSON list of text fragments extracted from Word, PowerPoint, or Excel.
Each fragment includes location/structure/style metadata for context — metadata is READ-ONLY; never alter formatting in output.
Return ONLY a JSON object: keys are the exact "id" fields from the input; values are the replacement strings.

Rules:
- Follow the user instruction exactly.
- Change only text content required by the instruction; keep all other characters in each fragment unchanged.
- If a fragment does not need changes, return its original "text" unchanged for that id.
- Do not add markdown, explanations, or extra keys.
- Plain text only — no **bold**, _italic_, or markup.
- In JSON string values use \\n for line breaks (no raw control characters inside strings).
- Preserve numbers, punctuation, and line breaks where possible.
"""


_PLAN_USER = """USER INSTRUCTION:
{instruction}

TEXT FRAGMENTS (JSON array of {{id, text}}):
{fragments_json}
"""

_TRANSLATE_PLAN_SUFFIX = """
TRANSLATION (full-fragment) RULES:
- Translate every word in each fragment into the target language named in the instruction.
- Do NOT add explanations, glosses, footnotes, or words that are not translations of the source.
- Do NOT leave source-language text unless the instruction names a selective source language scope.
- Each JSON value must contain ONLY the translated fragment text — same scope as the input fragment.
"""


def _sanitize_json_text(raw: str) -> str:
    if not raw:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", raw)


def _parse_json_object(raw: str, expected_ids: list[str] | None = None) -> dict[str, str]:
    raw = _sanitize_json_text((raw or "").strip())
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group(0)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = _parse_json_object_lenient(raw, expected_ids or [])
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return {str(k): str(v) for k, v in data.items()}


def _parse_json_object_lenient(raw: str, expected_ids: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    if expected_ids:
        for uid in expected_ids:
            pattern = re.compile(
                rf'"{re.escape(uid)}"\s*:\s*"(.*?)"(?=\s*,\s*"|}})',
                re.DOTALL,
            )
            m = pattern.search(raw)
            if m:
                val = m.group(1).replace("\\n", "\n").replace('\\"', '"')
                out[uid] = val
        if out:
            return out
    for m in re.finditer(r'"([^"]+)"\s*:\s*"(.*?)"(?=\s*,\s*"|}})', raw, re.DOTALL):
        key, val = m.group(1), m.group(2)
        if key.startswith("s") or key.startswith("p") or "_" in key:
            out[key] = val.replace("\\n", "\n").replace('\\"', '"')
    if out:
        return out
    raise ValueError("Could not parse mutation JSON")


def _mutation_plan_system(capability_id: str = "document_mutation", role_ids: list[str] | None = None) -> str:
    system = _PLAN_SYSTEM
    cap = (capability_id or "document_mutation").strip().lower()
    blocks: list[str] = []
    for rid in role_ids or ["mutation_editor"]:
        try:
            from pipeline.contracts.registry import get_contract_for_role
            from pipeline.roles.registry import get_role

            role = get_role(rid, cap)
            contract = get_contract_for_role(rid, cap)
            rules = "\n".join(f"- {r}" for r in contract.output_rules)
            blocks.append(f"Active role: {role.id}\n{role.system_prompt}\n\nContract:\n{rules}")
        except Exception:
            continue
    if blocks:
        system = f"{system}\n\n" + "\n\n---\n\n".join(blocks)
    return system


def _unit_chars(unit: dict) -> int:
    from services.office_mutation.unit_enrich import unit_to_plan_payload

    return len(json.dumps(unit_to_plan_payload(unit), ensure_ascii=False))


def _split_units_by_size(
    units: list[dict], *, max_chars: int, max_count: int
) -> list[list[dict]]:
    """Batch by BOTH a char budget and a count cap — count alone (the old fixed-35
    behavior) let a batch of large fragments (long paragraphs, big table cells) build
    one oversized prompt with no size guard at all. A single oversized unit still gets
    its own one-item batch rather than being dropped."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    current_chars = 0
    for u in units:
        u_chars = _unit_chars(u)
        if current and (
            len(current) >= max_count or current_chars + u_chars > max_chars
        ):
            batches.append(current)
            current, current_chars = [], 0
        current.append(u)
        current_chars += u_chars
    if current:
        batches.append(current)
    return batches or [units]


def _call_plan_llm(
    *,
    batch: list[dict],
    instruction: str,
    model_name: str,
    capability_id: str,
    role_ids: list[str] | None,
) -> dict[str, str]:
    from services.office_mutation.unit_enrich import unit_to_plan_payload
    from pipeline.direct.batch_budget import fit_budget_to_prompt, llm_extra_options, resolve_batch_budget

    payload = [unit_to_plan_payload(u) for u in batch]
    system = _mutation_plan_system(capability_id, role_ids)
    user_prompt = _PLAN_USER.format(
        instruction=instruction,
        fragments_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )
    prompt_chars = len(system) + len(user_prompt)
    # Expected output is roughly the same size as the input fragments (replacement
    # text for each id) — budget the output side for that, same reasoning as the
    # direct pipeline's fit_budget_to_prompt callers.
    budget = fit_budget_to_prompt(resolve_batch_budget(None, model_name), prompt_chars, prompt_chars)
    extra = llm_extra_options(budget)
    with ResourceGovernor.acquire("llm_chat"):
        response = chat_client.chat(
            model=model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user_prompt},
            ],
            options={"temperature": 0.1, **extra},
        )
    raw = response.get("message", {}).get("content", "")
    expected = [u["id"] for u in batch]
    return _parse_json_object(raw, expected_ids=expected)


def plan_text_map(
    units: list[dict],
    instruction: str,
    model_name: str,
    *,
    profile_hint: str = "",
    batch_size: int = DEFAULT_BATCH_SIZE,
    capability_id: str = "document_mutation",
    role_ids: list[str] | None = None,
) -> dict[str, str]:
    """Build id→replacement map from the user's instruction."""
    if not units:
        return {}

    instruction = (instruction or "").strip()
    if not instruction:
        state.add_log("Mutation: no instruction — keeping all fragments unchanged.")
        return {u["id"]: u.get("text") or u.get("value") or "" for u in units}

    if profile_hint.strip():
        instruction = f"{instruction.strip()}\n\nAdditional constraints:\n{profile_hint.strip()}"

    role_key = (role_ids or ["mutation_editor"])[0]
    if role_key in ("mutation_full_translator", "mutation_translator"):
        instruction = f"{instruction.strip()}\n{_TRANSLATE_PLAN_SUFFIX}"

    from pipeline.direct.batch_budget import batch_chunk_chars, resolve_batch_budget

    max_chars = batch_chunk_chars(resolve_batch_budget(None, model_name))
    batches = _split_units_by_size(units, max_chars=max_chars, max_count=batch_size)

    result: dict[str, str] = {}

    def _run_batch(batch: list[dict], label: str) -> None:
        try:
            mapped = _call_plan_llm(
                batch=batch,
                instruction=instruction,
                model_name=model_name,
                capability_id=capability_id,
                role_ids=role_ids,
            )
            for uid, text in mapped.items():
                result[uid] = text
        except Exception as ex:
            if len(batch) > 1:
                # A failure (JSON parse error, truncated response) on a multi-fragment
                # batch is often just that batch being too big for this model/machine —
                # halving and retrying recovers far more often than immediately
                # reverting every fragment in it to unchanged text.
                state.add_log(
                    f"Mutation plan batch {label} failed ({ex}) — retrying as smaller batches."
                )
                mid = len(batch) // 2
                _run_batch(batch[:mid], f"{label}a")
                _run_batch(batch[mid:], f"{label}b")
            else:
                state.add_log(f"Mutation plan batch {label} failed: {ex}")
                for u in batch:
                    result.setdefault(u["id"], u.get("text") or u.get("value") or "")

    for batch_idx, batch in enumerate(batches):
        state.add_log(f"Mutation plan batch {batch_idx + 1}/{len(batches)} ({len(batch)} fragments)…")
        _run_batch(batch, str(batch_idx + 1))

    for u in units:
        result.setdefault(u["id"], u.get("text") or u.get("value") or "")

    return result
