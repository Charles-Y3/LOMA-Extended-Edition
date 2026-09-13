# -*- coding: utf-8 -*-
"""Localized News Brief filter option labels."""
from __future__ import annotations

from pipeline.i18n import t as tr

_CATEGORY_KEYS = {
    "world": "news_brief.cat_world",
    "business": "news_brief.cat_business",
    "technology": "news_brief.cat_technology",
    "science": "news_brief.cat_science",
    "politics": "news_brief.cat_politics",
    "health": "news_brief.cat_health",
    "sports": "news_brief.cat_sports",
    "entertainment": "news_brief.cat_entertainment",
}

_COUNTRY_KEYS = {
    "global": "news_brief.country_global",
    "us": "news_brief.country_us",
    "uk": "news_brief.country_uk",
    "eu": "news_brief.country_eu",
    "china": "news_brief.country_china",
    "japan": "news_brief.country_japan",
    "india": "news_brief.country_india",
    "australia": "news_brief.country_australia",
    "canada": "news_brief.country_canada",
}

_TIMELINE_KEYS = {
    "today": "news_brief.timeline_today",
    "week": "news_brief.timeline_week",
    "month": "news_brief.timeline_month",
    "recent": "news_brief.timeline_recent",
}


def localized_news_categories() -> dict[str, str]:
    return {k: tr(v) for k, v in _CATEGORY_KEYS.items()}


def localized_news_countries() -> dict[str, str]:
    return {k: tr(v) for k, v in _COUNTRY_KEYS.items()}


def localized_timeline_options() -> dict[str, str]:
    return {k: tr(v) for k, v in _TIMELINE_KEYS.items()}
