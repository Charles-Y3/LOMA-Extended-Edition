# -*- coding: utf-8 -*-
"""LLM helpers for the Research extension (services only — no capability/direct)."""
from __future__ import annotations

import json
from typing import Any, Callable

from extensions.research.clarify_i18n import get_clarify_template
from extensions.research.brief import ResearchBrief, _strip_json_fences
from extensions.research.sources import ResearchSource


def _profile_and_model() -> tuple[dict, str]:
    from pipeline.base.profile_pack import load_profile
    from services.model_router import resolve_general_model
    from services.session import state

    profile_id = (state.current_settings or {}).get("active_profile", "none")
    prof = load_profile(profile_id) or {}
    model = resolve_general_model(prof)
    return prof, model


def _llm_extra_options(prompt_chars: int = 0) -> dict[str, int]:
    from pipeline.direct.batch_budget import fit_budget_to_prompt, llm_extra_options, resolve_batch_budget

    prof, model = _profile_and_model()
    budget = resolve_batch_budget(prof, model)
    if prompt_chars > 0:
        # Comparative notes synthesized from several sources (and the report authored from
        # them) can exceed the latency-friendly default budget — size num_ctx to what this
        # call is actually sending, same as the Web Viewer/News Brief long-content paths.
        budget = fit_budget_to_prompt(budget, prompt_chars, profile=prof)
    return llm_extra_options(budget)


def _chat(
    messages: list[dict[str, str]],
    *,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    from pipeline.capability_runtime.chat_runner import generate_text_sync
    from pipeline.state_machine import extension_processing

    prof, model = _profile_and_model()
    prompt_chars = sum(len(m.get("content") or "") for m in messages)
    extra = _llm_extra_options(prompt_chars)
    sink = None
    if log_fn:
        from services.console_i18n import translate_console_line

        class _Sink:
            def log(self, msg: str) -> None:
                log_fn(translate_console_line(msg))

        sink = _Sink()
    with extension_processing():
        return generate_text_sync(
            prof,
            model,
            messages,
            disable_thinking=True,
            sink=sink,
            extra_options=extra,
        )


def generate_clarify_plan(user_prompt: str, *, log_fn: Callable[[str], None] | None = None) -> str:
    dims = "\n".join(
        f"- {t['id']}: {t['question']}"
        + (f" (options: {', '.join(t['options'])})" if t.get("options") else "")
        for t in get_clarify_template()
    )
    system = (
        "Role: Research intake planner.\n"
        "Given the user's research request, propose clarifying answers for each dimension.\n"
        "For source_count, pick a sensible default (usually 5–8) from the options.\n"
        "Return ONLY valid JSON:\n"
        '{"topic":"...", "source_count":"6 sources", '
        '"questions":[{"id":"audience","question":"...","suggested_answer":"..."}, ...]}\n'
        "Use the template ids exactly. Be specific and practical."
    )
    user = f"User request:\n{user_prompt.strip()}\n\nTemplate dimensions:\n{dims}"
    if log_fn:
        log_fn("Drafting clarify plan…")
    return _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_fn=log_fn,
    )


def plan_search_queries(brief: ResearchBrief, *, log_fn: Callable[[str], None] | None = None) -> list[str]:
    n = brief.source_count or 6
    system = (
        f"Return ONLY a JSON array of 3–6 concise web search query strings "
        f"to find diverse, credible sources for the research topic. "
        f"Target collecting ~{n} quality sources. No markdown."
    )
    user = json.dumps(brief.to_dict(), ensure_ascii=False)
    raw = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_fn=log_fn,
    )
    cleaned = _strip_json_fences(raw)
    try:
        data = json.loads(cleaned)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()][:6]
    except json.JSONDecodeError:
        pass
    return brief.search_queries


