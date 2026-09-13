# -*- coding: utf-8 -*-
"""LLM brief generation for News Brief."""
from __future__ import annotations

import re
from typing import Any, Callable

from services.session.chat_post import set_assistant_message


def format_references(sources: list[dict[str, Any]]) -> str:
    from pipeline.i18n import t as tr

    if not sources:
        return ""
    lines = [tr("news_brief.section_references"), ""]
    for i, src in enumerate(sources[:12], start=1):
        title = (src.get("title") or "Source").strip()
        url = (src.get("url") or "").strip()
        # Escape the dot so the number renders as literal "1." text — visible even if the
        # theme hides <ol> list markers — and end with a hard break so each ref is its own line.
        entry = f"{i}\\. [{title}]({url})" if url else f"{i}\\. {title}"
        lines.append(entry + "  ")
    return "\n".join(lines)


def _strip_trailing_references(body: str) -> str:
    from pipeline.i18n import t as tr

    headers = {
        tr("news_brief.section_references").lstrip("#").strip().lower(),
        "## references",
        "## 參考來源",
        "## 参考来源",
    }
    lines = body.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip().lower()
        if stripped.startswith("##") and any(h in stripped for h in headers):
            cut = i
            break
    return "\n".join(lines[:cut]).rstrip()


def _dedent_bullets(body: str) -> str:
    out: list[str] = []
    for line in body.splitlines():
        if re.match(r"^[\t ]+[-*]", line):
            line = re.sub(r"^[\t ]+", "", line)
        out.append(line)
    return "\n".join(out)


def _strip_hallucinated_citations(body: str, source_count: int) -> str:
    if source_count <= 0:
        return body

    def _repl(match: re.Match) -> str:
        nums = [int(n) for n in match.group(1).split(",") if n.strip().isdigit()]
        valid = [str(n) for n in nums if 1 <= n <= source_count]
        return f"[{','.join(valid)}]" if valid else ""

    return re.sub(r"\[(\d+(?:,\s*\d+)*)\]", _repl, body)


def _renumber_and_filter(
    body: str, sources: list[dict[str, Any]]
) -> tuple[str, list[dict[str, Any]]]:
    """Keep only the sources actually cited in the body and renumber them 1..N in
    first-appearance order, rewriting inline [n] citations to match. Fixes the confusing
    'listed 8 references but only 4 were used' mismatch. If nothing was cited, keep all."""
    order: list[int] = []
    for m in re.finditer(r"\[(\d+(?:,\s*\d+)*)\]", body):
        for part in m.group(1).split(","):
            part = part.strip()
            if part.isdigit():
                k = int(part)
                if 1 <= k <= len(sources) and k not in order:
                    order.append(k)
    if not order:
        return body, sources[:8]

    remap = {old: new for new, old in enumerate(order, start=1)}

    def _repl(match: re.Match) -> str:
        new_nums = [
            str(remap[int(n)])
            for n in match.group(1).split(",")
            if n.strip().isdigit() and int(n) in remap
        ]
        return f"[{','.join(new_nums)}]" if new_nums else ""

    new_body = re.sub(r"\[(\d+(?:,\s*\d+)*)\]", _repl, body)
    cited_sources = [sources[old - 1] for old in order]
    return new_body, cited_sources


