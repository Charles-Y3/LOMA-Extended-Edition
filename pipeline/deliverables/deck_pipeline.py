# -*- coding: utf-8 -*-
"""Structured document/web/topic → deck pipeline, replacing the old single
freeform "deck_planner" LLM call that was asked to simultaneously extract
content, decide slide count, write prose, and invent image prompts in one
shot with no verification anywhere in between.

Stages, each independently checkable instead of one big guess:
  1. resolve_source_material  — real attached source text, else a real web
     search, else "" (brainstorm mode — logged, not silent).
  2. extract_key_points       — chunked, structured JSON extraction (never
     one giant "summarize everything" call); a chunk that fails to parse
     falls back to its own paragraphs as key points, never to nothing.
  3. group_key_points_to_slides — deterministic bucketing to the target slide
     count. The count is enforced by construction, not by hoping a single
     LLM call both wrote content AND counted correctly.
  4. author_slide              — one authoring call per slide, each scoped to
     only its own key points, then mechanically verified (keyword overlap,
     including CJK) against that material; a drifted result gets one retry,
     then a mechanical fallback built directly from the source summaries —
     never a generic scaffold placeholder ("Practical tip 1 for X...").
  5. translate_image_prompts   — a non-English image description never
     reaches the (English-only) diffusion model untranslated; that mismatch
     was the actual cause of "always a generic portrait" for CJK decks.

Used for presentations regardless of source: an attached document, a web
search, or (fallback) the model's own general knowledge — same structure,
same verification, every time."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from pipeline.deliverables.presentation_deck import DeckSpec, SlideSpec
from pipeline.deliverables.presentation_limits import MAX_BULLETS_PER_SLIDE, MAX_SLIDES, MIN_SLIDES

LogFn = Callable[[str], None] | None


def _log(log_fn: LogFn, msg: str) -> None:
    if log_fn:
        log_fn(msg)


# ---------------------------------------------------------------------------
# Stage 1 — source material
# ---------------------------------------------------------------------------


def resolve_source_material(
    *, bundle: Any, query: str, settings: dict | None, log_fn: LogFn = None,
) -> tuple[str, bool]:
    """Returns (source_text, is_real_source). Priority: attached document(s)
    > a real web search > "" (brainstorm mode). Never silently substitutes
    one for another without logging which path was taken."""
    from pipeline.context.digest_store import join_digest_plain_text

    digests = getattr(bundle, "source_digests", None) or []
    plain = join_digest_plain_text(digests) if digests else ""
    unified = (getattr(bundle, "unified_text", None) or "").strip()
    text = (plain or unified).strip()
    if text:
        _log(log_fn, "Deck pipeline: using attached source document(s).")
        return text, True

    from pipeline.base.grounding import resolve_generation_context

    ctx, _sources = resolve_generation_context(
        query, settings=settings, broad_trigger=True, log_fn=log_fn,
    )
    if ctx.strip():
        _log(log_fn, "Deck pipeline: using web search material.")
        return ctx.strip(), True

    _log(log_fn, "Deck pipeline: no source or web material found — using general knowledge (ungrounded).")
    return "", False


# ---------------------------------------------------------------------------
# Stage 2 — key-point extraction
# ---------------------------------------------------------------------------


@dataclass
class KeyPoint:
    heading: str
    summary: str
    quote: str = ""


_MAX_CHUNK_CHARS = 2800
_MAX_CHUNKS = 8
_MAX_KEY_POINTS = 40

_EXTRACT_SYSTEM = """You are LOMA's Key Point Extractor. Return ONLY a valid JSON array (no markdown fences, no commentary).

Given a chunk of source material, extract the distinct, independent points it makes:
[
  {"heading": "short label for this point, in the SAME language as the material", "summary": "one to two sentences capturing this point, in the SAME language as the material", "quote": "a short supporting quote or citation from the material, if one exists, else empty string"}
]

