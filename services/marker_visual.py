# -*- coding: utf-8 -*-
"""Shared classify-and-dispatch helper for `[IMAGE_PROMPT: ...]` markers embedded in
generated documents/presentations. A marker's own free-text description decides
whether it becomes a plain diffusion photo, a flowchart, or an infographic — the same
classification `pipeline/direct/image_intent.py::classify_image_request()` already
uses for standalone chat image requests, so the authoring LLM's own phrasing (see
pipeline/base/profile_pack.py::_image_marker_instruction()) is what drives the split,
not a separate marker syntax.

Also resolves grounding context (pipeline/base/grounding.py) before dispatching to a
diagram/infographic author, so their stated facts come from an attached source or a
web search when one is available, rather than the model's unverified guess.

Used by both services/artifact_build.py (docx/pptx fallback compilers) and
pipeline/deliverables/presentation_compile.py (themed/agentic presentation compiler)
so the classify-then-dispatch logic exists in exactly one place."""
from __future__ import annotations

import re

# A marker's photo fallback has no per-generation author step to keep it purely
# scenic (unlike diagram/infographic/chart, which are authored and rendered
# separately from diffusion) — a data-flavored description (e.g. "temperature
# trends over time") can still nudge SD toward attempting a fake chart-with-text
# even after classification correctly ruled out the real chart renderer. Suppress
# that specific failure mode the same way poster_generation.py already does for
# poster backgrounds.
_MARKER_PHOTO_TEXT_SUPPRESSION = (
    "text, words, letters, numbers, typography, writing, caption, chart, graph, "
    "diagram, axis labels, watermark, signage, subtitles, infographic, statistics, "
    "percentages, checklist, table, spreadsheet, data visualization, illegible text"
)

# A negative prompt (above) is a weak deterrent against a strong positive instruction —
# if a marker's own description explicitly asks for a quoted headline/caption to be
# rendered in the image (it shouldn't: see pipeline/direct/task_roles.py's deck_planner
# prompt, which now forbids this — but authoring LLMs don't always follow instructions),
# strip that clause outright before it reaches the diffusion model. Every diffusion
# model renders arbitrary requested text as garbled nonsense at any real length,
# regardless of model quality — this isn't a "weaker models only" problem.
#
# Double/curly quotes only (not a bare apostrophe ' — that would eat whole sentences
# between two unrelated contractions, e.g. "a woman's smile ... today's run"). Finds
# each quoted span first, then expands outward for a nearby trigger word (headline/
# title/...) and trailing modifier (overlaid/reading/...) *anywhere within a short
# window*, not just immediately adjacent — natural phrasing puts descriptive words
# between them (e.g. "bold motivational title text at the top reading "X"") often
# enough that requiring adjacency missed real cases.
_QUOTED_SPAN_RE = re.compile(r'["“][^"”]{1,200}["”]')
_TEXT_TRIGGER_RE = re.compile(r"\b(?:headline|title|caption|text|words?|banner|sign|logo|label)\b", re.IGNORECASE)
_TEXT_TRAILING_RE = re.compile(
    r"^\s*(?:overlaid|overlay|written|displayed|shown|in\s+\w+(?:\s+\w+){0,3}\s+text)",
    re.IGNORECASE,
)
_LOOKBACK_CHARS = 60
_LOOKAHEAD_CHARS = 40


def _strip_text_in_image_instructions(prompt: str) -> str:
    text = prompt
    for match in list(_QUOTED_SPAN_RE.finditer(text))[::-1]:
        start, end = match.start(), match.end()
        window_start = max(0, start - _LOOKBACK_CHARS)
        trigger = _TEXT_TRIGGER_RE.search(text[window_start:start])
        seg_start = window_start + trigger.start() if trigger else start
        trailing = _TEXT_TRAILING_RE.match(text[end:end + _LOOKAHEAD_CHARS])
        seg_end = end + trailing.end() if trailing else end
        text = text[:seg_start] + text[seg_end:]
    return re.sub(r"\s{2,}", " ", text).strip(" ,.")


