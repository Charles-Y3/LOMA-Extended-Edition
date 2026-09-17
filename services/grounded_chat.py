# -*- coding: utf-8 -*-
"""Quick web grounding for general chat (weather, local info, current events).

When web grounding is ON, search runs ONLY for clear live-fact queries:

  YES — current weather, prices, news, scores, exchange rates, "latest …",
        "what is the temperature in …", short factual lookups.

  NO  — stories, poems, creative writing, pasted narratives, chat/tasks
        (translate, summarize, rewrite), opinions, coding help, greetings,
        or any message without explicit live-world keywords.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
import urllib.parse
import urllib.request
from typing import Any, Callable
from urllib.parse import urlparse

_MAX_PAGES = 3
_MAX_QUERY_LEN = 200
_MAX_SNIPPET_CHARS = 3500
_MAX_TOTAL_CONTEXT = 10000
_WTTR_USER_AGENT = "LOMA-Grounded/1.0 (local; weather lookup)"

# Structural English regexes below stay as-is for their grammatical precision; each
# gains a multilingual fallback via pipeline/query_intent_i18n.py's CONCEPTS through
# the wrapper classes further down (_OrRE etc.), so every .search()/.match() call site
# in this file benefits without needing to touch each one individually. See CLAUDE.md
# section 8. This module gates whether live web search runs at all — the highest-
# impact place in the codebase for this fix, since it silently disabled grounding
# entirely for any non-English query before this.
_LIVE_PATTERNS_EN = re.compile(
    r"\b("
    r"weather|forecast|temperature|rain(?:ing)?|snow|humidity|"
    r"today|tonight|tomorrow|right now|currently|this week|this weekend|"
    r"latest|recent|current|live score|who won|"
    r"best (?:place|spot|restaurant|hotel|beach|area|time|deal|price|buy)|"
    r"cheapest|lowest price|price for|price of|how much|cost of|"
    r"where (?:to|should i|can i|can you buy)|things to do|places to visit|"
    r"opening hours|is .* open|exchange rate|in stock|available in"
    r")\b",
    re.I,
)

# Timeless facts — answer from model knowledge; never route to web search.
_STATIC_FACT_EN = re.compile(
    r"\b("
    r"highest mountain|tallest mountain|largest mountain|"
    r"tallest building|tallest structure|"
    r"largest (?:country|ocean|lake|river|desert|continent)|"
    r"deepest (?:ocean|lake|trench)|"
    r"capital of|population of (?:the )?(?:world|earth)|"
    r"who (?:invented|discovered|wrote|painted|composed)|"
    r"when (?:was|did) .{0,40}\b(?:born|die|founded|invented)\b|"
    r"how many (?:planets|continents|oceans)|"
    r"speed of (?:light|sound)|"
    r"distance (?:to|from) (?:the )?moon|"
    r"formula for|periodic table|atomic number"
    r")\b",
    re.I,
)

_QUESTION_START_EN = re.compile(
    r"^\s*(what|which|where|when|who|how|is|are|can|could|does|do|did|will|should)\b",
    re.I,
)

_TASK_VERBS_EN = re.compile(
    r"\b(translate|translation|summarize|summarise|summar\w*|transcri\w*|"
    r"rewrite|rephrase|convert|localize|localise|edit|polish|fix|mutate|"
    r"revise|extract|analyse|analyze|draft|compose|write|output|generate|create)\b",
    re.I,
)
_TASK_VERB_CONCEPTS = (
    "verb_translate", "verb_summarize", "verb_transcribe", "verb_rewrite",
    "verb_extract", "verb_analyze", "verb_write_author", "image_deliverable_hints",
)

_GREETING_ONLY_EN = re.compile(r"^(hi|hello|hey|thanks|thank you|ok|okay|bye)[\s!.?]*$", re.I)

# Never web-search these (creative content, tasks, fiction).
_SKIP_GROUNDING_EN = re.compile(
    r"(?:"
    r"\bonce upon a time\b|^\s*once,\s|\b^\s*one (?:day|evening|morning|afternoon|sunny)\b|"
    r"\bwrite (?:me )?(?:a )?(?:story|poem|essay|fiction)\b|"
    r"\btell (?:me )?(?:a )?story\b|\bshort story\b|\bcreative (?:writing|fiction)\b|"
    r"\btell (?:me )?(?:a )?joke\b|\bjoke\b|笑话|笑話|讲笑话|讲笑話|"
    r"\bchapter \d+\b|\b(he|she|they) (?:said|whispered|replied|asked)\b|"
    r"\b(work of )?fiction\b|\bplot twist\b|\bcharacter named\b|"
    r"\b(feedback|critique|review) (?:on|for) (?:this|my) (?:story|writing|poem)\b|"
    r"\b(translate|summarize|summarise|rewrite|proofread|edit) (?:this|the|my)\b"
    r")",
    re.I | re.M,
)

# Must match at least one for a web search (toggle alone is insufficient).
_EXPLICIT_LIVE_EN = re.compile(
    r"\b("
    r"weather|forecast|temperature|rain(?:ing)?|snow|humidity|"
    r"today|tonight|tomorrow|right now|currently|this week|this weekend|"
    r"latest|recent|headline|news|current events|"
    r"live score|who won|"
    r"price|pricing|cheapest|lowest|cost|how much|buy|deal|in stock|"
    r"exchange rate|stock (?:price|market)|"
    r"opening hours|hours of operation|"
    r"hottest|coldest|"
    r"things to do|places to visit|restaurant|hotel near|"
    r"available in (?:australia|au|store)"
    r")\b",
    re.I,
)

_MAX_GROUNDING_QUERY = 2000
_NARRATIVE_MIN_LEN = 260

_WEATHER_PATTERNS_EN = re.compile(
    r"\b(weather|forecast|temperature|rain(?:ing)?|snow|humidity|feels like)\b",
    re.I,
)

_WEATHER_EXTREME_EN = re.compile(
    r"\b(highest|lowest|hottest|coldest|warmest|coolest|maximum|minimum|extreme|record)\b",
    re.I,
)

_WEATHER_LOCATION_BLOCK_EN = re.compile(
    r"\b(the world|worldwide|world|global|earth|the globe|the planet|everywhere)\b",
    re.I,
)


class _OrConceptRE:
    """Wraps an English structural regex; .search()/.match() also tries the
    multilingual concept(s) so every existing call site in this file gains
    non-English coverage without being edited individually."""

    def __init__(self, en_pattern: re.Pattern, concepts: tuple[str, ...], use_match: bool = False):
        self._en = en_pattern
        self._concepts = concepts
        self._use_match = use_match

    def search(self, text: str):
        from pipeline.query_intent_i18n import any_matches

        m = self._en.search(text)
        if m:
            return m
        return any_matches(text, self._concepts) or None

    def match(self, text: str):
        from pipeline.query_intent_i18n import any_matches

        m = self._en.match(text)
        if m:
            return m
        stripped = (text or "").strip()
        if self._use_match and len(stripped) > 24:
            # Anchored, whole-message checks (greeting-only) — a long message that
            # merely *contains* a greeting word isn't "greeting only"; only treat it
            # as a match when the message itself is short, like a real greeting.
            return None
        return any_matches(text, self._concepts) or None


_LIVE_PATTERNS = _OrConceptRE(_LIVE_PATTERNS_EN, ("live_grounding_patterns",))
_STATIC_FACT = _OrConceptRE(_STATIC_FACT_EN, ("static_fact_patterns",))
_QUESTION_START = _OrConceptRE(_QUESTION_START_EN, ("question_start_words",))
_TASK_VERBS = _OrConceptRE(_TASK_VERBS_EN, _TASK_VERB_CONCEPTS)
_GREETING_ONLY = _OrConceptRE(_GREETING_ONLY_EN, ("greeting_only",), use_match=True)
_SKIP_GROUNDING = _OrConceptRE(_SKIP_GROUNDING_EN, ("creative_writing_skip",))
_EXPLICIT_LIVE = _OrConceptRE(_EXPLICIT_LIVE_EN, ("live_grounding_patterns",))
_WEATHER_PATTERNS = _OrConceptRE(_WEATHER_PATTERNS_EN, ("weather_words",))
_WEATHER_EXTREME = _OrConceptRE(_WEATHER_EXTREME_EN, ("weather_extreme_words",))
_WEATHER_LOCATION_BLOCK = _OrConceptRE(_WEATHER_LOCATION_BLOCK_EN, ("weather_global_location_words",))

_LOCATION_SAFE = re.compile(r"^[\w\s,\-'\.]{2,60}$", re.UNICODE)

_BLOCKED_SCHEMES = frozenset({"file", "javascript", "data", "vbscript", "about"})


def web_grounding_enabled(settings: dict[str, Any] | None) -> bool:
    return bool((settings or {}).get("web_grounding_enabled"))


def grounded_search_disabled_message() -> str:
    from pipeline.i18n import t as tr

    return tr("chat.grounded_disabled")


def _looks_like_pasted_narrative(query: str) -> bool:
    """Long prose with no live-fact intent — e.g. pasted stories."""
    q = (query or "").strip()
    if len(q) < _NARRATIVE_MIN_LEN:
        return False
    if _EXPLICIT_LIVE.search(q) or _LIVE_PATTERNS.search(q):
        return False
    if _SKIP_GROUNDING.search(q):
        return True
    sentence_breaks = len(re.findall(r"[.!?](?:\s|$)", q))
    if sentence_breaks >= 2 and "?" not in q:
        return True
    if len(q) >= 400 and sentence_breaks >= 1 and not _QUESTION_START.search(q):
        return True
    return False


def _should_skip_grounding(query: str) -> bool:
    q = (query or "").strip()
    if not q:
        return True
    if _GREETING_ONLY.match(q):
        return True
    if _STATIC_FACT.search(q):
        return True
    if _SKIP_GROUNDING.search(q):
        return True
    if _TASK_VERBS.search(q):
        return True
    if _looks_like_pasted_narrative(q):
        return True
    return False


def grounding_skip_reason(query: str) -> str | None:
    """Human-readable reason when web search is not used."""
    q = (query or "").strip()
    if not q:
        return "empty message"
    if _GREETING_ONLY.match(q):
        return "greeting"
    if _STATIC_FACT.search(q):
        return "timeless factual question — no live web data needed"
    if _SKIP_GROUNDING.search(q):
        return "creative or task content"
    if _TASK_VERBS.search(q):
        return "task request (use express/planner, not web)"
    if _looks_like_pasted_narrative(q):
        return "pasted narrative — not a live-fact question"
    if not (_EXPLICIT_LIVE.search(q) or _LIVE_PATTERNS.search(q)):
        return "no live-fact keywords (weather, price, news, latest, …)"
    return None


def needs_web_grounding(query: str) -> bool:
    """True only when the message clearly needs current public-web facts."""
    q = (query or "").strip()
    if not q or len(q) > _MAX_GROUNDING_QUERY:
        return False
    if _should_skip_grounding(q):
        return False
    return bool(_EXPLICIT_LIVE.search(q) or _LIVE_PATTERNS.search(q))


def should_use_grounded_chat(
    query: str,
    settings: dict[str, Any] | None,
    *,
    output_type: str = "chat",
    has_attachments: bool = False,
) -> bool:
    if (output_type or "chat").strip().lower() != "chat":
        return False
    if has_attachments:
        return False
    if not web_grounding_enabled(settings):
        return False
    return needs_web_grounding(query)


def is_url_safe(url: str) -> bool:
    raw = (url or "").strip()
    if not raw or len(raw) > 2048:
        return False
    try:
        parsed = urlparse(raw)
    except Exception:
        return False
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False
    if scheme in _BLOCKED_SCHEMES:
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1"):
        return False
    if host.endswith(".local") or host.endswith(".internal"):
        return False
    try:
        for info in socket.getaddrinfo(host, None):
            addr = info[4][0]
            ip = ipaddress.ip_address(addr)
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
    except OSError:
        pass
    return True


def _search_queries(query: str) -> list[str]:
    q = (query or "").strip()[:_MAX_QUERY_LEN]
    if not q:
        return []
    if _WEATHER_PATTERNS.search(q):
        if _WEATHER_EXTREME.search(q):
            return [
                "hottest place on earth today current temperature",
                "highest temperature in the world right now",
            ]
        loc = _extract_weather_location(q)
        if loc:
            return [
                f"{loc} current weather temperature today",
                f"weather forecast {loc} now",
            ]
        return [f"{q} current weather forecast"]
    if re.search(r"\b(price|pricing|cheapest|lowest|cost|buy|deal|compare)\b", q, re.I):
        return [q, f"{q} review comparison"]
    return [q]


def _is_plausible_weather_place(name: str) -> bool:
    low = (name or "").strip().lower()
    if not low or not _LOCATION_SAFE.match(name.strip()):
        return False
    if _WEATHER_LOCATION_BLOCK.search(low):
        return False
    if re.search(
        r"\b(what|which|where|when|how|who|highest|lowest|hottest|coldest|current|temperature|weather)\b",
        low,
    ):
        return False
    return True


def _extract_weather_location(query: str) -> str:
    q = (query or "").strip()
    for pat in (
        r"\b(?:in|at|for|of)\s+([A-Za-z][A-Za-z\s,\-'\.]{1,48}?)(?:\s+(?:now|today|tonight|tomorrow|currently|right now)|[?.!]|$)",
        r"\b(?:weather|temperature|forecast)\s+(?:in|at|for|of)\s+([A-Za-z][A-Za-z\s,\-'\.]{1,48}?)(?:[?.!]|$)",
    ):
        m = re.search(pat, q, re.I)
        if m:
            loc = m.group(1).strip(" ,.-")
            if _is_plausible_weather_place(loc):
                return loc
    return ""


def _should_use_wttr_local_weather(query: str) -> bool:
    q = (query or "").strip()
    if not q or _WEATHER_EXTREME.search(q):
        return False
    loc = _extract_weather_location(q)
    if not loc:
        return False
    if _WEATHER_LOCATION_BLOCK.search(loc):
        return False
    return True


def _is_weather_query(query: str) -> bool:
    return bool(_WEATHER_PATTERNS.search(query or ""))


def _fetch_weather_context(
    query: str,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Live weather via wttr.in (read-only public HTTPS; no API key)."""
    location = _extract_weather_location(query)
    if not location:
        return "", []

    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    slug = urllib.parse.quote(location.strip())
    url = f"https://wttr.in/{slug}?format=j1"
    if not is_url_safe(url):
        return "", []

    log(f"Grounded chat: fetching live weather for {location}…")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": _WTTR_USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=14) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception as exc:
        log(f"Grounded chat: weather lookup failed ({exc})")
        return "", []

    cur_list = data.get("current_condition") or []
    if not cur_list:
        return "", []
    cur = cur_list[0] if isinstance(cur_list[0], dict) else {}

    area_name = location
    areas = data.get("nearest_area") or []
    if areas and isinstance(areas[0], dict):
        bits = [
            str(areas[0].get("areaName", [{}])[0].get("value") or "").strip()
            if isinstance(areas[0].get("areaName"), list)
            else "",
            str(areas[0].get("country", [{}])[0].get("value") or "").strip()
            if isinstance(areas[0].get("country"), list)
            else "",
        ]
        label = ", ".join(b for b in bits if b)
        if label:
            area_name = label

    desc = ""
    wdesc = cur.get("weatherDesc")
    if isinstance(wdesc, list) and wdesc:
        desc = str(wdesc[0].get("value") or "").strip()

    block = (
        f"### Live weather — {area_name}\n"
        f"URL: https://wttr.in/{slug}\n"
        f"- Condition: {desc or 'n/a'}\n"
        f"- Temperature: {cur.get('temp_C', '?')}°C ({cur.get('temp_F', '?')}°F)\n"
        f"- Feels like: {cur.get('FeelsLikeC', '?')}°C\n"
        f"- Humidity: {cur.get('humidity', '?')}%\n"
        f"- Wind: {cur.get('windspeedKmph', '?')} km/h\n"
        f"- Observation time (local): {cur.get('localObsDateTime', 'n/a')}"
    )
    sources = [{"title": f"Weather — {area_name}", "url": f"https://wttr.in/{slug}"}]
    log("Grounded chat: live weather data retrieved.")
    return block, sources