def assess_sources_credibility(
    brief: ResearchBrief,
    sources: list[ResearchSource],
    *,
    log_fn: Callable[[str], None] | None = None,
) -> list[ResearchSource]:
    """LLM relevance scores merged with heuristic credibility."""
    if not sources:
        return []

    catalog = []
    for i, src in enumerate(sources[:20], start=1):
        catalog.append(
            {
                "index": i,
                "title": src.title,
                "url": src.url or "(upload)",
                "domain": src.domain,
                "heuristic_credibility": round(src.credibility_score, 2),
                "heuristic_note": src.credibility_note,
                "snippet": src.snippet(350),
            }
        )

    system = (
        "Role: Research librarian.\n"
        "Score each candidate source for relevance to the topic (0.0–1.0) "
        "and adjust credibility (0.0–1.0) considering authority, bias, and recency.\n"
        "Prefer .edu/.gov, peer-reviewed, established publishers; downrank SEO spam.\n"
        "Return ONLY JSON: {\"sources\":[{\"index\":1,\"relevance\":0.8,\"credibility\":0.85,"
        "\"note\":\"short reason\"}, ...]}"
    )
    user = (
        f"Topic: {brief.topic}\nAudience: {brief.audience}\n"
        f"Target sources: {brief.source_count}\n\nCandidates:\n"
        f"{json.dumps(catalog, ensure_ascii=False)}"
    )
    raw = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_fn=log_fn,
    )
    cleaned = _strip_json_fences(raw)
    scores: dict[int, dict[str, Any]] = {}
    try:
        data = json.loads(cleaned)
        for row in data.get("sources") or []:
            if not isinstance(row, dict):
                continue
            idx = int(row.get("index") or 0)
            scores[idx] = row
    except (json.JSONDecodeError, TypeError, ValueError):
        pass

    out: list[ResearchSource] = []
    for i, src in enumerate(sources[:20], start=1):
        row = scores.get(i) or {}
        rel = float(row.get("relevance") or 0.55)
        cred = float(row.get("credibility") or src.credibility_score)
        src.relevance_score = max(0.0, min(1.0, rel))
        src.credibility_score = max(0.0, min(1.0, 0.4 * src.credibility_score + 0.6 * cred))
        if row.get("note"):
            src.credibility_note = str(row["note"]).strip()
        out.append(src)
    return out


def extract_source_insights(
    brief: ResearchBrief,
    source: ResearchSource,
    *,
    log_fn: Callable[[str], None] | None = None,
) -> list[str]:
    """Map step: scan one source for topic-relevant facts."""
    body = (source.text or "").strip()
    if not body:
        return []

    from pipeline.direct.batch_budget import resolve_batch_budget, single_pass_char_limit

    prof, model = _profile_and_model()
    budget = resolve_batch_budget(prof, model)
    limit = single_pass_char_limit(budget)
    if len(body) > limit:
        body = body[:limit] + "\n\n[…truncated for context budget; scan the opening sections…]"

    system = (
        "Role: Research analyst.\n"
        "Read the source and extract 4–8 bullet facts relevant to the research topic.\n"
        "Note agreements, unique claims, and limitations. Use [quote] sparingly for key phrases.\n"
        "Return markdown bullets only (lines starting with '- ')."
    )
    user = (
        f"Topic: {brief.topic}\nSource: {source.title}\nURL: {source.url or 'upload'}\n\n"
        f"Text:\n{body}"
    )
    raw = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_fn=log_fn,
    )
    points = [
        ln[2:].strip()
        for ln in (raw or "").splitlines()
        if ln.strip().startswith("- ")
    ]
    source.key_points = points[:10]
    return source.key_points