def resolve_marker_visual(
    prompt: str,
    *,
    output_path: str,
    prof: dict | None,
    model: str | None,
    log_fn=None,
    photo_prompt: str | None = None,
    bundle=None,
    source_text: str = "",
    settings: dict | None = None,
    topic_text: str = "",
) -> tuple[str | None, list[dict[str, str]]]:
    """Returns (produced image path or None on failure, web sources used). The
    sources list is only ever non-empty when a chart/diagram/infographic was
    genuinely grounded in a real web search (see pipeline/base/grounding.py) —
    never for a plain photo or an attached-source excerpt (not an online
    citation) — so a caller can cite it directly without risking a fabricated
    reference. `prof`/`model` are required for diagram/infographic generation
    (they author structured content via an LLM call) — when either is missing,
    always falls back to a plain photo, so a caller that can't supply a profile
    keeps today's behavior unchanged.

    `prompt` drives classification and diagram/infographic authoring (it should be
    the marker's own free-text description). `photo_prompt`, if given, is used
    instead of `prompt` only for the plain-photo fallback — lets a caller pass a
    diffusion-tuned variant (e.g. with extra scene context) without that context
    leaking into diagram/infographic authoring, which expects a plain description.
    `topic_text`, if given, drives the WEB SEARCH specifically (falls back to
    `prompt` when empty) — a caller with more trustworthy surrounding text (e.g.
    the slide's own already-written title/bullets) should pass it, since a
    marker's own free-text description can invent a framing detail (a
    comparison axis, an extra qualifier) that isn't actually in the slide's
    content, which then dominates the search and grounds the visual in
    something the slide was never about (confirmed via a real run: a "recovery
    strategies" slide's image marker invented "vs. traditional emergency
    vehicles" as a comparison axis, and the resulting chart ended up about
    firetrucks instead of the slide's actual green-infrastructure content).

    `bundle`/`settings`, if given, are used to resolve grounding context (an
    attached-source excerpt, or a web search if grounding is enabled) before
    authoring a diagram/infographic — see pipeline/base/grounding.py."""
    prompt = (prompt or "").strip()
    if not prompt:
        return None, []

    if prof and model:
        try:
            from pipeline.direct.image_intent import classify_image_request

            intent = classify_image_request(prompt)
        except Exception as ex:
            if log_fn:
                log_fn(f"Marker visual classification failed, using photo: {ex}")
            intent = "photo"

        # "poster" always renders as a plain photo here — a poster's text-overlay
        # pipeline needs a background prompt shaped differently than a document
        # image marker, so embedded markers render posters as plain photos rather
        # than attempting text overlay on a description that wasn't authored for
        # it. "photo" (classify_image_request's own broad-keyword net already
        # ruled out chart/diagram/infographic wording — see
        # pipeline/query_intent_i18n.py's *_broad_keywords concepts) also falls
        # straight through, unchanged.
        if intent in ("diagram", "infographic_stat", "infographic_timeline", "infographic_comparison", "chart"):
            broad_trigger = intent != "diagram"
            context, sources = _resolve_context(
                (topic_text or prompt).strip(), bundle, source_text, settings,
                broad_trigger=broad_trigger, log_fn=log_fn,
            )
            if intent == "diagram":
                path = _try_diagram(prompt, output_path, prof, model, log_fn, context)
            elif intent == "chart":
                path = _try_chart(prompt, output_path, prof, model, log_fn, context, sources=sources)
            else:
                kind = intent.split("_", 1)[1]
                path = _try_infographic(prompt, output_path, prof, model, log_fn, context, kind=kind)
            if path:
                return path, sources
            # Classification succeeded but the dedicated renderer itself failed
            # (author LLM error, malformed JSON, render exception — see the
            # _try_* functions' own except blocks) — visibly distinct from
            # "never classified as this type" so a real renderer regression
            # doesn't look identical to a routing miss when reading the log.
            if log_fn:
                log_fn(
                    f"Marker classified as {intent!r} but that renderer failed — "
                    "falling back to a plain photo (this will not have real chart/"
                    "diagram/infographic content)."
                )

    return _try_photo(photo_prompt or prompt, output_path, log_fn), []


def _resolve_context(
    prompt: str, bundle, source_text: str, settings: dict | None, *, broad_trigger: bool, log_fn
) -> tuple[str, list[dict[str, str]]]:
    try:
        from pipeline.base.grounding import resolve_generation_context

        return resolve_generation_context(
            prompt, bundle=bundle, source_text=source_text, settings=settings,
            broad_trigger=broad_trigger, log_fn=log_fn,
        )
    except Exception as ex:
        if log_fn:
            log_fn(f"Grounding resolution failed, proceeding without it: {ex}")
        return "", []


def _try_diagram(prompt: str, output_path: str, prof: dict, model: str, log_fn, context: str = "") -> str | None:
    try:
        from services.diagram_generation import generate_diagram

        result, note = generate_diagram(prompt, prof=prof, model=model, output_path=output_path, context=context)
        if note and log_fn:
            log_fn(f"Diagram note: {note}")
        if result.path and _is_valid_png(result.path):
            return result.path
    except Exception as ex:
        if log_fn:
            log_fn(f"Diagram generation for embedded marker failed, falling back to photo: {ex}")
    return None