Rules:
- 2 to 8 points per chunk, depending on how much distinct content is actually there.
- Every heading/summary must be DIRECTLY supported by the material below — never invent a point, a number, or a citation that isn't there.
- Keep summaries factual and concrete, not vague ("the author discusses various ideas" is not acceptable).
"""


def _parse_json_array(raw: str) -> list[Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, list) else None


def _chunk_text(text: str, *, max_chars: int = _MAX_CHUNK_CHARS, max_chunks: int = _MAX_CHUNKS) -> list[str]:
    """Paragraph-aware chunking — splits on blank lines first, then packs
    paragraphs into chunks up to max_chars, so a chunk boundary doesn't cut a
    sentence in half. Caps chunk count so a very large document still bounds
    the number of extraction LLM calls."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if not paragraphs:
        paragraphs = [text.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) > max_chars and current:
            chunks.append(current)
            current = para
        else:
            current = candidate
    if current:
        chunks.append(current)
    if len(chunks) > max_chunks:
        # Merge the tail into the last kept chunk rather than silently
        # dropping content — long but bounded, better than data loss.
        head, tail = chunks[: max_chunks - 1], chunks[max_chunks - 1 :]
        chunks = head + ["\n\n".join(tail)]
    return chunks


def _fallback_points_from_chunk(chunk: str) -> list[KeyPoint]:
    """Mechanical fallback when a chunk's extraction JSON never parses (after
    retry) — each paragraph becomes its own key point verbatim. Guarantees a
    chunk never contributes zero points just because the model formatted its
    answer badly; the content itself is still real, not invented."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", chunk) if p.strip()]
    points = []
    for p in paras:
        heading = p[:24].strip() + ("…" if len(p) > 24 else "")
        points.append(KeyPoint(heading=heading, summary=p[:400]))
    return points


def _extract_points_from_chunk(
    chunk: str, query: str, *, prof: dict, model: str, log_fn: LogFn,
) -> list[KeyPoint]:
    from pipeline.capability_runtime.chat_runner import generate_text_sync

    user_content = f"Context (what the deck is about): {query}\n\nMaterial:\n{chunk}"
    for attempt in range(2):
        raw = generate_text_sync(
            prof, model,
            [
                {"role": "system", "content": _EXTRACT_SYSTEM},
                {"role": "user", "content": user_content if attempt == 0 else (
                    user_content + "\n\nYour previous answer wasn't valid JSON. Return ONLY a JSON array."
                )},
            ],
            disable_thinking=True,
        )
        data = _parse_json_array(raw)
        if data is not None:
            points = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                heading = str(item.get("heading") or "").strip()
                summary = str(item.get("summary") or "").strip()
                if not heading and not summary:
                    continue
                points.append(KeyPoint(
                    heading=heading or summary[:24],
                    summary=summary or heading,
                    quote=str(item.get("quote") or "").strip(),
                ))
            if points:
                return points
    _log(log_fn, "Deck pipeline: a source chunk's extraction didn't parse — using its raw paragraphs instead.")
    return _fallback_points_from_chunk(chunk)


_BRAINSTORM_SYSTEM = """You are LOMA's Key Point Extractor. Return ONLY a valid JSON array (no markdown fences, no commentary).

No source material was provided or found for this request. From your own general knowledge, produce a reasonable outline of distinct points for the requested topic:
[
  {"heading": "short label, in the SAME language as the request", "summary": "one to two sentences, in the SAME language as the request"}
]