def synthesize_comparative_notes(
    brief: ResearchBrief,
    sources: list[ResearchSource],
    *,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    """Reduce step: compare sources, note consensus and conflicts."""
    blocks: list[str] = []
    for src in sources:
        bullets = "\n".join(f"- {p}" for p in (src.key_points or [])) or "- (no extract)"
        blocks.append(
            f"### [{src.source_id}] {src.title}\n"
            f"URL: {src.url or 'upload'}\n"
            f"Credibility: {src.credibility_score:.2f} — {src.credibility_note}\n"
            f"{bullets}"
        )
    joined = "\n\n".join(blocks)

    from pipeline.direct.batch_budget import resolve_batch_budget, single_pass_char_limit
    from pipeline.direct.batch_processor import _hard_split_chars, _split_paragraphs

    prof, model = _profile_and_model()
    budget = resolve_batch_budget(prof, model)
    chunk_limit = single_pass_char_limit(budget)

    system = (
        "Role: Senior research synthesizer.\n"
        "Compare sources: consensus, contradictions, evidence gaps, strongest claims.\n"
        "Organize themed notes with inline citations like [S1], [S2].\n"
        "Flag when only one weak source supports a claim. No final report yet."
    )

    if len(joined) <= chunk_limit:
        user = (
            f"Topic: {brief.topic}\nAudience: {brief.audience}\n"
            f"Constraints: {brief.constraints or 'none'}\n\nPer-source extracts:\n{joined}"
        )
        return _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            log_fn=log_fn,
        )

    if log_fn:
        log_fn(f"Large source corpus ({len(joined):,} chars) — map-reduce synthesis…")
    chunks = _split_paragraphs(joined, chunk_limit, max_paragraphs=20)
    if len(chunks) <= 1:
        chunks = _hard_split_chars(joined, chunk_limit)

    partials: list[str] = []
    for i, chunk in enumerate(chunks):
        if log_fn:
            log_fn(f"  synthesis batch {i + 1}/{len(chunks)}")
        user = (
            f"Topic: {brief.topic}\nBatch {i + 1}/{len(chunks)} — partial source extracts:\n{chunk}"
        )
        part = _chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            log_fn=log_fn,
        )
        if part.strip():
            partials.append(part.strip())

    merge_user = (
        f"Topic: {brief.topic}\n\nMerge these partial synthesis notes into one comparative outline. "
        f"Preserve [S#] citations.\n\n" + "\n\n---\n\n".join(partials)
    )
    return _chat(
        [
            {"role": "system", "content": system + "\nMerge partial notes; deduplicate."},
            {"role": "user", "content": merge_user},
        ],
        log_fn=log_fn,
    )


def _cut_repeated_tail(text: str) -> str:
    """Small local models occasionally degenerate into repeating an entire paragraph
    verbatim dozens of times once they've said everything meaningful but generation
    hasn't hit its stop condition yet. A legitimate report never repeats a full
    paragraph (200+ chars) word-for-word, so once one is seen twice, cut there."""
    paras = text.split("\n\n")
    seen: set[str] = set()
    for i, para in enumerate(paras):
        key = para.strip()
        if len(key) < 200:
            continue
        if key in seen:
            return "\n\n".join(paras[:i]).rstrip()
        seen.add(key)
    return text


def author_deliverable(
    brief: ResearchBrief,
    notes: str,
    *,
    sources: list[ResearchSource] | None = None,
    output_format: str | None = None,
    tone: str | None = None,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    fmt = (output_format or brief.output_format).strip()
    tone_v = (tone or brief.tone).strip()
    depth = brief.depth
    rules = {
        "Executive summary": "400–700 words, lead with key findings and recommendations.",
        "Full report": "Structured report with ## sections, 1200–2500 words.",
        "Bullet brief": "Scannable bullets grouped by theme, max ~40 bullets.",
    }
    cite_rules = (
        "Cite sources inline as [S1], [S2], etc. matching the source list. "
        "Do NOT add a References, Sources, or Bibliography section of any kind — no "
        "heading, no bullet list, no '**Sources:**' line — the source list is appended "
        "automatically after your text. End your response after the body content."
    )
    system = (
        f"Role: Research author.\n"
        f"Tone: {tone_v}. Depth: {depth}.\n"
        f"Format: {fmt}. {rules.get(fmt, '')}\n"
        f"{cite_rules}\n"
        "Use ONLY facts, figures, and examples present in the comparative research notes "
        "below — never invent statistics, illustrative numbers, or claims (e.g. about laws, "
        "regulations, or events) that the notes don't state. If the notes don't cover "
        "something, omit it rather than speculating or extrapolating as if it were a finding.\n"
        "Output markdown only. No meta commentary about being an AI."
    )
    src_lines = ""
    if sources:
        src_lines = "\n".join(
            f"- {s.source_id}: {s.title} ({s.url or 'upload'})"
            for s in sources
            if s.selected
        )
    user = (
        f"Topic: {brief.topic}\nAudience: {brief.audience}\n"
        f"Constraints: {brief.constraints or 'none'}\n"
        f"Sources used:\n{src_lines or 'none'}\n\nComparative research notes:\n{notes}"
    )
    if log_fn:
        log_fn(f"Writing {fmt.lower()}…")
    body = _chat(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        log_fn=log_fn,
    )
    return _cut_repeated_tail(body)