def _try_infographic(
    prompt: str, output_path: str, prof: dict, model: str, log_fn, context: str = "", *, kind: str,
) -> str | None:
    try:
        if kind == "stat":
            from services.infographic_generation import generate_stat_grid as generate_fn
        elif kind == "comparison":
            from services.infographic_generation import generate_comparison as generate_fn
        else:
            from services.infographic_generation import generate_timeline as generate_fn

        result = generate_fn(prompt, prof=prof, model=model, output_path=output_path, context=context)
        if kind == "stat" and log_fn:
            # Only the stat-grid layout draws from the fixed built-in icon set.
            from pipeline.i18n import t as tr

            log_fn(f"Infographic note: {tr('infographic.limitation_icons')}")
        if result.path and _is_valid_png(result.path):
            return result.path
    except Exception as ex:
        if log_fn:
            log_fn(f"Infographic generation for embedded marker failed, falling back to photo: {ex}")
    return None


def _try_chart(
    prompt: str, output_path: str, prof: dict, model: str, log_fn, context: str = "",
    sources: list[dict[str, str]] | None = None,
) -> str | None:
    try:
        from services.chart_generation import generate_chart

        result = generate_chart(
            prompt, prof=prof, model=model, output_path=output_path, context=context, sources=sources,
        )
        if result.path and _is_valid_png(result.path):
            return result.path
    except Exception as ex:
        if log_fn:
            log_fn(f"Chart generation for embedded marker failed, falling back to photo: {ex}")
    return None


def _try_photo(prompt: str, output_path: str, log_fn) -> str | None:
    try:
        from services.image_generation import generate_image_verified
        from services.model_router import image_generation_deps_available

        deps_ok, _ = image_generation_deps_available()
        if not deps_ok:
            return None
        # This prompt comes straight from the deck-authoring LLM's own [IMAGE:]/
        # [IMAGE_PROMPT:] marker text — unlike the standalone chat "generate an
        # image" path (pipeline/direct/step_executor.py's _run_image_generation),
        # it never goes through image_prompt_author's schema-constrained authoring
        # step, so it has none of that path's explicit-content protection. Confirmed
        # a real miss: a slide about self-reflection/meditation authored an image
        # description that rendered a topless figure, with nothing in the check
        # catching it because this path had no check at all. Skipping the visual
        # (returning None) is the right outcome here specifically — unlike the
        # standalone chat path, a slide can go without an image entirely (see
        # presentation_compile.py's own image_desc handling).
        from pipeline.image_safety_embeddings import is_explicit_prompt

        if is_explicit_prompt(prompt):
            if log_fn:
                log_fn("Skipped this slide's image — the description reads as explicit content.")
            return None
        cleaned_prompt = _strip_text_in_image_instructions(prompt)
        if cleaned_prompt != prompt and log_fn:
            log_fn("Removed a text-in-image instruction from the marker's description before generating.")
        # max_reseeds=1: one retry with a fresh seed if the text detector flags the
        # first attempt. If it's still garbled after that, try one inpaint-based
        # mutation (erase-and-repaint just the flagged region) before giving up — a
        # document/slide can skip the visual entirely (see presentation_compile.py),
        # so discarding remains the final fallback, unlike the standalone chat path
        # (services.image_generation.generate_image_verified's docstring), which goes
        # straight to mutation after two reseeds since it can't leave the user with
        # nothing.
        result, still_has_text = generate_image_verified(
            cleaned_prompt or prompt,
            output_path=output_path,
            negative_prompt=_MARKER_PHOTO_TEXT_SUPPRESSION,
            max_reseeds=1,
        )
        if not result.fallback and still_has_text and result.path and _is_valid_png(result.path):
            from services.image_generation import mutate_remove_text
            from services.image_text_detection import has_rendered_text

            if log_fn:
                log_fn("Still garbled after a reseed retry — repainting the affected region…")
            mutated = mutate_remove_text(result.path, cleaned_prompt or prompt, model_id=result.model_id)
            if not mutated.fallback:
                try:
                    from PIL import Image

                    with Image.open(mutated.path) as img:
                        still_has_text = has_rendered_text(img)
                except Exception:
                    still_has_text = False
                result = mutated
        if result.fallback or still_has_text:
            if still_has_text and log_fn:
                log_fn("Discarding this image — still contained garbled text after reseed and repaint attempts.")
            return None
        if result.path and _is_valid_png(result.path):
            return result.path
    except Exception as ex:
        if log_fn:
            log_fn(f"Photo generation for embedded marker failed: {ex}")
    return None


def _is_valid_png(path: str) -> bool:
    import os

    return bool(path) and path.lower().endswith(".png") and os.path.exists(path)
