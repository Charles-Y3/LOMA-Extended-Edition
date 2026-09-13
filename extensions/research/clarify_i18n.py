# -*- coding: utf-8 -*-
"""Localized Research clarify-step template."""
from __future__ import annotations

from typing import Any

from pipeline.i18n import t as tr


def get_clarify_template() -> tuple[dict[str, Any], ...]:
    return (
        {
            "id": "topic",
            "label": tr("research.clarify.topic.label"),
            "question": tr("research.clarify.topic.question"),
            "required": True,
        },
        {
            "id": "audience",
            "label": tr("research.clarify.audience.label"),
            "question": tr("research.clarify.audience.question"),
            "options": (
                tr("research.clarify.audience.opt_general"),
                tr("research.clarify.audience.opt_executive"),
                tr("research.clarify.audience.opt_academic"),
                tr("research.clarify.audience.opt_technical"),
            ),
        },
        {
            "id": "depth",
            "label": tr("research.clarify.depth.label"),
            "question": tr("research.clarify.depth.question"),
            "options": (
                tr("research.clarify.depth.opt_brief"),
                tr("research.clarify.depth.opt_standard"),
                tr("research.clarify.depth.opt_deep"),
            ),
        },
        {
            "id": "source_count",
            "label": tr("research.clarify.source_count.label"),
            "question": tr("research.clarify.source_count.question"),
            "options": (
                tr("research.clarify.source_count.opt_3"),
                tr("research.clarify.source_count.opt_5"),
                tr("research.clarify.source_count.opt_6"),
                tr("research.clarify.source_count.opt_8"),
                tr("research.clarify.source_count.opt_10"),
                tr("research.clarify.source_count.opt_12"),
            ),
        },
        {
            "id": "source_policy",
            "label": tr("research.clarify.source_policy.label"),
            "question": tr("research.clarify.source_policy.question"),
            "options": (
                tr("research.clarify.source_policy.opt_web"),
                tr("research.clarify.source_policy.opt_uploads"),
                tr("research.clarify.source_policy.opt_both"),
            ),
        },
        {
            "id": "tone",
            "label": tr("research.clarify.tone.label"),
            "question": tr("research.clarify.tone.question"),
            "options": (
                tr("research.clarify.tone.opt_formal"),
                tr("research.clarify.tone.opt_academic"),
                tr("research.clarify.tone.opt_executive"),
                tr("research.clarify.tone.opt_casual"),
            ),
        },
        {
            "id": "output_format",
            "label": tr("research.clarify.output_format.label"),
            "question": tr("research.clarify.output_format.question"),
            "options": (
                tr("research.clarify.output_format.opt_summary"),
                tr("research.clarify.output_format.opt_report"),
                tr("research.clarify.output_format.opt_bullets"),
            ),
        },
        {
            "id": "constraints",
            "label": tr("research.clarify.constraints.label"),
            "question": tr("research.clarify.constraints.question"),
            "required": False,
        },
    )


def localize_clarify_row(row: dict[str, Any]) -> dict[str, Any]:
    """Refresh label/question/options from current locale."""
    rid = row.get("id") or ""
    tpl = {item["id"]: item for item in get_clarify_template()}
    if rid not in tpl:
        return row
    spec = tpl[rid]
    out = dict(row)
    out["label"] = spec["label"]
    out["question"] = spec["question"]
    if spec.get("options"):
        out["options"] = list(spec["options"])
    return out