def gather_grounded_context(
    query: str,
    *,
    log_fn: Callable[[str], None] | None = None,
    topic_relevance: bool = False,
) -> tuple[str, list[dict[str, str]]]:
    """
    Search the web, fetch a few pages, return (context_block, sources).
    Only public http(s) URLs from search results are fetched.

    `topic_relevance=True` (used by report-shaped grounding — charts/diagrams/
    infographics/presentations, see pipeline/base/grounding.py) turns on two
    mechanical, no-extra-LLM-call filters that plain chat grounding doesn't
    need: a lenient domain-credibility gate (drops personal blogs/forums and
    obvious non-data sources like presentation-template marketplaces before
    they're even fetched — see pipeline/base/source_relevance.py) and
    sentence-level relevance extraction (a fetched page is usually mostly
    irrelevant to the one fact/number being asked for; this keeps only the
    handful of sentences that actually mention it, instead of truncating to
    the first N characters and hoping). A page that survives credibility but
    has nothing relevant is dropped entirely rather than included thin —
    "no real support found" is the correct signal for a chart/diagram to
    refuse instead of fabricating.
    """
    from extensions.research.web_search import load_source, search_web_batch

    def log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    queries = _search_queries(query)
    if not queries:
        return "", []

    blocks: list[str] = []
    sources: list[dict[str, str]] = []

    if _is_weather_query(query) and _should_use_wttr_local_weather(query):
        weather_block, weather_sources = _fetch_weather_context(query, log_fn=log)
        if weather_block:
            ctx = (
                "WEB SNIPPETS (use for factual answers; cite URLs when helpful):\n\n"
                + weather_block
            )
            from pipeline.i18n import t as tr

            log(tr("console.grounded_using", n=len(weather_sources)))
            return ctx, weather_sources

    from pipeline.i18n import t as tr

    log(tr("console.grounded_searching"))
    hits = search_web_batch(
        queries,
        max_per_query=5,
        log_fn=log,
        allow_wikipedia=False,
    )
    safe_hits = [h for h in hits if is_url_safe(h.get("url") or "")]
    if topic_relevance and safe_hits:
        from pipeline.base.source_relevance import (
            CREDIBILITY_MIN,
            heuristic_credibility,
            is_non_data_source,
        )

        filtered = []
        for h in safe_hits:
            url, title = (h.get("url") or "").strip(), (h.get("title") or "").strip()
            if is_non_data_source(title, url):
                continue
            score, _note = heuristic_credibility(url, title)
            if score >= CREDIBILITY_MIN:
                filtered.append(h)
        safe_hits = filtered
    if not safe_hits and not blocks:
        log(tr("console.grounded_no_urls"))
        return "", []

    total = sum(len(b) for b in blocks)

    for hit in safe_hits[: _MAX_PAGES + 2]:
        if len(sources) >= _MAX_PAGES:
            break
        url = (hit.get("url") or "").strip()
        if not is_url_safe(url):
            continue
        loaded = load_source(hit, log_fn=log)
        if loaded.get("error"):
            continue
        text = (loaded.get("text") or "").strip()
        if not text or len(text) < 80:
            continue
        title = (loaded.get("title") or hit.get("title") or url).strip()
        if topic_relevance:
            from pipeline.base.source_relevance import extract_relevant_sentences

            relevant = extract_relevant_sentences(text, query, max_sentences=6)
            if not relevant:
                # Credible source, but nothing in it actually addresses this
                # request — skip rather than pad the context with noise.
                continue
            snippet = " ".join(relevant)
        else:
            snippet = text[:_MAX_SNIPPET_CHARS]
            if len(text) > _MAX_SNIPPET_CHARS:
                snippet += "…"
        block = f"### {title}\nURL: {url}\n{snippet}"
        if total + len(block) > _MAX_TOTAL_CONTEXT:
            break
        blocks.append(block)
        total += len(block)
        sources.append({"title": title, "url": url})

    if not blocks:
        log(tr("console.grounded_no_text"))
        return "", []

    ctx = (
        "WEB SNIPPETS (use for factual answers; cite URLs when helpful):\n\n"
        + "\n\n---\n\n".join(blocks)
    )
    log(tr("console.grounded_using", n=len(sources)))
    return ctx, sources


