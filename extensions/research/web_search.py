# -*- coding: utf-8 -*-
"""Web search + source loading for the Research extension."""
from __future__ import annotations

import base64
import json
import re
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Any, Iterator

from services.web_fetch_policy import (
    check_research_request,
    filter_research_hits,
    is_allowed_source_url,
    user_agent,
)

_WIKI_API = "https://en.wikipedia.org/w/api.php"
_GOOGLE_SEARCH = "https://www.google.com/search"
_BING_SEARCH = "https://www.bing.com/search"


def _log(msg: str) -> None:
    try:
        from services.session import state

        state.add_log(msg)
    except Exception:
        pass


def _unwrap_google_url(url: str) -> str:
    raw = (url or "").strip()
    if "/url?" in raw and "google." in raw:
        try:
            parsed = urllib.parse.urlparse(raw)
            q = urllib.parse.parse_qs(parsed.query).get("q", [""])[0]
            if q.startswith("http"):
                return q
        except Exception:
            pass
    return raw


def _unwrap_bing_url(url: str) -> str:
    if "bing.com/ck/a" not in (url or ""):
        return url
    m = re.search(r"[?&]u=a1([^&]+)", url)
    if not m:
        return url
    try:
        raw = m.group(1)
        pad = "=" * (-len(raw) % 4)
        decoded = base64.b64decode(raw + pad).decode("utf-8", errors="ignore")
        if decoded.startswith("http"):
            return decoded
    except Exception:
        pass
    return url


def _unwrap_search_url(url: str) -> str:
    return _unwrap_google_url(_unwrap_bing_url(url))


def _normalize_hit(title: str, url: str, *, source: str = "web") -> dict[str, str]:
    link = _unwrap_search_url((url or "").strip())
    return {
        "title": (title or link).strip(),
        "url": link,
        "source": source,
    }