Rules:
- 4 to 10 points, covering genuinely distinct aspects of the topic — never pad with near-duplicates.
- Keep claims general and non-numeric — do not state a specific statistic, date, or figure you cannot verify; describe trends/concepts qualitatively instead.
"""


def _brainstorm_key_points(query: str, *, prof: dict, model: str, log_fn: LogFn) -> list[KeyPoint]:
    from pipeline.capability_runtime.chat_runner import generate_text_sync

    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _BRAINSTORM_SYSTEM},
            {"role": "user", "content": query},
        ],
        disable_thinking=True,
    )
    data = _parse_json_array(raw) or []
    points = []
    for item in data:
        if not isinstance(item, dict):
            continue
        heading = str(item.get("heading") or "").strip()
        summary = str(item.get("summary") or "").strip()
        if heading or summary:
            points.append(KeyPoint(heading=heading or summary[:24], summary=summary or heading))
    return points


def extract_key_points(
    source_text: str, query: str, *, prof: dict, model: str, log_fn: LogFn = None,
) -> list[KeyPoint]:
    if not source_text.strip():
        return _brainstorm_key_points(query, prof=prof, model=model, log_fn=log_fn)
    chunks = _chunk_text(source_text)
    _log(log_fn, f"Deck pipeline: extracting key points from {len(chunks)} source chunk(s)…")
    points: list[KeyPoint] = []
    for chunk in chunks:
        points.extend(_extract_points_from_chunk(chunk, query, prof=prof, model=model, log_fn=log_fn))
        if len(points) >= _MAX_KEY_POINTS:
            break
    return points[:_MAX_KEY_POINTS]


# ---------------------------------------------------------------------------
# Stage 3 — deterministic grouping to the target slide count
# ---------------------------------------------------------------------------


def group_key_points_to_slides(points: list[KeyPoint], n_content_slides: int) -> list[list[KeyPoint]]:
    """Contiguous, order-preserving grouping — deterministic, not another LLM
    guess. Guarantees exactly `n_content_slides` groups regardless of how
    many points were extracted (some groups may be empty if there were fewer
    points than slides; author_slide falls back to brainstorming just that
    slide's content in that case, still bounded to the right count)."""
    n = max(1, n_content_slides)
    if not points:
        return [[] for _ in range(n)]
    base, rem = divmod(len(points), n)
    groups: list[list[KeyPoint]] = []
    i = 0
    for g in range(n):
        size = base + (1 if g < rem else 0)
        groups.append(points[i : i + size])
        i += size
    if i < len(points):
        groups[-1] = groups[-1] + points[i:]
    return groups


# ---------------------------------------------------------------------------
# Stage 4 — per-slide authoring + mechanical verification
# ---------------------------------------------------------------------------

_AUTHOR_SYSTEM = """You are LOMA's Slide Author. Return ONLY valid JSON (no markdown fences):
{"title": "short slide title", "bullets": ["...", "...", "..."], "notes": "1-2 sentence speaker talking point", "image_description": "a concrete visual scene description, or empty string if no image fits"}

Rules:
- title and every bullet and the notes must be in the SAME language as the material below.
- image_description is the ONE exception: always write it in ENGLISH regardless of the material's language — it feeds an English-only image-generation model, never the deck's own language.
- 3 to 5 bullets, each a complete, concrete sentence or phrase.
- Use ONLY facts/claims present in the material — never invent a number, date, name, or claim not stated there.
- image_description: describe a step-by-step process as a flowchart, numeric/trend content as a chart, a comparison as a comparison, a handful of key facts as key facts — otherwise a plain photographic scene matching THIS slide's specific content, not a generic illustration. Never request words/text/captions/signs to appear in the image — diffusion models render requested text as garbled nonsense.
"""

_AUTHOR_RETRY_SUFFIX = (
    "\n\nYour previous answer drifted from the material below — every bullet must be "
    "directly traceable to it. Try again, staying strictly within it."
)


def _parse_json_obj(raw: str) -> dict[str, Any] | None:
    text = (raw or "").strip()
    if not text:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _material_block(points: list[KeyPoint]) -> str:
    lines = []
    for p in points:
        line = f"- {p.heading}: {p.summary}"
        if p.quote:
            line += f" (\"{p.quote}\")"
        lines.append(line)
    return "\n".join(lines)


_FAITHFULNESS_SYSTEM = """You are LOMA's Fact-Checker. Return ONLY valid JSON (no markdown fences): {"faithful": true or false, "issue": "brief description of the contradiction, or empty string"}

Given source material and a list of bullet points claiming to summarize it, check whether any bullet states an outcome or event CONTRARY to what the material actually says — e.g. it claims something broke/failed/happened when the material says it was avoided/caught/prevented, or the reverse. Paraphrasing, omission, and reasonable inference are fine and NOT a contradiction — only flag a bullet that actually reverses what happened.
"""


def _verify_faithfulness(
    bullets: list[str], title: str, material: str, *, prof: dict, model: str, log_fn: LogFn,
) -> bool:
    """Keyword overlap (the earlier check) only proves the bullets are about
    the right topic — it can't catch a specific outcome flipping in
    paraphrase, since both the true and the inverted version share every
    keyword with the source (it's still the same story). Fails open (True)
    on a parse failure — a broken fact-check response shouldn't discard
    otherwise-good content."""
    if not material.strip() or not bullets:
        return True
    from pipeline.capability_runtime.chat_runner import generate_text_sync

    user = f"Source material:\n{material}\n\nBullets to check:\n" + "\n".join(f"- {b}" for b in [title] + bullets)
    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _FAITHFULNESS_SYSTEM},
            {"role": "user", "content": user},
        ],
        disable_thinking=True,
    )
    data = _parse_json_obj(raw)
    if data is None:
        return True
    if data.get("faithful") is False:
        issue = str(data.get("issue") or "").strip()
        _log(log_fn, f"Deck pipeline: fact-check flagged a contradiction — {issue or 'unspecified'}.")
        return False
    return True


