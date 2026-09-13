# -*- coding: utf-8 -*-
"""Curated real historical encounters for History Events."""
from __future__ import annotations

import random
from dataclasses import dataclass, field

ERA_ANY = "__any__"

ERA_BUCKET_LABELS: dict[str, str] = {
    ERA_ANY: "Any era",
    "ancient": "Ancient (to 500 CE)",
    "medieval": "Medieval (500–1500)",
    "early_modern": "Early modern (1500–1800)",
    "nineteenth": "19th century",
    "twentieth": "20th century",
    "twenty_first": "21st century",
}


def normalize_era_bucket(era_bucket: str) -> str:
    bucket = (era_bucket or "").strip()
    if bucket in ("", ERA_ANY):
        return ""
    return bucket


@dataclass(frozen=True)
class HistoricalEncounter:
    id: str
    title: str
    era_bucket: str
    era: str
    region: str
    category: str
    setup: str
    question: str
    historical_context: str
    what_happened: str
    key_factors: tuple[str, ...] = field(default_factory=tuple)
    image_hint: str = ""

    @property
    def image_prompt(self) -> str:
        hint = (self.image_hint or self.title).strip()
        return (
            f"Oil painting, historical scene, {hint}, period {self.era}, "
            f"{self.region}, dramatic cinematic lighting, detailed, no text, no watermark"
        )


from extensions.history_events.encounters_data import ALL_ENCOUNTERS  # noqa: E402

ENCOUNTERS: tuple[HistoricalEncounter, ...] = tuple(ALL_ENCOUNTERS)


def pick_encounter(
    *,
    exclude: set[str] | None = None,
    era_bucket: str = "",
) -> HistoricalEncounter:
    bucket = normalize_era_bucket(era_bucket)
    exc = exclude or set()

    def _pool(skip_seen: bool) -> list[HistoricalEncounter]:
        items = ENCOUNTERS if skip_seen else [e for e in ENCOUNTERS if e.id not in exc]
        if bucket:
            items = [e for e in items if e.era_bucket == bucket]
        return list(items)

    pool = _pool(skip_seen=False)
    if not pool and bucket:
        pool = _pool(skip_seen=True)
    if not pool and not bucket:
        pool = list(ENCOUNTERS)
    if not pool:
        raise ValueError(f"No encounters for era filter: {bucket or 'any'}")
    return random.choice(pool)


def encounter_by_id(encounter_id: str) -> HistoricalEncounter | None:
    for e in ENCOUNTERS:
        if e.id == encounter_id:
            return e
    return None


def list_categories() -> list[str]:
    return sorted({e.category for e in ENCOUNTERS})


def list_era_buckets() -> list[str]:
    return [k for k in ERA_BUCKET_LABELS if k != ERA_ANY]
