# -*- coding: utf-8 -*-
"""Translate console log lines for the active UI locale, and classify each line
as a user-facing milestone ("simple") or an internal/technical detail.

The full log is always kept in memory (state.orchestra_log) regardless of the
console_detail setting — nothing is ever dropped for debugging. This module only
decides what the Console tab *renders*: "Simple" mode shows just the milestones a
non-technical user cares about (progress, results, errors); "Detailed" mode shows
every line, same as before this existed. Switching the setting re-renders from the
same underlying log, so flipping to Detailed reveals full history retroactively.
"""
from __future__ import annotations

import re

from pipeline.i18n import get_locale, t

# (regex, i18n key, group names for format, simple)
# simple=True  -> shown in both Simple and Detailed console modes (a milestone).
# simple=False -> shown only in Detailed mode (internal/technical detail).
_PATTERNS: list[tuple[re.Pattern[str], str, tuple[str, ...], bool]] = [
    (re.compile(r"^System initialized\.\.\.$"), "console.system_init", (), True),
    (re.compile(r"^Awaiting User Input$"), "console.awaiting_input", (), True),
    (re.compile(r"^Awaiting user input$"), "console.awaiting_input", (), True),
    (re.compile(r"^Context memory cleared\.$"), "console.context_cleared", (), True),
    (re.compile(r"^Workflow stopped before execution\.$"), "console.workflow_stopped", (), True),
    (re.compile(r"^Grounded chat \(web search\)$"), "console.grounded_chat", (), True),
    (re.compile(r"^Using cached web context \(no re-scrape\)\.$"), "console.web_cache", (), False),
    (re.compile(r"^Compiling artifact from draft…$"), "console.compiling", (), True),
    (re.compile(r"^Saving (\w+) from Preview \(([\d,]+) chars\)…$"), "console.saving_preview", ("type", "chars"), True),
    (re.compile(r"^Synthesizing (\w+) from markdown draft…$"), "console.synthesizing", ("type",), True),
    (re.compile(r"^Saved → data/generated/(.+)$"), "console.saved_to", ("name",), True),
    (re.compile(r"^No profile selected — using base defaults$"), "console.no_profile", (), True),
    (re.compile(r"^Context bundle built in (\d+)ms$"), "console.bundle_built", ("ms",), False),
    (re.compile(r"^Query with (\d+) file\(s\), (\d+) web link\(s\)$"), "console.query_context", ("files", "links"), False),
    (re.compile(r"^Web context added: (.+)$"), "console.web_added", ("url",), True),
    (re.compile(r"^Fetching: (.+)$"), "console.fetching", ("url",), False),
    (re.compile(r"^Loaded profile '(.+)'$"), "console.profile_loaded", ("id",), True),
    (re.compile(r"^Profile '(.+)' unavailable — using defaults$"), "console.profile_missing", ("id",), True),
    (re.compile(r"^Web grounding skipped: (.+)$"), "console.grounding_skipped", ("reason",), True),
    (re.compile(r"^Graph service: (\d+) chart\(s\) generated$"), "console.charts_generated", ("count",), True),
    (re.compile(r"^Artifact ready → data/generated/(.+)$"), "console.artifact_ready", ("name",), True),
    (re.compile(r"^Draft saved \(([\d,]+) chars\) — compile failed; edit in Preview and use Download\.$"), "console.draft_saved", ("chars",), True),
    (re.compile(r"^Workflow Error: (.+)$"), "console.workflow_error", ("error",), True),
    (re.compile(r"^Upload retention: purged (\d+) stale file\(s\) from data/uploads\.$"), "console.upload_purge", ("count",), False),
    (re.compile(r"^Express lane: (\d+) prior turn\(s\), ([\d,]+) prompt chars$"), "console.express_turns", ("turns", "chars"), False),
    (re.compile(r"^Express lane: model=(.+), ([\d,]+) prompt chars$"), "console.express_model", ("model", "chars"), False),
    (re.compile(r"^LLM stream complete$"), "console.llm_complete", (), False),
    (re.compile(r"^LLM request prep: ([\d.]+)ms(?: \(think probe [\d.]+ms\))?$"), "console.llm_request_prep", ("ms",), False),
    (re.compile(r"^LLM time-to-first-token: ([\d.]+)ms$"), "console.llm_ttft", ("ms",), False),
    (re.compile(r"^LLM stream complete: ([\d.]+)ms$"), "console.llm_stream_ms", ("ms",), True),
    (re.compile(r"^LLM response: ([\d.]+)ms$"), "console.llm_response_ms", ("ms",), False),
    (re.compile(r"^Generation stopped by user\.$"), "console.llm_stopped", (), True),
    (re.compile(r"^  · source (.+) \((.+)\)$"), "console.source_line", ("name", "kind"), False),
    (re.compile(r"^  · vision: (\d+) image\(s\) attached for model$"), "console.vision_images", ("count",), False),
    (re.compile(r"^Direct pipeline: grounded chat \(web\)$"), "console.direct_grounded", (), False),
    (re.compile(r"^Direct pipeline: express lane$"), "console.direct_express", (), False),
    (re.compile(r"^Direct plan: steps=(\d+) mode=(\w+) output=(\w+)$"), "console.direct_plan", ("steps", "mode", "output"), False),
    (re.compile(r"^  step (\d+): (.+) → (.+)$"), "console.direct_plan_step", ("index", "intent", "roles"), False),
    (re.compile(r"^Direct step (\d+)/(\d+): (.+)$"), "console.direct_step", ("index", "total", "roles"), True),
    (re.compile(r"^Planner worker: (.+)$"), "console.planner_worker", ("reason",), False),
    (re.compile(r"^Planner worker failed: (.+)$"), "console.planner_worker_failed", ("error",), True),
    (re.compile(r"^Planner worker: could not parse JSON$"), "console.planner_worker_no_json", (), False),
    (re.compile(r"^Planner worker unavailable; using fallback\.$"), "console.planner_worker_unavailable", (), False),
    (re.compile(r"^Plan: combined multi-source \((\d+) prep steps\)$"), "console.plan_combined_multi", ("count",), False),
    (re.compile(r"^Plan: document task → (\w+) \((\w+)\)$"), "console.plan_document_task", ("role", "mode"), False),
    (re.compile(r"^Plan: summarize \+ translate \((\d+) steps\)$"), "console.plan_summarize_translate", ("count",), False),
    (re.compile(r"^Plan: summarize then translate \(2 steps\)$"), "console.plan_summarize_then_translate", (), False),
    (re.compile(r"^Plan: multi-source summarize \((\d+) steps\)$"), "console.plan_multi_source_summarize", ("count",), False),
    (re.compile(r"^Plan: multi-source translate \((\d+) steps\)$"), "console.plan_multi_source_translate", ("count",), False),
    (re.compile(r"^Plan: per-source summarize \((\d+) steps\)$"), "console.plan_per_source_summarize", ("count",), False),
    (re.compile(r"^Plan: document task mode corrected (\w+) → (\w+)$"), "console.plan_mode_corrected", ("old", "new"), False),
    (re.compile(r"^Guardrail: mutation \(preview selection\)$"), "console.guardrail_mutation", (), False),
    (re.compile(r"^Guardrail: generation \(new presentation from sources\)$"), "console.guardrail_pres_sources", (), False),
    (re.compile(r"^Guardrail: generation \(presentation deliverable\)$"), "console.guardrail_pres_deliverable", (), False),
    (re.compile(r"^Guardrail: generation \(multi-source\)$"), "console.guardrail_multi_source", (), False),
    (re.compile(r"^Guardrail: generation \(whole-document translate/summarize\)$"), "console.guardrail_doc_transform", (), False),
    (re.compile(r"^System execution environments reset successfully\.$"), "console.system_env_reset", (), True),
    (re.compile(r"^Profile defaulted to none\.$"), "console.profile_defaulted", (), True),
    (re.compile(r"^File physically stored: (.+)$"), "console.file_stored", ("path",), False),
    (re.compile(r"^Context successfully populated: (.+)$"), "console.context_populated", ("name",), False),
    (re.compile(r"^Routing → output=(\w+) \| services=(.+)$"), "console.routing", ("output", "services"), False),
    (
        re.compile(r"^Context retrieval \((.+)\): selected passages from large sources$"),
        "console.context_retrieval",
        ("mode",),
        False,
    ),
    (
        re.compile(r"^Context strategy: (\w+) \| ([\d,]+) source chars \| budget ~([\d,]+)$"),
        "console.context_strategy",
        ("strategy", "chars", "budget"),
        False,
    ),
    (
        re.compile(r"^Sources: ([\d,]+) characters \(within model context budget\)$"),
        "console.sources_within_budget",
        ("chars",),
        False,
    ),
]

# Fallback for lines matching no pattern above (ad-hoc strings, new log call sites
# that haven't been catalogued yet): always show anything that looks like a failure,
# even in Simple mode — a user should never have an error silently swallowed.
_ERROR_LIKE_RE = re.compile(r"\b(error|failed|failure|⛔|exception)\b", re.IGNORECASE)


def translate_console_line(line: str, *, simple_mode: bool = False) -> str | None:
    """Return the display text for this log line, or None if it should be hidden
    in the current mode. simple_mode=False (Detailed) never hides anything."""
    text = (line or "").strip()
    if not text:
        return None
    for pattern, key, fields, simple in _PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        if simple_mode and not simple:
            return None
        if get_locale() == "en":
            return line
        if not fields:
            return t(key)
        return t(key, **{fields[i]: m.group(i + 1) for i in range(len(fields))})
    if simple_mode and not _ERROR_LIKE_RE.search(text):
        return None
    return line
