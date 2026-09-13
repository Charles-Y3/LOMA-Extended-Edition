# -*- coding: utf-8 -*-
"""LLM chart planning from dataset profile (column picks + chart types)."""
from __future__ import annotations

import json
import re
from typing import Any

from services.graph_generation.models import DatasetProfile


def _parse_specs(raw: str, columns: list[str]) -> list[dict[str, Any]]:
    text = (raw or "").strip()
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, list):
        return []
    col_set = {str(c) for c in columns}
    specs: list[dict[str, Any]] = []
    for item in data[:6]:
        if not isinstance(item, dict):
            continue
        chart_type = str(item.get("chart_type") or item.get("type") or "bar").lower()
        x_col = str(item.get("x_col") or item.get("x") or item.get("category_col") or "")
        y_col = str(item.get("y_col") or item.get("y") or item.get("value_col") or "")
        if x_col and x_col not in col_set:
            x_col = ""
        if y_col and y_col not in col_set:
            y_col = ""
        if not x_col and not y_col:
            continue
        specs.append(
            {
                "chart_type": chart_type,
                "x_col": x_col,
                "y_col": y_col,
                "group_col": str(item.get("group_col") or ""),
                "title": str(item.get("title") or "Chart"),
                "caption": str(item.get("caption") or ""),
                "x_label": str(item.get("x_label") or x_col),
                "y_label": str(item.get("y_label") or y_col),
            }
        )
    return specs


def plan_charts_with_llm(
    profile: DatasetProfile,
    *,
    query: str = "",
    model: str = "",
    profile_pack: dict | None = None,
) -> list[dict[str, Any]]:
    """Ask the LLM which charts to build from column metadata (not raw rows)."""
    if not model:
        from services.model_router import resolve_general_model

        model = resolve_general_model(profile_pack or {})
    columns = [str(c.get("name") or "") for c in (profile.columns or []) if c.get("name")]
    if not columns:
        return []

    from services import llm_bridge as chat_client
    from services.resource_governor import ResourceGovernor

    prompt = (
        "You are a data visualization planner.\n"
        "Given dataset column metadata and the user question, propose 1–4 useful charts.\n"
        "Return ONLY a JSON array. Each item must use exact column names from the list.\n"
        "Allowed chart_type: bar, line, scatter, histogram, pie.\n"
        "Fields: chart_type, x_col, y_col (optional for histogram), group_col (optional), "
        "title, caption, x_label, y_label.\n\n"
        f"User question: {query or 'Analyze and visualize this dataset'}\n\n"
        f"Column names: {json.dumps(columns)}\n\n"
        f"Column metadata:\n{profile.summary_md[:8000]}"
    )
    try:
        with ResourceGovernor.acquire("llm_chat"):
            resp = chat_client.chat(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                options={"temperature": 0.2},
            )
        raw = (resp.get("message") or {}).get("content") or ""
        return _parse_specs(raw, columns)
    except Exception:
        return []
