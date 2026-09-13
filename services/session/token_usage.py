# -*- coding: utf-8 -*-
"""Persist and query LLM token usage events."""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from typing import Any

_ROOT = os.path.join("data", "token_usage")
_EVENTS_PATH = os.path.join(_ROOT, "events.jsonl")
_COST_PATH = os.path.join(_ROOT, "cost_settings.json")
_lock = threading.Lock()

DEFAULT_COST_SETTINGS: dict[str, float] = {
    "prompt_per_million": 2.0,
    "completion_per_million": 10.0,
}


def _ensure_dir() -> None:
    os.makedirs(_ROOT, exist_ok=True)


def _parse_ts(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _local_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        return ts.date()
    return ts.astimezone().date()


def record_usage(
    *,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    operation: str = "chat",
    source: str = "",
) -> None:
    prompt_tokens = max(0, int(prompt_tokens or 0))
    completion_tokens = max(0, int(completion_tokens or 0))
    total = prompt_tokens + completion_tokens
    if total <= 0:
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "model": (model or "unknown").strip() or "unknown",
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total,
        "operation": (operation or "chat").strip() or "chat",
        "source": (source or "").strip(),
    }
    _ensure_dir()
    line = json.dumps(event, ensure_ascii=False)
    with _lock:
        with open(_EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def _coerce_response_dict(response: Any) -> dict[str, Any]:
    if response is None:
        return {}
    if isinstance(response, dict):
        return response
    if hasattr(response, "model_dump"):
        try:
            dumped = response.model_dump()
            if isinstance(dumped, dict):
                return dumped
        except Exception:
            pass
    out: dict[str, Any] = {}
    for key in (
        "model",
        "prompt_eval_count",
        "eval_count",
        "prompt_tokens",
        "completion_tokens",
        "eval_tokens",
        "usage",
    ):
        if hasattr(response, key):
            val = getattr(response, key, None)
            if val is not None:
                out[key] = val
    return out


def record_from_response(response: Any, *, model: str, operation: str = "chat") -> None:
    data = _coerce_response_dict(response)
    if not data:
        return
    prompt = int(
        data.get("prompt_eval_count")
        or data.get("prompt_tokens")
        or 0
    )
    completion = int(
        data.get("eval_count")
        or data.get("completion_tokens")
        or data.get("eval_tokens")
        or 0
    )
    usage = data.get("usage") or {}
    if isinstance(usage, dict):
        prompt = prompt or int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)
        completion = completion or int(
            usage.get("completion_tokens") or usage.get("output_tokens") or 0
        )
    record_usage(
        model=model or str(data.get("model") or "unknown"),
        prompt_tokens=prompt,
        completion_tokens=completion,
        operation=operation,
    )


def load_events(
    *,
    start: date | None = None,
    end: date | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    if not os.path.isfile(_EVENTS_PATH):
        return []
    model_key = (model or "").strip().lower()
    out: list[dict[str, Any]] = []
    with _lock:
        with open(_EVENTS_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                ts = _parse_ts(str(row.get("ts") or ""))
                if start and ts and _local_date(ts) < start:
                    continue
                if end and ts and _local_date(ts) > end:
                    continue
                if model_key and str(row.get("model") or "").lower() != model_key:
                    continue
                out.append(row)
    return out


def load_cost_settings() -> dict[str, float]:
    _ensure_dir()
    if not os.path.isfile(_COST_PATH):
        return dict(DEFAULT_COST_SETTINGS)
    try:
        with open(_COST_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        out = dict(DEFAULT_COST_SETTINGS)
        if isinstance(raw, dict):
            for key in DEFAULT_COST_SETTINGS:
                if key in raw:
                    out[key] = max(0.0, float(raw[key] or 0))
        return out
    except Exception:
        return dict(DEFAULT_COST_SETTINGS)


def save_cost_settings(data: dict[str, float]) -> None:
    _ensure_dir()
    merged = dict(DEFAULT_COST_SETTINGS)
    merged.update(data or {})
    with _lock:
        with open(_COST_PATH, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    *,
    cost_cfg: dict[str, float] | None = None,
) -> float:
    cfg = cost_cfg or load_cost_settings()
    prompt_rate = float(cfg.get("prompt_per_million") or 0) / 1_000_000
    completion_rate = float(cfg.get("completion_per_million") or 0) / 1_000_000
    return prompt_tokens * prompt_rate + completion_tokens * completion_rate


def summarize_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, dict[str, int]] = {}
    by_day: dict[str, dict[str, int]] = {}
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    cost_cfg = load_cost_settings()
    total_cost = 0.0
    for row in events:
        model = str(row.get("model") or "unknown")
        pt = int(row.get("prompt_tokens") or 0)
        ct = int(row.get("completion_tokens") or 0)
        tt = int(row.get("total_tokens") or pt + ct)
        row_cost = estimate_cost(pt, ct, cost_cfg=cost_cfg)
        total_cost += row_cost
        bucket = by_model.setdefault(
            model,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0, "cost": 0.0},
        )
        bucket["prompt_tokens"] += pt
        bucket["completion_tokens"] += ct
        bucket["total_tokens"] += tt
        bucket["calls"] += 1
        bucket["cost"] = float(bucket.get("cost") or 0) + row_cost
        totals["prompt_tokens"] += pt
        totals["completion_tokens"] += ct
        totals["total_tokens"] += tt
        totals["calls"] += 1
        ts = _parse_ts(str(row.get("ts") or ""))
        day = _local_date(ts).isoformat() if ts else "unknown"
        day_bucket = by_day.setdefault(
            day,
            {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0, "cost": 0.0},
        )
        day_bucket["prompt_tokens"] += pt
        day_bucket["completion_tokens"] += ct
        day_bucket["total_tokens"] += tt
        day_bucket["calls"] += 1
        day_bucket["cost"] = float(day_bucket.get("cost") or 0) + row_cost
    models_sorted = sorted(
        by_model.items(),
        key=lambda kv: kv[1]["total_tokens"],
        reverse=True,
    )
    days_sorted = sorted(by_day.items(), key=lambda kv: kv[0])
    return {
        "totals": {**totals, "cost": round(total_cost, 4)},
        "by_model": dict(models_sorted),
        "by_day": dict(days_sorted),
        "cost_settings": cost_cfg,
    }


def aggregate_period(
    events: list[dict[str, Any]],
    *,
    period: str = "month",
) -> list[tuple[str, int]]:
    """Group token totals by day, month, or year."""
    buckets: dict[str, int] = {}
    for row in events:
        ts = _parse_ts(str(row.get("ts") or ""))
        if not ts:
            key = "unknown"
        elif period == "year":
            key = str(ts.year)
        elif period == "day":
            key = _local_date(ts).isoformat()
        else:
            key = ts.strftime("%Y-%m")
        buckets[key] = buckets.get(key, 0) + int(row.get("total_tokens") or 0)
    return sorted(buckets.items(), key=lambda kv: kv[0])


def list_models() -> list[str]:
    seen: set[str] = set()
    for row in load_events():
        m = str(row.get("model") or "").strip()
        if m:
            seen.add(m)
    return sorted(seen, key=str.lower)
