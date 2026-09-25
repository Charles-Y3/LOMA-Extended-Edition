# -*- coding: utf-8 -*-
"""Robots.txt, rate limits, and responsible-use checks for web fetching."""
from __future__ import annotations

import threading
import time
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

_USER_AGENT = "LOMA/1.0 (+local research; user-initiated fetch)"
_MIN_INTERVAL_SEC = 2.0
_robots_cache: dict[str, tuple[RobotFileParser, float]] = {}
_robots_lock = threading.Lock()
_last_fetch: dict[str, float] = {}
_rate_lock = threading.Lock()
_CACHE_TTL = 3600.0

def responsible_use_notice() -> str:
    from pipeline.i18n import t

    return t("web.responsible_use_notice")


def research_mode_notice() -> str:
    from pipeline.i18n import t

    return t("research.mode_notice")


# Backward-compatible aliases (English at import; prefer notice functions in UI).
RESPONSIBLE_USE_NOTICE = (
    "Web content is fetched only from URLs you provide. LOMA respects robots.txt, "
    "rate-limits repeat requests, and caches pages to avoid unnecessary traffic. "
    "You are responsible for complying with each site's terms of use and copyright."
)

RESEARCH_MODE_NOTICE = (
    "Research uses its own web search when you choose Web sources — independent of "
    "Settings → Search the internet for grounded replies (that toggle is for chat only). "
    "LOMA discovers links via search, reads public article pages, uses Wikipedia's API "
    "only to supplement shortfalls, respects robots.txt, and rate-limits requests."
)

# Search-engine / portal pages: discovery only, never article sources.
_DISCOVERY_ONLY_SUFFIXES = (
    "google.com",
    "google.com.au",
    "bing.com",
    "duckduckgo.com",
    "yahoo.com",
    "baidu.com",
)

# Not useful as cited research sources (social, video portals, etc.).
_JUNK_SOURCE_SUFFIXES = (
    "googleusercontent.com",
    "gstatic.com",
    "microsoft.com",
    "youtube.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "reddit.com",
    "pinterest.com",
    "linkedin.com",
    "amazon.com",
)

_WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"


def user_agent() -> str:
    return _USER_AGENT


def _domain_key(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def host_of(url: str) -> str:
    return _domain_key(url)


def _host_matches_suffix(host: str, suffixes: tuple[str, ...]) -> bool:
    if not host:
        return False
    for suffix in suffixes:
        if host == suffix or host.endswith(f".{suffix}"):
            return True
    return False


def is_discovery_only_url(url: str) -> bool:
    """Search-provider pages — used to find links, not read as sources."""
    return _host_matches_suffix(host_of(url), _DISCOVERY_ONLY_SUFFIXES)


def is_allowed_source_url(url: str) -> bool:
    """Public article URLs suitable for research reading (post-search filter)."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False
    host = host_of(url)
    if is_discovery_only_url(url):
        return False
    if _host_matches_suffix(host, _JUNK_SOURCE_SUFFIXES):
        return False
    return True


def filter_research_hits(hits: list[dict]) -> list[dict]:
    """Drop search-engine and junk URLs before article fetch (GPT/Gemini-style)."""
    out: list[dict] = []
    seen: set[str] = set()
    for hit in hits or []:
        url = (hit.get("url") or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        if not is_allowed_source_url(url):
            continue
        seen.add(url)
        out.append(hit)
    return out


def _apply_rate_limit(domain: str) -> tuple[bool, str]:
    now = time.time()
    with _rate_lock:
        last = _last_fetch.get(domain, 0.0)
        if now - last < _MIN_INTERVAL_SEC:
            wait = _MIN_INTERVAL_SEC - (now - last)
            return False, f"Rate limit: wait {wait:.1f}s before fetching {domain} again."
        _last_fetch[domain] = now
    return True, ""


def check_research_request(url: str, *, kind: str = "article") -> tuple[bool, str]:
    """
    Policy gate for Research extension requests.

    kind:
      article — full robots.txt + rate limit (destination pages)
      wikipedia_api — rate limit only (official MediaWiki API)
      search_discovery — rate limit for search-provider hosts (link discovery)
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return False, "Only http(s) URLs are allowed."

    if kind == "wikipedia_api":
        if not (url or "").startswith(_WIKIPEDIA_API):
            return False, "Not a Wikipedia API endpoint."
        return _apply_rate_limit(_domain_key(url))

    if kind == "search_discovery":
        host = host_of(url)
        if host == "html.duckduckgo.com" or host.endswith("duckduckgo.com"):
            return _apply_rate_limit(host)
        if is_discovery_only_url(url):
            return _apply_rate_limit(host)
        return check_fetch_allowed(url)

    if not is_allowed_source_url(url):
        return False, "URL is not an allowed public article source."
    return check_fetch_allowed(url)


def _robots_for_url(url: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    base = f"{parsed.scheme}://{parsed.netloc}"
    now = time.time()
    with _robots_lock:
        cached = _robots_cache.get(base)
        if cached and now - cached[1] < _CACHE_TTL:
            return cached[0]
    robots_url = urljoin(base, "/robots.txt")
    rp = RobotFileParser()
    rp.set_url(robots_url)
    try:
        rp.read()
    except Exception:
        rp = RobotFileParser()
        rp.parse("User-agent: *\nAllow: /".splitlines())
    with _robots_lock:
        _robots_cache[base] = (rp, now)
    return rp


def check_fetch_allowed(url: str) -> tuple[bool, str]:
    """Return (allowed, reason). Empty reason when allowed."""
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in ("http", "https"):
        return False, "Only http(s) URLs can be fetched."
    if not parsed.netloc:
        return False, "Invalid URL."

    from services.security.policy_gate import decide

    verdict = decide("fetch_url", url=url)
    if not verdict.allowed:
        return False, verdict.reason

    domain = _domain_key(url)
    allowed, reason = _apply_rate_limit(domain)
    if not allowed:
        return False, reason

    rp = _robots_for_url(url)
    if rp is not None and not rp.can_fetch(_USER_AGENT, url):
        return False, f"Blocked by robots.txt for {_USER_AGENT}."
    return True, ""
