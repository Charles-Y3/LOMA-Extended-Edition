# -*- coding: utf-8 -*-
"""News-only search and article loading."""
from __future__ import annotations

import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Callable
from urllib.parse import urlparse

from extensions.research.web_search import load_source, scrape_url, search_web_batch
from pipeline.i18n import t as tr

_BLOCKED_HOSTS = (
    "wikipedia.org",
    "wikimedia.org",
    "wikidata.org",
    "wiktionary.org",
    "britannica.com",
    "fandom.com",
)

_JUNK_HOSTS = (
    "google.com",
    "googleusercontent.com",
    "gstatic.com",
    "bing.com",
    "microsoft.com",
    "office.com",
    "live.com",
    "youtube.com",
    "facebook.com",
    "twitter.com",
    "x.com",
    "instagram.com",
    "tiktok.com",
    "amazon.com",
    "reddit.com",
    "pinterest.com",
    "linkedin.com",
)

_ALLOWED_NEWS_HOSTS = (
    "news.google.com",
)

_MIN_ARTICLE_CHARS = 80
_ERROR_PREFIXES = ("error:", "web extraction", "failed to isolate", "pipeline failure")


class NewsBriefAborted(Exception):
    """Raised to unwind the worker thread when the user hits Stop."""


def _host(url: str) -> str:
    try:
        return (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return ""


def _host_blocked(host: str) -> bool:
    if not host:
        return True
    if host in _ALLOWED_NEWS_HOSTS:
        return False
    for blocked in _BLOCKED_HOSTS:
        if host == blocked or host.endswith(f".{blocked}"):
            return True
    for junk in _JUNK_HOSTS:
        if host == junk or host.endswith(f".{junk}"):
            return True
    return False


def is_news_url(url: str) -> bool:
    return not _host_blocked(_host(url))


def _looks_like_article(text: str) -> bool:
    body = (text or "").strip()
    if len(body) < _MIN_ARTICLE_CHARS:
        return False
    low = body[:120].lower()
    return not any(low.startswith(p) for p in _ERROR_PREFIXES)


def _extract_text(row: dict[str, Any]) -> str:
    for key in ("text", "content", "simple_text"):
        val = (row.get(key) or "").strip()
        if val:
            return val
    return ""


def filter_news_hits(hits: list[dict[str, str]]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for hit in hits:
        url = (hit.get("url") or "").strip().rstrip("/")
        if not url or url in seen or not is_news_url(url):
            continue
        seen.add(url)
        out.append(hit)
    return out


def _google_news_rss(query: str, *, max_results: int = 10) -> list[dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    rss_url = (
        "https://news.google.com/rss/search?"
        + urllib.parse.urlencode({"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"})
    )
    try:
        req = urllib.request.Request(
            rss_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; LOMA-NewsBrief/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)
    except Exception:
        return []

    out: list[dict[str, str]] = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        source_el = item.find("source")
        pub_url = ""
        if source_el is not None:
            pub_url = (source_el.get("url") or source_el.text or "").strip()
        # `<source url="...">` is the PUBLISHER'S HOMEPAGE (e.g. https://www.nature.com),
        # not the article — using it as the fetch/citation URL meant scraping the site's
        # front page instead of the actual story (and collapsed distinct articles from the
        # same publisher into "duplicates" during dedup, since they all shared that one
        # homepage URL). `link` is unique per story, so it takes priority even though the
        # Google News redirect itself can't be fetched directly (needs JS + blocked by its
        # own robots.txt) — _load_one_article() falls back to a title search to resolve it
        # to the real article. pub_url is only a last resort when an item has no link at all.
        article_url = link if link.startswith("http") else pub_url
        if not title or not article_url.startswith("http"):
            continue
        if not is_news_url(article_url):
            continue
        out.append(
            {
                "title": title,
                "url": article_url,
                "source": "google_news",
                "google_link": link,
            }
        )
        if len(out) >= max_results:
            break
    return out


def search_news_batch(
    queries: list[str],
    *,
    max_per_query: int = 4,
    log_fn: Callable[[str], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> list[dict[str, str]]:
    """Find news article URLs via Google News RSS, then Bing as backup."""
    cleaned = [q.strip() for q in queries if (q or "").strip()]
    if not cleaned:
        return []

    hits: list[dict[str, str]] = []
    for q in cleaned[:6]:
        if should_abort and should_abort():
            raise NewsBriefAborted()
        rss_q = q if "news" in q.lower() else f"{q} news"
        if log_fn:
            log_fn(tr("news_brief.log_searching", query=rss_q[:70]))
        hits.extend(_google_news_rss(rss_q, max_results=max_per_query))

    hits = filter_news_hits(hits)

    if should_abort and should_abort():
        raise NewsBriefAborted()

    if len(hits) < 3:
        if log_fn:
            log_fn(tr("news_brief.log_supplementing"))
        hits.extend(
            filter_news_hits(
                search_web_batch(
                    cleaned[:3],
                    max_per_query=max_per_query,
                    log_fn=log_fn,
                    allow_wikipedia=False,
                )
            )
        )

    seen: set[str] = set()
    deduped: list[dict[str, str]] = []
    for hit in hits:
        url = (hit.get("url") or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        deduped.append(hit)

    if log_fn:
        log_fn(tr("news_brief.log_found_urls", count=len(deduped)))
    return deduped


def _row_has_article(row: dict[str, Any] | None) -> bool:
    """True only for a genuinely fetched article — not a blocked/failed fetch.

    load_source()/scrape_url() already set an accurate error flag (robots.txt block,
    missing browser, extraction failure, etc.), but their placeholder text for those
    cases (e.g. "Blocked by robots.txt for LOMA/1.0...", or the longer "⚠️ Web fetch
    blocked" message from scrape_website_text) is long enough and doesn't start with
    any of _ERROR_PREFIXES, so relying on _looks_like_article(text) alone let blocked
    fetches through as if they were real article content — the LLM then had nothing
    to summarize for that source and hallucinated instead. Check the error flag first.
    """
    if not row or row.get("error"):
        return False
    return _looks_like_article(_extract_text(row))


def _load_one_article(hit: dict[str, str], log_fn: Callable[[str], None] | None) -> dict[str, Any] | None:
    url = (hit.get("url") or "").strip()
    title = (hit.get("title") or url).strip()
    if not url or not is_news_url(url):
        return None
    if log_fn:
        log_fn(tr("news_brief.log_scraping", title=title[:70]))

    row = load_source(hit, log_fn=log_fn)
    if not _row_has_article(row):
        row = scrape_url(url)
        if not _row_has_article(row) and hit.get("google_link") and hit["google_link"] != url:
            row = scrape_url(str(hit["google_link"]))

    if not _row_has_article(row) and title and title != url:
        # The Google News link needs client-side JS to resolve to the real article and its
        # own robots.txt disallows fetching it directly, so it can never be scraped as-is —
        # find the actual article via a normal web search on its headline instead of
        # falling back to the publisher's homepage (which is readable but isn't the story).
        from extensions.research.web_search import search_web_batch

        for fb_hit in filter_news_hits(search_web_batch([title], max_per_query=1, allow_wikipedia=False)):
            fb_url = (fb_hit.get("url") or "").strip()
            if not fb_url or fb_url == url:
                continue
            row = scrape_url(fb_url)
            if _row_has_article(row):
                url = fb_url
                # The fallback often lands on a different outlet's coverage of the same
                # story (or a syndicated copy) rather than the original hit's publisher —
                # keep the title in sync with what's actually being cited, or "Source"
                # (the original headline) ends up attached to the wrong outlet's page.
                title = (fb_hit.get("title") or title).strip()
                break

    if not _row_has_article(row):
        return None

    text = _extract_text(row)
    # row["url"] may be the page the browser actually landed on (scrape_url resolves
    # redirects, e.g. a Google News link) — cite that instead of the pre-fetch URL.
    resolved_url = (row.get("url") or "").strip() or url
    return {
        "url": resolved_url,
        "title": title,
        "text": text[:12000],
        "source": (hit.get("source") or "web").strip(),
        "error": False,
    }


def load_news_sources(
    hits: list[dict[str, str]],
    *,
    max_sources: int = 10,
    log_fn: Callable[[str], None] | None = None,
    on_loaded: Callable[[dict[str, Any]], None] | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> list[dict[str, Any]]:
    loaded: list[dict[str, Any]] = []
    for hit in hits:
        if len(loaded) >= max_sources:
            break
        if should_abort and should_abort():
            raise NewsBriefAborted()
        row = _load_one_article(hit, log_fn)
        if row:
            loaded.append(row)
            if on_loaded:
                on_loaded(row)
    if log_fn:
        log_fn(tr("news_brief.log_loaded", count=len(loaded)))
    return loaded
