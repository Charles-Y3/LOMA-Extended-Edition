# -*- coding: utf-8 -*-
"""Plan enrichment: phases, delivery pipeline section, role normalization."""
from __future__ import annotations

from pipeline.deliverables.specs import (
    get_deliverable_spec,
    infer_slide_count,
    service_purpose,
)
from pipeline.schemas.task_schema import AgenticPlan, PlanStep, RoutingDecision

_FORBIDDEN_WORKER_ROLES = frozenset({"renderer", "assembler", "compiler", "exporter"})
_ROLE_MAP = {
    "renderer": "General",
    "assembler": "General",
    "compiler": "General",
    "exporter": "General",
    "general": "General",
    "coder": "Specialist",
    "specialist": "Specialist",
    "vision": "Vision",
    "verifier": "Verifier",
}


def normalize_worker_role(role: str) -> str:
    key = (role or "General").strip()
    lower = key.lower()
    if lower in _ROLE_MAP:
        return _ROLE_MAP[lower]
    if lower in _FORBIDDEN_WORKER_ROLES:
        return "General"
    casing = {"general": "General", "coder": "Specialist", "specialist": "Specialist", "vision": "Vision", "verifier": "Verifier"}
    if lower in casing:
        return casing[lower]
    return key if key else "General"


def assign_phase(step_index: int, total: int, phases: tuple[str, ...]) -> str:
    if not phases:
        return "draft"
    if total <= len(phases):
        return phases[min(step_index, len(phases) - 1)]
    # Map N steps onto phase template (last step always assemble/prompt)
    if step_index >= total - 1:
        return phases[-1]
    slot = int(step_index * (len(phases) - 1) / max(total - 1, 1))
    return phases[min(slot, len(phases) - 2)]


def enrich_plan_steps(
    steps: list[PlanStep],
    spec,
) -> list[PlanStep]:
    enriched: list[PlanStep] = []
    total = len(steps)
    for i, step in enumerate(steps):
        role = normalize_worker_role(step.role)
        phase = (step.phase or "").strip() or assign_phase(i, total, spec.phases)
        contract = spec.contract_for_phase(phase)
        enriched.append(
            PlanStep(
                role=role,
                description=step.description,
                action=step.action,
                step_id=step.step_id,
                artifact_type=step.artifact_type,
                acceptance_criteria=list(step.acceptance_criteria),
                options=list(step.options),
                instruction_md=step.instruction_md,
                depends_on=list(step.depends_on),
                phase=phase,
                deliverable_contract=contract.id,
            )
        )
    return enriched


def build_delivery_pipeline_section(
    service_ids: list[str],
    *,
    output_type: str,
    mode: str | None,
) -> str:
    if not service_ids:
        return (
            "## Delivery Pipeline (automatic)\n\n"
            "No compile services — worker output is delivered as text.\n"
        )
    rows = []
    for sid in service_ids:
        rows.append(f"| `{sid}` | {service_purpose(sid)} |")
    mode_note = ""
    if (mode or "").lower() == "mutation":
        mode_note = (
            "\n\n**Mutation mode:** `artifact_store` applies edits to the uploaded template "
            "while preserving layout markers.\n"
        )
    return (
        "## Delivery Pipeline (automatic)\n\n"
        "These services run **after** worker steps complete. "
        "They are not separate LLM tasks.\n\n"
        "| Service | Purpose |\n|---------|--------|\n"
        + "\n".join(rows)
        + mode_note
    )


def append_delivery_pipeline_to_plan_md(plan_md: str, pipeline_section: str) -> str:
    body = (plan_md or "").strip()
    marker = "## Delivery Pipeline"
    if marker in body:
        import re

        body = re.sub(
            r"## Delivery Pipeline[^\n]*\n.*?(?=\n## |\Z)",
            pipeline_section.strip() + "\n",
            body,
            count=1,
            flags=re.DOTALL,
        )
        return body.strip()
    risks_marker = "## Risks & Boundaries"
    if risks_marker in body:
        head, tail = body.split(risks_marker, 1)
        return f"{head.rstrip()}\n\n{pipeline_section.strip()}\n\n{risks_marker}{tail}"
    return f"{body}\n\n{pipeline_section.strip()}"


def orchestrator_planning_rules(decision: RoutingDecision, spec) -> str:
    slide_hint = ""
    if decision.output_type == "presentation":
        slide_hint = (
            "\n\nPRESENTATION PLAYBOOK (agentic):\n"
            "- Phases: research → outline (JSON deck spec) → draft (slide copy JSON) → assemble (markdown only).\n"
            "- Slide 1 MUST be layout title: deck title + optional subtitle, NO bullet list.\n"
            "- Slides 2..N: short ## titles, 3–5 bullets max, ~12 words per bullet.\n"
            "- Include design in deck spec: mood + palette suited to the topic (see contract palettes).\n"
            "- Vary slide intent: hook, insight, example, practice, closing CTA.\n"
            "- Final assemble step outputs --- Slide N --- markdown only (compiled to styled .pptx automatically).\n"
            "- Do not plan worker steps that export or save PowerPoint files.\n"
        )
    return (
        f"\nDELIVERABLE TYPE: {decision.output_type}\n"
        f"MODE: {decision.mode or 'generation'}\n"
        f"REQUIRED SERVICES (from router): {', '.join(decision.required_services or [])}\n"
        f"WORKER ROLES ALLOWED: General, Specialist, Vision only.\n"
        f"FORBIDDEN: worker steps that claim to create .docx/.pptx/.pdf files or use role Renderer.\n"
        f"FINAL WORKER STEP: produce compile-ready source ({spec.final_contract_id}).\n"
        f"Phases template: {' → '.join(spec.phases)}.\n"
        f'Each step must include "phase" matching the template.{slide_hint}\n'
    )


def enrich_agentic_plan(
    plan: AgenticPlan,
    decision: RoutingDecision,
    *,
    query: str = "",
) -> AgenticPlan:
    spec = get_deliverable_spec(decision.output_type, decision.mode)
    steps = enrich_plan_steps(list(plan.steps), spec)
    service_ids = spec.delivery_service_ids(list(decision.required_services or []))
    pipeline_section = build_delivery_pipeline_section(
        service_ids,
        output_type=decision.output_type,
        mode=decision.mode,
    )
    plan_md = append_delivery_pipeline_to_plan_md(plan.plan_md, pipeline_section)
    return AgenticPlan(
        objective=plan.objective,
        plan_md=plan_md,
        steps=steps,
        success_criteria=list(plan.success_criteria),
        delivery_pipeline=service_ids,
        deliverable_type=decision.output_type,
        services_required=list(decision.required_services or []),
    )