def author_slide(
    points: list[KeyPoint], *, title_hint: str, topic: str, prof: dict, model: str, log_fn: LogFn = None,
) -> dict[str, Any]:
    """Returns {title, bullets, notes, image_description}. Verified against
    its own material via keyword overlap (CJK-aware); a drifted result gets
    one retry, then a mechanical fallback built directly from the point
    summaries — real source content, never a generic scaffold placeholder.

    `topic` is the deck's actual subject (its own title, not the user's raw
    request text) — used only when this slide has no assigned key points
    (more slides requested than points extracted). Passing the raw request
    instead of the real topic here made an empty-group slide title itself
    after the command ("create a presentation on the document attached...")
    rather than the document's actual subject — confirmed via a real run."""
    from pipeline.base.source_relevance import keywords
    from pipeline.capability_runtime.chat_runner import generate_text_sync

    material = _material_block(points)
    material_kw = keywords(material)
    user_content = (
        f"Slide topic: {title_hint}\n\n"
        f"Source material for THIS slide only:\n{material}"
        if material else
        f"Slide topic: {title_hint}\n\n(No specific source material for this slide — "
        f"write general, non-numeric content about this aspect of: {topic})"
    )

    def _call(extra: str = "") -> dict[str, Any] | None:
        raw = generate_text_sync(
            prof, model,
            [
                {"role": "system", "content": _AUTHOR_SYSTEM},
                {"role": "user", "content": user_content + extra},
            ],
            disable_thinking=True,
        )
        return _parse_json_obj(raw)

    data = _call() or {}
    bullets = [str(b).strip() for b in (data.get("bullets") or []) if str(b).strip()][:MAX_BULLETS_PER_SLIDE]
    title = str(data.get("title") or "").strip() or title_hint
    notes = str(data.get("notes") or "").strip()
    image_desc = str(data.get("image_description") or "").strip()

    if material and material_kw:
        content_kw = keywords(" ".join(bullets) + " " + title)
        if not (material_kw & content_kw):
            _log(log_fn, f"Deck pipeline: slide '{title_hint}' drifted from its source — retrying once.")
            data = _call(_AUTHOR_RETRY_SUFFIX) or {}
            bullets = [str(b).strip() for b in (data.get("bullets") or []) if str(b).strip()][:MAX_BULLETS_PER_SLIDE]
            title = str(data.get("title") or "").strip() or title_hint
            notes = str(data.get("notes") or "").strip() or notes
            image_desc = str(data.get("image_description") or "").strip() or image_desc
            content_kw = keywords(" ".join(bullets) + " " + title)
            if not (material_kw & content_kw):
                _log(log_fn, f"Deck pipeline: slide '{title_hint}' still drifted — using source summaries directly.")
                bullets = [p.summary[:140] for p in points if p.summary][:MAX_BULLETS_PER_SLIDE]
                title = points[0].heading if points and points[0].heading else title_hint

        # Keyword overlap only proves the bullets are about the right topic —
        # it doesn't catch a specific outcome getting flipped in paraphrase
        # (confirmed via a real run: a source where a general CAUGHT a cup
        # before it broke got summarized as him discarding "a broken cup" —
        # both versions share every keyword with the source, since it's the
        # same story, just with one fact inverted).
        #
        # _verify_faithfulness() (below) was built to catch exactly this via
        # an explicit LLM comprehension check, and is deliberately NOT wired
        # in here — tested directly against this exact case with two
        # different prompt strategies and both failed in real ways, not just
        # "didn't help": the plain judgment prompt said the fabricated
        # "broken cup" bullet was faithful, and the stricter extract-then-
        # compare prompt hallucinated bullets that were never given AND
        # flipped a genuinely correct bullet to "unfaithful". A gate that can
        # silently discard good content is worse than no gate — false
        # negatives here get better content, false positives destroy it. Not
        # a prompt-engineering gap to iterate past; the local model isn't
        # reliable enough at this specific comprehension task yet. Left
        # defined so it can be re-enabled once a capable-enough model is
        # available, rather than deleted and re-invented later.

    if not bullets:
        bullets = [p.summary[:140] for p in points if p.summary][:MAX_BULLETS_PER_SLIDE] or [
            f"{title_hint} — see speaker notes for detail."
        ]
    if not notes:
        highlight = "; ".join(b.rstrip(".") for b in bullets[:2])
        notes = f"Walk through {title}, emphasizing: {highlight}."

    return {"title": title, "bullets": bullets, "notes": notes, "image_description": image_desc}


