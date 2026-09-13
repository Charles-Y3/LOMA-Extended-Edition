# -*- coding: utf-8 -*-
"""Search query builders for News Brief."""
from __future__ import annotations

NEWS_CATEGORIES: dict[str, str] = {
    "world": "World news",
    "business": "Business and markets",
    "technology": "Technology",
    "science": "Science",
    "politics": "Politics",
    "health": "Health",
    "sports": "Sports",
    "entertainment": "Entertainment",
}

NEWS_COUNTRIES: dict[str, str] = {
    "global": "Global",
    "us": "United States",
    "uk": "United Kingdom",
    "eu": "Europe",
    "china": "China",
    "japan": "Japan",
    "india": "India",
    "australia": "Australia",
    "canada": "Canada",
}

_COUNTRY_QUERY: dict[str, str] = {
    "global": "",
    "us": "United States",
    "uk": "United Kingdom",
    "eu": "Europe",
    "china": "China",
    "japan": "Japan",
    "india": "India",
    "australia": "Australia",
    "canada": "Canada",
}


TIMELINE_OPTIONS: dict[str, str] = {
    "today": "Today",
    "week": "Past week",
    "month": "Past month",
    "recent": "Recent (72 hours)",
}

_TIMELINE_QUERY: dict[str, str] = {
    "today": "today",
    "week": "this week",
    "month": "this month",
    "recent": "last 72 hours",
}


def build_search_queries(
    categories: list[str],
    countries: list[str],
    topics: list[str],
    *,
    timeline: str = "week",
    max_queries: int = 5,
) -> list[str]:
    cats = [c for c in categories if c in NEWS_CATEGORIES]
    if not cats:
        cats = ["world"]
    countries = [c for c in countries if c in NEWS_COUNTRIES] or ["global"]
    topics = [t.strip() for t in topics if (t or "").strip()]
    time_hint = _TIMELINE_QUERY.get(timeline or "week", "this week")

    queries: list[str] = []
    for topic in topics[:3]:
        region = _COUNTRY_QUERY.get(countries[0], "")
        q = f"{topic} news {time_hint}"
        if region:
            q += f" {region}"
        queries.append(q)

    for cat in cats[:3]:
        cat_label = NEWS_CATEGORIES[cat].split(" and ")[0]
        for country in countries[:2]:
            region = _COUNTRY_QUERY.get(country, "")
            parts = ["latest news", time_hint, cat_label]
            if region:
                parts.append(region)
            if topics:
                parts.append(topics[0])
            queries.append(" ".join(parts))

    for topic in topics[1:3]:
        region = _COUNTRY_QUERY.get(countries[0], "")
        q = f"latest news {time_hint} {topic}"
        if region:
            q += f" {region}"
        queries.append(q)

    seen: set[str] = set()
    out: list[str] = []
    for q in queries:
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(q)
        if len(out) >= max_queries:
            break
    return out
