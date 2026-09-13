# -*- coding: utf-8 -*-
"""Research brief template and clarify-question schema."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from extensions.research.clarify_i18n import get_clarify_template


@dataclass
class ResearchBrief:
    topic: str = ""
    audience: str = "General reader"
    depth: str = "Standard report"
    source_policy: str = "Web + uploads"
    tone: str = "Formal"
    output_format: str = "Executive summary"
    constraints: str = ""
    source_count: int = 6
    original_prompt: str = ""
    search_queries: list[str] = field(default_factory=list)

    def uses_web(self) -> bool:
        return "web" in self.source_policy.lower()

    def uses_uploads(self) -> bool:
        return "upload" in self.source_policy.lower()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResearchBrief:
        return cls(
            topic=str(data.get("topic") or "").strip(),
            audience=str(data.get("audience") or "General reader").strip(),
            depth=str(data.get("depth") or "Standard report").strip(),
            source_policy=str(data.get("source_policy") or "Web + uploads").strip(),
            tone=str(data.get("tone") or "Formal").strip(),
            output_format=str(data.get("output_format") or "Executive summary").strip(),
            constraints=str(data.get("constraints") or "").strip(),
            source_count=int(data.get("source_count") or 6),
            original_prompt=str(data.get("original_prompt") or "").strip(),
            search_queries=list(data.get("search_queries") or []),
        )


def estimate_research_seconds(
    brief: ResearchBrief,
    *,
    upload_count: int = 0,
) -> int:
    """Rough ETA for the research run (shown before start)."""
    secs = 25
    n = max(3, brief.source_count or 6)
    if brief.uses_web():
        secs += 40 + 22 * n + 25 * min(4, len(brief.search_queries) or 3)
    if brief.uses_uploads():
        secs += max(15, 20 * upload_count)
    secs += 35  # synthesis
    depth_mult = {
        "brief overview": 1.0,
        "standard report": 1.45,
        "deep dive": 2.1,
    }
    fmt_mult = {
        "executive summary": 1.0,
        "bullet brief": 0.85,
        "full report": 1.75,
    }
    secs = int(
        secs
        * depth_mult.get(brief.depth.lower(), 1.4)
        * fmt_mult.get(brief.output_format.lower(), 1.0)
    )
    return max(50, secs)


def format_eta(seconds: int) -> str:
    if seconds < 90:
        return f"~{seconds}s"
    mins = seconds // 60
    rem = seconds % 60
    if rem < 15:
        return f"~{mins} min"
    return f"~{mins}–{mins + 1} min"


def _strip_json_fences(text: str) -> str:
    body = (text or "").strip()
    if body.startswith("```"):
        lines = body.split("\n")
        end = len(lines) - 1 if lines[-1].strip() == "```" else len(lines)
        return "\n".join(lines[1:end]).strip()
    return body


def parse_clarify_response(raw: str, user_prompt: str) -> list[dict[str, Any]]:
    """Parse LLM clarify JSON into UI rows (merge with template)."""
    data: dict[str, Any] = {}
    cleaned = _strip_json_fences(raw)
    if cleaned.lstrip().startswith("{"):
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {}

    by_id = {item.get("id"): item for item in data.get("questions") or [] if isinstance(item, dict)}
    rows: list[dict[str, Any]] = []
    for tpl in get_clarify_template():
        rid = tpl["id"]
        llm_row = by_id.get(rid) or {}
        suggested = str(
            llm_row.get("suggested_answer")
            or data.get(rid)
            or (user_prompt if rid == "topic" else "")
            or ("6 sources" if rid == "source_count" else "")
            or (tpl.get("options") or ("",))[0]
        ).strip()
        rows.append(
            {
                "id": rid,
                "label": tpl["label"],
                "question": str(tpl["question"]).strip(),
                "answer": suggested,
                "options": list(tpl.get("options") or []),
                "required": bool(tpl.get("required", rid != "constraints")),
            }
        )
    return rows


from extensions.research.sources import parse_source_count


def rows_to_brief(rows: list[dict[str, Any]], *, original_prompt: str) -> ResearchBrief:
    answers = {str(r.get("id") or ""): str(r.get("answer") or "").strip() for r in rows}
    topic = answers.get("topic") or original_prompt
    queries = _default_search_queries(topic, answers.get("constraints", ""))
    return ResearchBrief(
        topic=topic,
        audience=answers.get("audience") or "General reader",
        depth=answers.get("depth") or "Standard report",
        source_policy=answers.get("source_policy") or "Web + uploads",
        tone=answers.get("tone") or "Formal",
        output_format=answers.get("output_format") or "Executive summary",
        constraints=answers.get("constraints") or "",
        source_count=parse_source_count(answers.get("source_count") or "6"),
        original_prompt=original_prompt,
        search_queries=queries,
    )


def _default_search_queries(topic: str, constraints: str) -> list[str]:
    base = (topic or "").strip()
    if not base:
        return []
    queries = [base]
    if constraints:
        queries.append(f"{base} {constraints[:80]}".strip())
    words = base.split()
    if len(words) > 4:
        queries.append(" ".join(words[:5]))
    return list(dict.fromkeys(q for q in queries if q))[:4]
