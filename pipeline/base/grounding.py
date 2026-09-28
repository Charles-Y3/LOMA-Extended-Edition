# -*- coding: utf-8 -*-
"""Shared "get me real grounding context if any exists" resolver for any LLM-authored
surface that states facts (document/presentation body text, diagram/infographic/
poster authors). Priority: a user-attached source always wins over a web search;
a web search only runs if the user has enabled it and the content isn't purely
creative; otherwise the caller's prompt is unchanged and the LLM falls back to its
own (unverified) knowledge, same as before this module existed.

Reuses services/grounded_chat.py's real web-search machinery
(gather_grounded_context) and settings gate (web_grounding_enabled) rather than
reimplementing search — this module only adds the trigger policy needed for
report-shaped generation (documents/presentations/diagrams/infographics/charts/
posters): matches_live_fact_pattern(), the same live-fact/explicit-search keyword
match chat uses minus its task-verb skip (see _should_use_web()'s docstring for
why). This replaced a per-content-type broad_trigger flag (2026-09) that decided
whether to search from *what kind* of visual was requested rather than *what it
was actually about* — a chart and a diagram about the same evergreen topic used
to get different treatment for no good reason, and a chart about a genuinely
time-sensitive topic got the same mandatory treatment as one about nothing
time-sensitive at all, purely because both were charts."""
from __future__ import annotations

from typing import Callable

_SOURCE_EXCERPT_CHARS = 4000


def _source_excerpt(source_text: str, bundle) -> str:
    """`source_text`, if given, wins outright — a caller passing it directly
    already knows it's the right material (e.g. artifact_build.py passes the
    document/slide body already drafted, so an embedded diagram/infographic stays
    consistent with the surrounding report even when that report itself came from
    a web search rather than an attached file). Otherwise fall back to
    `bundle.unified_text` (a pipeline.schemas.task_schema.ContextBundle) when the
    caller has one in scope instead."""
    text = (source_text or "").strip() or (getattr(bundle, "unified_text", None) or "").strip()
    return text[:_SOURCE_EXCERPT_CHARS] if text else ""


def _should_use_web(query: str, *, settings: dict | None) -> bool:
    """Whether to attempt a search at all, for any report-shaped caller (document/
    presentation/diagram/infographic/chart/poster authoring). Content-based, not
    type-based: uses matches_live_fact_pattern() — the same live-fact/explicit-
    search keyword match chat uses, minus its task-verb skip (a report-shaped
    request is always phrased as a task, "create a chart of X", so that skip would
    exclude nearly everything here) — instead of the old per-content-type
    broad_trigger flag, which decided "should this search" purely from *what kind*
    of visual/document was requested rather than *what it's actually about*. A
    chart about "quitting procrastination" no longer searches just because it's a
    chart; a chart about "today's exchange rate" still does, because the query
    itself says so.

    This is deliberately only half the safety net, though — the OTHER half is each
    author function's own "insufficient_data" self-check (see
    services/infographic_generation.py / services/chart_generation.py), which lets
    the authoring LLM itself refuse when it genuinely isn't confident, instead of
    this upfront keyword match having to be perfect (it can't be: "weather 100
    years ago" matches the same keywords as "weather right now")."""
    from services.grounded_chat import web_grounding_enabled

    if not web_grounding_enabled(settings):
        return False

    from pipeline.query_intent_i18n import matches

    if matches(query, "creative_writing_skip"):
        return False

    from services.grounded_chat import matches_live_fact_pattern

    return matches_live_fact_pattern(query)


def resolve_generation_context(
    query: str,
    *,
    bundle=None,
    source_text: str = "",
    settings: dict | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Returns (context block to prepend to an authoring prompt, or "" if there's
    nothing to ground on; web sources used, or [] — only populated for the web-search
    path, since an attached source excerpt isn't an online citation). See module
    docstring for the priority order."""
    query = (query or "").strip()
    if not query:
        return "", []

    excerpt = _source_excerpt(source_text, bundle)
    if excerpt:
        if log_fn:
            log_fn("Grounding: using attached source material")
        return f"Source material:\n{excerpt}", []

    if not _should_use_web(query, settings=settings):
        return "", []

    try:
        from services.grounded_chat import gather_grounded_context

        # topic_relevance=True — the credibility/relevance filtering and Wikipedia
        # fallback it turns on (gather_grounded_context's docstring) are a strict
        # improvement for every caller here. It's also gather_grounded_context's own
        # default now — passed explicitly so that isn't a silent dependency.
        web_ctx, sources = gather_grounded_context(query, log_fn=log_fn, topic_relevance=True)
    except Exception as ex:
        if log_fn:
            log_fn(f"Grounding: web search failed, proceeding without it: {ex}")
        return "", []

    return web_ctx, sources
