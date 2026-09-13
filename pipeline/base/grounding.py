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
report-shaped generation (documents/presentations/infographics/posters), which is
deliberately broader than chat's own needs_web_grounding() (see _should_use_web()
docstring for why reusing that narrow chat heuristic verbatim doesn't work here)."""
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


def _should_use_web(query: str, *, settings: dict | None, broad_trigger: bool) -> bool:
    """Chat's needs_web_grounding() is tuned narrow (weather/prices/"latest"/"today"
    phrasing) and explicitly excludes anything containing a task verb like "write"
    or "create" — correct for chat (where a "write me X" request should go to the
    planner, not web search) but wrong here, since a document/presentation request
    is *always* phrased as a task ("write a report on...") and would never pass
    that check. So report-shaped callers (broad_trigger=True) skip the phrasing
    heuristic entirely and ground by default, excluding only genuinely creative/
    fictional content — grounding a poem request would just waste a search."""
    from services.grounded_chat import web_grounding_enabled

    if not web_grounding_enabled(settings):
        return False

    from pipeline.query_intent_i18n import matches

    if matches(query, "creative_writing_skip"):
        return False

    if broad_trigger:
        return True

    from services.grounded_chat import needs_web_grounding

    return needs_web_grounding(query)


def resolve_generation_context(
    query: str,
    *,
    bundle=None,
    source_text: str = "",
    settings: dict | None = None,
    broad_trigger: bool = False,
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

    if not _should_use_web(query, settings=settings, broad_trigger=broad_trigger):
        return "", []

    try:
        from services.grounded_chat import gather_grounded_context

        web_ctx, sources = gather_grounded_context(query, log_fn=log_fn)
    except Exception as ex:
        if log_fn:
            log_fn(f"Grounding: web search failed, proceeding without it: {ex}")
        return "", []

    return web_ctx, sources