@contextmanager
def _playwright_page() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = None
        last_err = ""
        for channel in ("msedge", "chrome", None):
            try:
                if channel:
                    browser = p.chromium.launch(headless=True, channel=channel)
                else:
                    browser = p.chromium.launch(headless=True)
                break
            except Exception as exc:
                last_err = str(exc)
                continue
        if browser is None:
            raise RuntimeError(f"No browser for web search ({last_err or 'unknown'})")
        context = browser.new_context(
            user_agent=user_agent(),
            locale="en-AU",
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()
        try:
            yield page
        finally:
            try:
                context.close()
            except Exception:
                pass
            try:
                browser.close()
            except Exception:
                pass


def _dismiss_google_consent(page: Any) -> None:
    for sel in ("#L2AGLb", 'button:has-text("Accept all")', 'button:has-text("Reject all")'):
        try:
            btn = page.locator(sel).first
            if btn.count() and btn.is_visible(timeout=800):
                btn.click(timeout=2000)
                page.wait_for_timeout(400)
                return
        except Exception:
            continue


def _google_search_on_page(page: Any, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    url = _GOOGLE_SEARCH + "?" + urllib.parse.urlencode({"q": q, "hl": "en"})
    allowed, reason = check_research_request(url, kind="search_discovery")
    if not allowed:
        _log(f"Research search: Google blocked — {reason}")
        return []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=22000)
        _dismiss_google_consent(page)
        page.wait_for_timeout(1800)
        raw = page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const skip = /google\\.(com|com\\.au)\\/(search|url|aclk|preferences)/;
                const push = (title, href) => {
                    if (!title || !href || skip.test(href) || seen.has(href)) return;
                    seen.add(href);
                    out.push({ title, url: href });
                };
                for (const h3 of document.querySelectorAll('h3')) {
                    const a = h3.closest('a');
                    if (a && a.href) push((h3.innerText || '').trim(), a.href);
                }
                for (const a of document.querySelectorAll('div#search a[href^="http"]')) {
                    const h3 = a.querySelector('h3');
                    if (h3) push((h3.innerText || '').trim(), a.href);
                }
                return out;
            }"""
        )
    except Exception as exc:
        _log(f"Research search: Google failed ({exc})")
        return []

    out: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        hit = _normalize_hit(str(item.get("title") or ""), str(item.get("url") or ""), source="google")
        if hit["url"].startswith("http") and is_allowed_source_url(hit["url"]):
            out.append(hit)
        if len(out) >= max_results:
            break
    return out


def _bing_search_on_page(page: Any, query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    url = _BING_SEARCH + "?" + urllib.parse.urlencode({"q": q})
    allowed, reason = check_research_request(url, kind="search_discovery")
    if not allowed:
        _log(f"Research search: Bing blocked — {reason}")
        return []
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=22000)
        page.wait_for_timeout(1800)
        raw = page.evaluate(
            """() => {
                const out = [];
                const seen = new Set();
                const push = (title, href) => {
                    if (!title || !href || seen.has(href)) return;
                    seen.add(href);
                    out.push({ title, url: href });
                };
                for (const li of document.querySelectorAll('li.b_algo')) {
                    const a = li.querySelector('h2 a');
                    if (a && a.href) push((a.innerText || '').trim(), a.href);
                }
                for (const a of document.querySelectorAll('#b_results h2 a')) {
                    if (a.href) push((a.innerText || '').trim(), a.href);
                }
                return out;
            }"""
        )
    except Exception as exc:
        _log(f"Research search: Bing failed ({exc})")
        return []

    out: list[dict[str, str]] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        hit = _normalize_hit(str(item.get("title") or ""), str(item.get("url") or ""))
        if hit["url"].startswith("http") and is_allowed_source_url(hit["url"]):
            out.append(hit)
        if len(out) >= max_results:
            break
    return out


def _wiki_api(params: dict[str, str]) -> dict[str, Any]:
    url = _WIKI_API + "?" + urllib.parse.urlencode({**params, "format": "json"})
    allowed, reason = check_research_request(url, kind="wikipedia_api")
    if not allowed:
        raise RuntimeError(reason or "Wikipedia API request blocked")
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=14) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _wikipedia_search(query: str, *, max_results: int = 4) -> list[dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    try:
        data = _wiki_api(
            {
                "action": "query",
                "list": "search",
                "srsearch": q,
                "srlimit": str(max(1, min(max_results, 8))),
            }
        )
    except Exception as exc:
        _log(f"Research search: Wikipedia lookup failed ({exc})")
        return []

    out: list[dict[str, str]] = []
    for item in data.get("query", {}).get("search", []) or []:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        slug = urllib.parse.quote(title.replace(" ", "_"))
        out.append(
            _normalize_hit(
                title,
                f"https://en.wikipedia.org/wiki/{slug}",
                source="wikipedia",
            )
        )
    return out[:max_results]


def _wikipedia_extract(title: str, *, max_chars: int = 10000) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    try:
        data = _wiki_api(
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": "1",
                "exintro": "0",
                "titles": t,
            }
        )
    except Exception:
        return ""
    for page in (data.get("query", {}) or {}).get("pages", {}).values():
        text = str(page.get("extract") or "").strip()
        return text[:max_chars]
    return ""


def _dedupe_hits(hits: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    out: list[dict[str, str]] = []
    for hit in hits:
        url = (hit.get("url") or "").strip().rstrip("/")
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(hit)
    return out


def search_web(query: str, *, max_results: int = 5) -> list[dict[str, str]]:
    """Search web for a single query."""
    return search_web_batch([query], max_per_query=max_results)


def _browser_search_batch(
    queries: list[str],
    *,
    max_per_query: int,
    log_fn=None,
) -> list[dict[str, str]]:
    """Browser SERP discovery (Bing, then Google) when HTTP search returns no usable links."""
    hits: list[dict[str, str]] = []
    try:
        with _playwright_page() as page:
            for q in queries[:5]:
                if log_fn:
                    log_fn(f"Searching (browser Bing): {q}")
                batch = _bing_search_on_page(page, q, max_results=max_per_query)
                if not batch:
                    if log_fn:
                        log_fn(f"Searching (browser Google): {q}")
                    batch = _google_search_on_page(page, q, max_results=max_per_query)
                if batch:
                    hits.extend(batch)
    except Exception as exc:
        _log(f"Research search: browser search unavailable ({exc})")
        if log_fn:
            log_fn(f"Browser search unavailable: {exc}")
    return hits


def search_web_batch(
    queries: list[str],
    *,
    max_per_query: int = 4,
    min_total: int = 0,
    log_fn=None,
    allow_wikipedia: bool = True,
) -> list[dict[str, str]]:
    """
    Discover candidate source URLs (search → read articles only).

    Browser Bing/Google directly — DuckDuckGo's HTTP endpoint now serves an
    anti-bot challenge page instead of results, so it's not attempted.
    Wikipedia supplements only when web discovery is still short (never replaces web).
    """
    cleaned = [q.strip() for q in queries if (q or "").strip()]
    if not cleaned:
        return []

    per_q = max(3, int(max_per_query or 4))
    min_needed = max(per_q, int(min_total or 0))

    hits = _dedupe_hits(_browser_search_batch(cleaned, max_per_query=per_q, log_fn=log_fn))

    if allow_wikipedia and len(hits) < min_needed:
        if log_fn:
            log_fn("Supplementing with Wikipedia API sources…")
        wiki_cap = min(8, min_needed - len(hits) + 2)
        for q in cleaned[:3]:
            hits.extend(_wikipedia_search(q, max_results=min(5, wiki_cap)))
            hits = _dedupe_hits(hits)
            if len(hits) >= min_needed:
                break

    if allow_wikipedia and not hits and cleaned:
        hits.extend(_wikipedia_search(cleaned[0], max_results=per_q))

    result = filter_research_hits(_dedupe_hits(hits))
    if log_fn:
        web_n = sum(1 for h in result if "wikipedia.org" not in (h.get("url") or ""))
        wiki_n = len(result) - web_n
        log_fn(
            f"Search found {len(result)} article source(s) "
            f"({web_n} web, {wiki_n} Wikipedia) to read."
        )
    return result


def load_source(hit: dict[str, str], *, log_fn=None) -> dict[str, Any]:
    """Fetch body text for a search hit (policy-checked article read)."""
    url = (hit.get("url") or "").strip()
    title = (hit.get("title") or url).strip()
    source = (hit.get("source") or "web").strip().lower()

    if not is_allowed_source_url(url) and source != "wikipedia":
        if log_fn:
            log_fn(f"Skipped (not a public article URL): {title[:60]}")
        return {"url": url, "title": title, "text": "", "error": True}

    if source == "wikipedia" or "wikipedia.org/wiki/" in url:
        wiki_title = title
        if "wikipedia.org/wiki/" in url:
            wiki_title = urllib.parse.unquote(url.split("/wiki/", 1)[-1]).replace("_", " ")
        text = _wikipedia_extract(wiki_title)
        if text:
            return {"url": url, "title": title, "text": text, "error": False}
        if log_fn:
            log_fn(f"Wikipedia extract empty for {title}")

    allowed, reason = check_research_request(url, kind="article")
    if not allowed:
        if log_fn:
            from pipeline.i18n import t as tr

            log_fn(tr("news_brief.log_fetch_blocked", title=title[:60], reason=reason))
        return {"url": url, "title": title, "text": reason, "error": True}

    if log_fn:
        from pipeline.i18n import t as tr

        log_fn(tr("news_brief.log_reading", title=title[:80]))
    # check_research_request above just validated + rate-limited this URL — skip re-check.
    row = scrape_url(url, skip_policy_check=True)
    if title and title != url:
        row["title"] = title
    return row


def scrape_url(url: str, *, skip_policy_check: bool = False) -> dict[str, Any]:
    from services.web_fetch import scrape_website_text

    result = scrape_website_text(url, skip_policy_check=skip_policy_check)
    if isinstance(result, str):
        return {"url": url, "title": url, "text": result, "error": True}
    text = str(result.get("simple_text") or result.get("content") or "").strip()
    err = bool(result.get("error"))
    # If `url` was a redirect (e.g. a Google News link), cite the page actually landed on.
    final_url = str(result.get("final_url") or "").strip() or url
    return {"url": final_url, "title": url, "text": text[:12000], "error": err}
