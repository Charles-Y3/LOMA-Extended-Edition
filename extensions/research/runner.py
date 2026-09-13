# -*- coding: utf-8 -*-

"""Research execution — gather, credibility, scan, compare, author."""

from __future__ import annotations



import os

from dataclasses import dataclass, field

from typing import Any, Callable



from extensions.research.brief import ResearchBrief

from extensions.research import llm as research_llm

from extensions.research.pipeline import (

    build_notes_and_report,

    candidate_from_hit,

    candidate_from_upload,

    select_and_analyze_sources,

)

from extensions.research.progress_i18n import localize_progress_line

from extensions.research.sources import ResearchSource

from extensions.research.web_search import load_source, search_web_batch

from pipeline.i18n import t as tr

from services.source_parser import parse_file





@dataclass

class ResearchProgress:

    phase: str = ""

    fraction: float = 0.0

    log: list[str] = field(default_factory=list)



    def emit(self, msg: str, *, phase: str = "", fraction: float | None = None) -> None:

        self.log.append(msg)

        if phase:

            self.phase = phase

        if fraction is not None:

            self.fraction = max(0.0, min(1.0, fraction))





def _parse_upload(path: str, name: str) -> ResearchSource:

    parsed = parse_file(path, filename=name)

    text = (parsed.context_text() or parsed.markdown or "").strip()

    if not text and parsed.raw:

        text = str(parsed.raw.get("content") or "")

    return candidate_from_upload(name, text)





def run_research(

    brief: ResearchBrief,

    *,

    upload_paths: list[tuple[str, str]],

    on_progress: Callable[[ResearchProgress], None] | None = None,

) -> tuple[str, str, list[str], list[ResearchSource]]:

    """

    Returns (markdown_deliverable, synthesis_notes, log_lines, sources_used).

    """

    prog = ResearchProgress()

    logs: list[str] = []

    target = max(3, min(15, int(brief.source_count or 6)))

    candidate_cap = min(20, max(target + 4, target * 2))



    def tick(msg: str, *, phase: str = "", fraction: float | None = None) -> None:

        localized = localize_progress_line(msg)

        prog.emit(localized, phase=phase, fraction=fraction)

        logs.append(localized)

        if on_progress:

            on_progress(prog)



    candidates: list[ResearchSource] = []

    steps = 5

    step_i = 0



    def advance(phase: str, msg: str) -> None:

        nonlocal step_i

        step_i += 1

        tick(msg, phase=phase, fraction=step_i / steps)



    if brief.uses_web():

        advance("search", tr("research.progress.plan_search"))

        queries = research_llm.plan_search_queries(brief, log_fn=tick)

        brief.search_queries = queries or brief.search_queries

        tick(tr("research.progress.searching_web", target=target), phase="search")

        query_count = max(1, len(brief.search_queries[:5]))

        per_query = max(5, (candidate_cap + query_count - 1) // query_count)

        hits = search_web_batch(

            brief.search_queries[:5],

            max_per_query=per_query,

            min_total=target,

            log_fn=tick,

        )

        for hit in hits:

            if len(candidates) >= candidate_cap:

                break

            url = hit.get("url") or ""

            title = hit.get("title") or url

            if not url:

                continue

            tick(tr("research.progress.reading", title=title[:90]), phase="gather")

            loaded = load_source(hit, log_fn=tick)

            text = (loaded.get("text") or "").strip()

            # loaded["error"] is True for a blocked/failed fetch (robots.txt, missing
            # browser, extraction failure) — its "text" is just the block/error reason,
            # not real article content. Accepting it here fed placeholder text to the
            # LLM as if it were a genuine source, causing hallucinated summaries and
            # citations that don't match the actual (never-fetched) page.
            if text and not loaded.get("error"):

                candidates.append(candidate_from_hit(hit, text))

            else:

                tick(tr("research.progress.no_text", title=title[:60]))

        advance("gather", tr("research.progress.loaded_pages", count=len(candidates)))



    if brief.uses_uploads() and upload_paths:

        tick(tr("research.progress.reading_uploads", count=len(upload_paths)), phase="uploads")

        for path, name in upload_paths:

            if not os.path.isfile(path):

                tick(tr("research.progress.skip_missing", name=name))

                continue

            try:

                candidates.append(_parse_upload(path, name))

                tick(tr("research.progress.parsed_upload", name=name))

            except Exception as exc:

                tick(tr("research.progress.upload_failed", name=name, error=exc))



    if not candidates:

        tick(tr("research.progress.no_external"), phase="synthesize")

        candidates.append(

            candidate_from_upload(

                "Research brief",

                (

                    f"Topic: {brief.topic}\nAudience: {brief.audience}\n"

                    f"Constraints: {brief.constraints or 'none'}\n"

                    f"Original request: {brief.original_prompt}"

                ),

            )

        )



    advance("credibility", tr("research.progress.ranking"))

    sources = select_and_analyze_sources(brief, candidates, log_fn=tick)



    advance("synthesize", tr("research.progress.synthesizing"))

    notes, deliverable = build_notes_and_report(brief, sources, log_fn=tick)



    tick(tr("research.progress.complete"), phase="done", fraction=1.0)

    return deliverable.strip(), notes.strip(), logs, sources





def save_research_markdown(content: str, *, stem: str = "research") -> str:

    out_dir = os.path.join("data", "research", "output")

    os.makedirs(out_dir, exist_ok=True)

    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in stem)[:60] or "research"

    path = os.path.join(out_dir, f"{safe}.txt")

    n = 1

    while os.path.exists(path):

        path = os.path.join(out_dir, f"{safe}_{n}.txt")

        n += 1

    with open(path, "w", encoding="utf-8") as f:

        f.write(content)

    return path