def grounded_system_prompt() -> str:
    return (
        "You are LOMA. Answer using the web snippets when provided.\n"
        "Rules:\n"
        "- Prefer facts from WEB SNIPPETS over general knowledge for time-sensitive questions.\n"
        "- For live weather blocks, report the temperature and conditions for that place only.\n"
        "- For global extremes (hottest/coldest/highest/lowest in the world), use web snippets — "
        "do not treat one city reading as a world record.\n"
        "- For prices or shopping, quote only figures explicitly present in snippets; say if none found.\n"
        "- If snippets do not contain the answer, say you could not verify from the web.\n"
        "- Be concise. Mention source titles or URLs when citing specific facts.\n"
        "- Do not invent URLs, statistics, or current conditions."
    )


def _heuristic_grounded_mismatch(query: str, draft: str, web_ctx: str) -> str | None:
    """Return correction hint when draft obviously misreads snippets."""
    q = (query or "").strip()
    d = (draft or "").strip()
    ctx = web_ctx or ""
    if not d:
        return None
    if (_WEATHER_EXTREME.search(q) or _WEATHER_LOCATION_BLOCK.search(q)) and "Live weather —" in ctx:
        if "world" in q.lower() or _WEATHER_EXTREME.search(q):
            return (
                "The question asks about a global extreme, but snippets only contain one locality's weather. "
                "Do not present a single city reading as the world highest/lowest."
            )
    return None


