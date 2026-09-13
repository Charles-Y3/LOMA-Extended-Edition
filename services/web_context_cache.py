# -*- coding: utf-8 -*-
"""Session-scoped web scrape cache (until link removed or workspace reboot)."""
from __future__ import annotations

from urllib.parse import urlparse, urlunparse


def canonical_web_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        p = urlparse(raw)
        scheme = (p.scheme or "https").lower()
        netloc = (p.netloc or "").lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]
        path = (p.path or "").rstrip("/") or ""
        return urlunparse((scheme, netloc, path, "", "", ""))
    except Exception:
        return raw.rstrip("/")


def scrape_text_from_payload(payload) -> str:
    if isinstance(payload, dict):
        if payload.get("error"):
            return ""
        return (payload.get("simple_text") or payload.get("content") or "").strip()
    return str(payload or "").strip()


def _cache_dict() -> dict:
    from services.session import state

    cache = getattr(state, "web_scrape_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        state.web_scrape_cache = cache
    return cache


def get_cached(url: str):
    cache = _cache_dict()
    for key in (url, canonical_web_url(url)):
        if not key:
            continue
        hit = cache.get(key)
        if hit is not None and scrape_text_from_payload(hit):
            return hit
    return None


def set_cached(url: str, payload) -> None:
    text = scrape_text_from_payload(payload)
    if not text:
        return
    cache = _cache_dict()
    for key in (url, canonical_web_url(url)):
        if key:
            cache[key] = payload


def drop_cached(url: str) -> None:
    cache = _cache_dict()
    for key in (url, canonical_web_url(url)):
        cache.pop(key, None)


def merge_cached_links(links: list[str] | None, web_md: dict[str, str] | None = None) -> dict[str, str]:
    out = dict(web_md or {})
    for link in links or []:
        u = (link or "").strip()
        if not u or (out.get(u) or "").strip():
            continue
        hit = get_cached(u)
        if hit:
            text = scrape_text_from_payload(hit)
            if text:
                out[u] = text
    return out