# ---------------------------------------------------------------------------
# Stage 5 — image-prompt language bridge
# ---------------------------------------------------------------------------

_NON_LATIN_RE = re.compile(r"[一-鿿぀-ヿ가-힯؀-ۿЀ-ӿ]")


def _looks_non_english(text: str) -> bool:
    if not (text or "").strip():
        return False
    hits = len(_NON_LATIN_RE.findall(text))
    return hits / max(1, len(text)) > 0.15


_TRANSLATE_SYSTEM = """You are LOMA's Image Prompt Translator. Return ONLY a valid JSON array of strings, same length and order as the input array — no markdown fences, no commentary.

Each input string is a visual scene description in a non-English language. Rewrite each into a concise, vivid ENGLISH scene description suitable for an image generation model — keep it purely visual (subjects, setting, action, mood), do not translate literally word-for-word if that reads awkwardly, and never include instructions for text/words/captions to appear in the image.
"""


def translate_image_prompts_if_needed(
    descriptions: list[str], *, prof: dict, model: str, log_fn: LogFn = None,
) -> list[str]:
    """Batched — one LLM call for every description that needs translation,
    not one call per image. A non-English prompt fed straight to an
    English-only CLIP text encoder produces a near-meaningless embedding;
    these diffusion checkpoints' learned fallback for that is a generic
    photorealistic portrait, which is the actual mechanism behind
    persistently unrelated images on non-English decks — not a content
    choice, a language mismatch."""
    idx_needing = [i for i, d in enumerate(descriptions) if _looks_non_english(d)]
    if not idx_needing:
        return descriptions

    from pipeline.capability_runtime.chat_runner import generate_text_sync

    _log(log_fn, f"Deck pipeline: translating {len(idx_needing)} image prompt(s) to English for the diffusion model.")
    payload = json.dumps([descriptions[i] for i in idx_needing], ensure_ascii=False)
    raw = generate_text_sync(
        prof, model,
        [
            {"role": "system", "content": _TRANSLATE_SYSTEM},
            {"role": "user", "content": payload},
        ],
        disable_thinking=True,
    )
    translated = _parse_json_array(raw)
    out = list(descriptions)
    if translated and len(translated) == len(idx_needing):
        for i, t in zip(idx_needing, translated):
            if isinstance(t, str) and t.strip():
                out[i] = t.strip()
    else:
        _log(log_fn, "Deck pipeline: image prompt translation failed — those slides will skip their image rather than use an untranslated prompt.")
        for i in idx_needing:
            out[i] = ""
    return out


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def build_deck_via_pipeline(
    *, query: str, bundle: Any, settings: dict | None, prof: dict, model: str,
    target_slides: int | None = None, log_fn: LogFn = None,
) -> tuple[DeckSpec | None, bool]:
    """Returns (deck_spec, presentation_grounded). deck_spec is None only on
    total failure (e.g. no LLM available at all) — callers should fall back
    to the legacy single-call path in that case, never crash."""
    from pipeline.deliverables.presentation_finalize import _infer_deck_title

    target = max(MIN_SLIDES + 2, min(MAX_SLIDES, int(target_slides or 8)))
    n_content = max(1, target - 3)  # reserve title, agenda, closing

    try:
        source_text, grounded = resolve_source_material(
            bundle=bundle, query=query, settings=settings, log_fn=log_fn,
        )
        points = extract_key_points(source_text, query, prof=prof, model=model, log_fn=log_fn)
        groups = group_key_points_to_slides(points, n_content)

        # Computed before authoring (not after) so an empty group — more
        # slides requested than points extracted — falls back to writing
        # about the deck's actual subject, not the user's raw command text.
        deck_title = ((points[0].heading if points else "") or _infer_deck_title(query))[:70]

        authored = []
        for group in groups:
            hint = group[0].heading if group else deck_title
            authored.append(author_slide(
                group, title_hint=hint, topic=deck_title, prof=prof, model=model, log_fn=log_fn,
            ))

        image_descs = [a["image_description"] for a in authored]
        image_descs = translate_image_prompts_if_needed(image_descs, prof=prof, model=model, log_fn=log_fn)
        for a, translated in zip(authored, image_descs):
            a["image_description"] = translated

        slides: list[SlideSpec] = [
            SlideSpec(index=1, layout="title", title=deck_title, subtitle="", bullets=[]),
        ]
        agenda_bullets = [a["title"] for a in authored if a["title"]][:MAX_BULLETS_PER_SLIDE]
        slides.append(SlideSpec(index=2, layout="content", title="Agenda", bullets=agenda_bullets))
        for i, a in enumerate(authored):
            slides.append(SlideSpec(
                index=3 + i,
                layout="content",
                title=a["title"],
                bullets=a["bullets"],
                notes=a["notes"],
                visual_type="image" if a["image_description"] else "none",
                visual_description=a["image_description"],
            ))
        closing_bullets = [a["title"] for a in authored[-3:] if a["title"]] or ["Thank you."]
        slides.append(SlideSpec(
            index=len(slides) + 1,
            layout="closing",
            title="Key Takeaways",
            bullets=closing_bullets,
            notes="Recap the main points and close.",
        ))

        _log(log_fn, f"Deck pipeline: built {len(slides)} slides from {len(points)} extracted key point(s).")
        return DeckSpec(deck_title=deck_title, slides=slides, design={}), grounded
    except Exception as ex:
        _log(log_fn, f"Deck pipeline failed ({ex}) — falling back to legacy single-call planning.")
        return None, False
