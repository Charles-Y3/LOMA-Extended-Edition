# -*- coding: utf-8 -*-
"""Deliverable specs, assembly, and validation for the agentic loop."""
from pipeline.deliverables.plan_helpers import enrich_agentic_plan
from pipeline.deliverables.assembly import assemble_deliverable, merge_revised_assemble
from pipeline.deliverables.specs import get_deliverable_spec, infer_slide_count
from pipeline.deliverables.validators import validate_deliverable_contract

__all__ = [
    "assemble_deliverable",
    "enrich_agentic_plan",
    "get_deliverable_spec",
    "infer_slide_count",
    "merge_revised_assemble",
    "validate_deliverable_contract",
]