def compose_brief(
    *,
    categories: list[str],
    countries: list[str],
    topics: list[str],
    sources: list[dict[str, Any]],
    timeline: str = "week",
    log_fn: Callable[[str], None] | None = None,
    stream: bool = True,
    should_abort: Callable[[], bool] | None = None,
) -> str:
    from extensions.news_brief.queries import NEWS_CATEGORIES, NEWS_COUNTRIES, TIMELINE_OPTIONS
    from extensions.news_brief.search import NewsBriefAborted
    from pipeline.capability_runtime.chat_runner import generate_text_sync
    from pipeline.i18n import t as tr
    from pipeline.base.profile_pack import load_profile, resolve_active_profile_id
    from services.model_router import resolve_general_model
    from services.llm_bridge import build_chat_request, chat as llm_chat
    from services.resource_governor import ResourceGovernor
    from services.session import state

    profile_id = resolve_active_profile_id((state.current_settings or {}).get("active_profile"))
    prof = load_profile(profile_id) or {}
    model = resolve_general_model(prof)

    cat_labels = [NEWS_CATEGORIES.get(c, c) for c in categories]
    country_labels = [NEWS_COUNTRIES.get(c, c) for c in countries]
    topic_text = ", ".join(topics) if topics else "general headlines"
    time_label = TIMELINE_OPTIONS.get(timeline or "week", timeline or "Past week")

    used_sources = sources[:8]
    blocks = []
    for i, src in enumerate(used_sources, start=1):
        title = (src.get("title") or "").strip()
        url = (src.get("url") or "").strip()
        text = (src.get("text") or "").strip()[:2500]
        blocks.append(f"### Source {i}: {title}\nURL: {url}\n{text}")

    system = (
        "You are LOMA's news editor. Write a concise news brief in markdown.\n"
        "STRICT RULES:\n"
        "- Use ONLY facts explicitly stated in the numbered sources below.\n"
        "- Do NOT invent events, quotes, names, dates, statistics, or URLs.\n"
        "- Every bullet MUST cite one or more source numbers inline like [1] or [1,2].\n"
        "- If the sources do not cover a topic, omit it — do not speculate.\n"
        "- Do NOT include a References section — references are appended separately.\n"
        "- Left-align all bullets — no indentation under section headings.\n"
        "Sections:\n"
        f"{tr('news_brief.section_headlines')}\n"
        f"{tr('news_brief.section_developments')}\n"
        f"{tr('news_brief.section_watch')}\n"
        "Keep it scannable — bullet points, 400–700 words max."
    )
    user = (
        f"Time window: {time_label}\n"
        f"Categories: {', '.join(cat_labels) or 'General'}\n"
        f"Regions: {', '.join(country_labels) or 'Global'}\n"
        f"Topics: {topic_text}\n\n"
        f"SOURCES (only use these {len(used_sources)} sources)\n\n" + "\n\n".join(blocks)
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    if log_fn:
        log_fn(tr("news_brief.log_generating"))

    # Up to 8 full scraped articles can easily exceed the fixed Settings num_ctx default —
    # when that happens the backend silently drops/truncates earlier context instead of
    # erroring, and the model fills the gap for the missing source with plausible-sounding
    # but fabricated content instead of admitting it's missing. Size num_ctx to the actual
    # prompt, same as the Web Viewer highlight path and batch_processor already do.
    from pipeline.direct.batch_budget import fit_budget_to_prompt, llm_extra_options, resolve_batch_budget

    budget = resolve_batch_budget(prof, model)
    budget = fit_budget_to_prompt(budget, len(system) + len(user), profile=prof)
    extra_options = {**llm_extra_options(budget), "temperature": 0.15}

    body = ""
    if stream:
        set_assistant_message(tr("news_brief.generating_placeholder"))
        from pipeline.i18n import apply_locale_to_messages

        messages = apply_locale_to_messages(messages)
        kwargs, _ = build_chat_request(
            prof,
            model=model,
            messages=messages,
            stream=True,
            extra_options=extra_options,
        )
        acc = ""
        with ResourceGovernor.acquire("llm_chat"):
            stream_resp = llm_chat(**kwargs)
            for chunk in stream_resp:
                if should_abort and should_abort():
                    raise NewsBriefAborted()
                msg = chunk.get("message") or {}
                token = msg.get("content") or chunk.get("response") or ""
                if token:
                    acc += token
                    set_assistant_message(acc)
        body = acc.strip()
    else:
        body = generate_text_sync(
            prof,
            model,
            messages,
            extra_options=extra_options,
        ).strip()

    body = _strip_hallucinated_citations(body, len(used_sources))
    body = _strip_trailing_references(body)
    body = _dedent_bullets(body)
    body, cited_sources = _renumber_and_filter(body, used_sources)
    refs = format_references(cited_sources)
    final = f"{body}\n\n{refs}" if refs else body
    set_assistant_message(final)
    return final