def refine_grounded_answer(
    query: str,
    web_ctx: str,
    draft: str,
    *,
    model: str,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    """Second-pass check: ensure the draft matches snippets and the question."""
    text = (draft or "").strip()
    if not text:
        return draft

    hint = _heuristic_grounded_mismatch(query, text, web_ctx)
    if hint and log_fn:
        log_fn("Grounded chat: sanity check flagged a possible mismatch.")

    try:
        from services.llm_bridge import chat

        user_block = (
            f"WEB SNIPPETS:\n{web_ctx[:8500]}\n\n"
            f"USER QUESTION:\n{query}\n\n"
            f"DRAFT ANSWER:\n{text}\n"
        )
        if hint:
            user_block += f"\nCHECKER HINT:\n{hint}\n"

        resp = chat(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You verify draft answers that must use ONLY the web snippets.\n"
                        "Reply with exactly one line:\n"
                        "- OK: — draft is supported by snippets and answers the question\n"
                        "- REVISE: <corrected answer> — draft overclaims or misreads snippets\n"
                        "- REJECT: <brief explanation> — snippets cannot answer the question\n"
                        "Do not invent facts beyond snippets."
                    ),
                },
                {"role": "user", "content": user_block},
            ],
            stream=False,
            options={"num_predict": 512, "temperature": 0.1},
        )
        line = (resp.get("message", {}).get("content") or "").strip()
        # Small local models don't reliably follow "reply with exactly one line" — the
        # verdict tag can land mid-paragraph after a preamble instead of at line start.
        # Search the whole reply (not just line 1) so a real REJECT/REVISE verdict is
        # never silently dropped, which would let an unverified/hallucinated draft
        # through to the user indistinguishable from a clean pass.
        match = re.search(r"\b(OK|REVISE|REJECT)\s*:\s*(.*)", line, re.I | re.S)
        if match:
            tag = match.group(1).upper()
            rest = match.group(2).strip()
            if tag == "OK":
                return text
            if tag == "REVISE":
                revised = rest.split("\n", 1)[0].strip()
                if log_fn:
                    log_fn("Grounded chat: answer revised after sanity check.")
                return revised or text
            if tag == "REJECT":
                reason = rest.split("\n", 1)[0].strip() or "The web snippets did not support a reliable answer."
                if log_fn:
                    log_fn("Grounded chat: answer rejected after sanity check.")
                return (
                    f"I could not verify a reliable answer from the web snippets.\n\n{reason}"
                )
    except Exception as exc:
        if log_fn:
            log_fn(f"Grounded chat: sanity check skipped ({exc})")
    return text
