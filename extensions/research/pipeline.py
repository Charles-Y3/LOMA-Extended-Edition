# -*- coding: utf-8 -*-

"""Map-reduce research pipeline: credibility → per-source scan → compare → author."""

from __future__ import annotations



from typing import Any, Callable



from extensions.research.brief import ResearchBrief

from extensions.research import llm as research_llm

from extensions.research.sources import (

    ResearchSource,

    append_references,

    assign_source_ids,

    hit_to_source,

    upload_to_source,

)

from pipeline.i18n import t as tr





def select_and_analyze_sources(

    brief: ResearchBrief,

    candidates: list[ResearchSource],

    *,

    log_fn: Callable[[str], None] | None = None,

) -> list[ResearchSource]:

    """Rank by credibility/relevance, keep top N, extract key points per source."""

    target = max(3, min(15, int(brief.source_count or 6)))

    if not candidates:

        return []



    if log_fn:

        log_fn(tr("research.progress.assessing", count=len(candidates)))

    scored = research_llm.assess_sources_credibility(brief, candidates, log_fn=log_fn)



    scored.sort(

        key=lambda s: (0.55 * s.credibility_score + 0.45 * s.relevance_score),

        reverse=True,

    )

    selected = scored[:target]

    for src in selected:

        src.selected = True

    assign_source_ids(selected)



    if log_fn:

        log_fn(tr("research.progress.selected", count=len(selected)))

        for src in selected:

            log_fn(

                tr(

                    "research.progress.source_score",

                    id=src.source_id,

                    cred=f"{src.credibility_score:.2f}",

                    rel=f"{src.relevance_score:.2f}",

                    title=src.title[:70],

                )

            )



    for i, src in enumerate(selected):

        if log_fn:

            log_fn(

                tr(

                    "research.progress.scanning",

                    id=src.source_id,

                    title=src.title[:80],

                )

            )

        points = research_llm.extract_source_insights(brief, src, log_fn=log_fn)

        src.key_points = points



    return selected





def build_notes_and_report(

    brief: ResearchBrief,

    sources: list[ResearchSource],

    *,

    log_fn: Callable[[str], None] | None = None,

) -> tuple[str, str]:

    """Comparative synthesis notes + final deliverable with references."""

    if log_fn:

        log_fn(tr("research.progress.comparing"))

    notes = research_llm.synthesize_comparative_notes(brief, sources, log_fn=log_fn)



    if log_fn:

        log_fn(tr("research.progress.writing", format=brief.output_format.lower()))

    deliverable = research_llm.author_deliverable(

        brief,

        notes,

        sources=sources,

        log_fn=log_fn,

    )

    deliverable = append_references(deliverable, sources)

    return notes, deliverable





def candidate_from_hit(hit: dict[str, str], text: str) -> ResearchSource:

    return hit_to_source(hit, text)





def candidate_from_upload(name: str, text: str) -> ResearchSource:

    return upload_to_source(name, text)


