# -*- coding: utf-8 -*-
"""Unified Office mutation entrypoint (docx / pptx in → any Office format out)."""
from __future__ import annotations

import os
import shutil
from typing import Callable, Optional

from services.office_mutation.export import export_docx_from_units, export_pptx_units_to_docx
from services.office_mutation.extract import extract_units
from services.office_mutation.paths import resolve_upload_path, source_format, target_extension
from services.office_mutation.plan import count_changed, plan_mutation_map, verify_selective_plan
from services.session import state


def mutate_office_file(
    output_type: str,
    original_filename: str,
    model_name: str,
    *,
    instruction: str = "",
    profile_hint: str = "",
    text_map: dict[str, str] | None = None,
    replan: bool = True,
    on_unit_applied: Callable | None = None,
    capability_id: str = "document_mutation",
    mutation_role_ids: list[str] | None = None,
    src_path: str | None = None,
    units: list[dict] | None = None,
) -> str:
    """
    Mutate an uploaded Office file per user instruction.
    Source format is detected from the upload; output format from output_type.
    Supports pptx → docx (and same-format in-place) mutation.
    """
    from services.office_mutation.apply import apply_docx, apply_pptx, force_docx_portrait
    from services.office_mutation.export import export_pptx_units_to_docx

    src_path = src_path or resolve_upload_path(original_filename)
    if not src_path:
        state.add_log(f"Mutation: upload not found for {original_filename}")
        return ""

    src_fmt = source_format(src_path)
    out_ext = target_extension(src_path, output_type)
    from services.session.artifact import resolve_generated_stem

    query = instruction or (state.last_user_instruction or "")
    base_name = resolve_generated_stem(
        query=query,
        source_names=[os.path.basename(src_path)],
        output_type=output_type,
    )
    generated_dir = os.path.join("data", "generated")
    os.makedirs(generated_dir, exist_ok=True)
    dest_path = os.path.join(generated_dir, f"{base_name}_LOMA{out_ext}")

    state.add_log(f"Mutation: {os.path.basename(src_path)} ({src_fmt}) → {os.path.basename(dest_path)}")

    if units is None:
        units = extract_units(src_path)
    if not units:
        state.add_log(f"Mutation: no text units extracted from {os.path.basename(src_path)}")
        return ""

    state.add_log(f"Extracted {len(units)} text units from {src_fmt or 'file'}.")

    instruction = (instruction or state.last_user_instruction or "").strip()
    if not instruction:
        state.add_log("Mutation: no instruction — copying source unchanged.")
        shutil.copy2(src_path, dest_path)
        return dest_path

    from services.office_mutation.unit_enrich import changed_text_map
    from services.office_mutation.unit_filter import filter_mutation_units

    if text_map is not None and not replan:
        state.add_log(f"Reusing cached mutation plan ({len(text_map)} entries).")
    else:
        to_plan = filter_mutation_units(units, instruction)
        if len(to_plan) < len(units):
            state.add_log(
                f"Mutation filter: {len(to_plan)}/{len(units)} fragment(s) selected for planning"
            )
        if not to_plan:
            state.add_log("Mutation: no fragments match instruction — copying source unchanged.")
            shutil.copy2(src_path, dest_path)
            return dest_path

        state.add_log(f"Planning mutations ({len(to_plan)} fragments)…")
        text_map = plan_mutation_map(
            to_plan,
            instruction,
            model_name,
            profile_hint=profile_hint,
            capability_id=capability_id,
            role_ids=mutation_role_ids,
            all_units=units,
        )

        warnings = verify_selective_plan(to_plan, text_map, instruction)
        for w in warnings:
            state.add_log(f"Mutation warning: {w} — retrying with editor role.")
        if warnings:
            text_map = plan_mutation_map(
                to_plan,
                instruction,
                model_name,
                profile_hint=profile_hint,
                capability_id=capability_id,
                role_ids=["mutation_editor"],
                all_units=units,
            )

    changed = count_changed(units, text_map)
    patch = changed_text_map(units, text_map)
    if changed and len(patch) < changed:
        state.add_log(f"Mutation patch: {len(patch)} unit(s) with actual text diffs")
    state.add_log(f"Applying {changed}/{len(units)} text changes…")
    state.mutation_map = text_map

    # Cross-format / PDF: build new docx from translated units
    if src_path.lower().endswith(".pdf") and out_ext == ".docx":
        path = export_docx_from_units(units, text_map, dest_path)
        return path or ""

    if src_path.lower().endswith(".pptx") and out_ext == ".docx":
        path = export_pptx_units_to_docx(units, text_map, dest_path)
        return path or ""

    if replan or not os.path.exists(dest_path):
        shutil.copy2(src_path, dest_path)

    if out_ext == ".pptx" and dest_path.lower().endswith(".pptx"):
        return apply_pptx(dest_path, units, text_map, on_unit_applied) or dest_path
    if out_ext == ".docx" and dest_path.lower().endswith(".docx"):
        path = apply_docx(dest_path, units, text_map) or dest_path
        force_docx_portrait(path)
        return path
    if out_ext == ".xlsx" and dest_path.lower().endswith((".xlsx", ".xlsm")):
        from services.office_mutation.apply import apply_xlsx

        return apply_xlsx(dest_path, units, text_map, on_unit_applied) or dest_path

    state.add_log(f"Mutation: unsupported source/output pair ({src_path} → {out_ext})")
    return ""
