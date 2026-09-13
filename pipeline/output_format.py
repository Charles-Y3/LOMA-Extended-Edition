# -*- coding: utf-8 -*-
"""Output format registry: UI options, query inference, and resolution priority."""
from __future__ import annotations

import re

DEFAULT_OUTPUT_FORMAT = "chat"

USER_OUTPUT_FORMAT_KEYS = (
    "chat",
    "document",
    "presentation",
    "image",
)

VALID_OUTPUT_TYPES = frozenset(USER_OUTPUT_FORMAT_KEYS)

GENERATION_ONLY_TYPES = frozenset()

EXTENSION_BY_TYPE: dict[str, str] = {
    "chat": ".txt",
    "document": ".docx",
    "presentation": ".pptx",
    "image": ".png",
    "video": ".mp4",
}

# Anchored: an explicit extension, "word document"/"document format", or a directive
# verb near the word "document" — not a bare substring match, which false-triggers on
# any mention of the word (e.g. asking about the "Document Intelligence" extension).
# One regex per locale — the anchoring *structure* (verb near noun) is English-grammar
# shaped, so each locale gets its own pattern rather than a shared phrase list (see
# pipeline/query_intent_i18n.py's docstring on why regex-shaped concepts don't just
# swap vocabulary). Format-name concepts (document/presentation) live in
# query_intent_i18n.CONCEPTS as "document_format_hint"/"presentation_format_hint" for
# the always-a-match keywords (".docx", "word document", ...); these regexes add the
# looser "verb ... document/presentation" anchoring per language.
_DOCUMENT_FORMAT_RE_BY_LOCALE = {
    "en": re.compile(
        r"\.docx\b"
        r"|\bword\s+document\b"
        r"|\bdocument\s+format\b"
        r"|\b(?:as|into|in)\s+(?:a\s+)?(?:word\s+)?docx?\b"
        r"|\b(?:generate|create|write|make|produce|draft|export|turn|convert|put|save|compile|give|provide|send)\b"
        r"(?:\s+\S+){0,4}?\s+(?:a\s+|this\s+|the\s+)?(?:word\s+)?document\b",
        re.IGNORECASE,
    ),
    "zh_tw": re.compile(r"\.docx|word文件|文件格式|(?:產生|建立|寫|做|製作|匯出|轉換|存成|存為).{0,6}文件"),
    "zh_cn": re.compile(r"\.docx|word文档|文档格式|(?:生成|创建|写|做|制作|导出|转换|存成|存为).{0,6}文档"),
    "es": re.compile(
        r"\.docx\b|documento\s+word|formato\s+de\s+documento"
        # Verb stems (not just infinitives) so conjugations match too: "crea", "crear",
        # "creando", "escríbeme", etc. — Spanish conjugates by person/mood, unlike the
        # English list which only needs the bare imperative/infinitive form.
        r"|\b(?:gener\w*|cre\w*|escrib\w*|hac\w*|produc\w*|redact\w*|export\w*|convert\w*|guard\w*|compil\w*|d[ae]\w*|proporcion\w*|envi\w*)\b"
        r"(?:\s+\S+){0,4}?\s+(?:un\s+|el\s+|la\s+)?documento\b",
        re.IGNORECASE,
    ),
    "de": re.compile(
        r"\.docx\b|word-dokument|dokumentformat"
        r"|\b(?:erstell\w*|schreib\w*|generier\w*|mach\w*|produzier\w*|exportier\w*|konvertier\w*|speicher\w*|geb\w*|send\w*)\b"
        r"(?:\s+\S+){0,4}?\s+(?:ein\s+|das\s+)?dokument\b",
        re.IGNORECASE,
    ),
}

# Same anchoring for presentation — bare "presentation"/"powerpoint" false-triggers the
# same way ("what is a presentation extension").
_PRESENTATION_FORMAT_RE_BY_LOCALE = {
    "en": re.compile(
        r"\.pptx\b"
        r"|\bslide\s+deck\b"
        r"|\bas\s+(?:a\s+)?pptx\b"
        r"|\b\d+\s*slides?\b"
        r"|\b(?:generate|create|write|make|produce|draft|export|turn|convert|put|save|compile|build)\b"
        r"(?:\s+\S+){0,4}?\s+(?:a\s+|this\s+|the\s+)?(?:powerpoint\s+)?presentation\b",
        re.IGNORECASE,
    ),
    "zh_tw": re.compile(r"\.pptx|投影片|簡報|\d+\s*張投影片|(?:產生|建立|寫|做|製作|匯出|轉換|存成|存為).{0,6}簡報"),
    "zh_cn": re.compile(r"\.pptx|幻灯片|演示文稿|\d+\s*张幻灯片|(?:生成|创建|写|做|制作|导出|转换|存成|存为).{0,6}(?:演示文稿|幻灯片)"),
    "es": re.compile(
        r"\.pptx\b|diapositivas|\d+\s*diapositivas"
        r"|\b(?:gener\w*|cre\w*|escrib\w*|hac\w*|produc\w*|redact\w*|export\w*|convert\w*|guard\w*|compil\w*)\b"
        r"(?:\s+\S+){0,4}?\s+(?:una\s+|la\s+)?presentaci[oó]n\b",
        re.IGNORECASE,
    ),
    "de": re.compile(
        r"\.pptx\b|folien|\d+\s*folien"
        r"|\b(?:erstell\w*|schreib\w*|generier\w*|mach\w*|produzier\w*|exportier\w*|konvertier\w*|speicher\w*)\b"
        r"(?:\s+\S+){0,4}?\s+(?:eine\s+)?präsentation\b",
        re.IGNORECASE | re.UNICODE,
    ),
}


def _matches_any_locale(text: str, patterns: dict[str, re.Pattern]) -> bool:
    return any(p.search(text) for p in patterns.values())


class _DocumentFormatRE:
    def search(self, text: str):
        for p in _DOCUMENT_FORMAT_RE_BY_LOCALE.values():
            m = p.search(text)
            if m:
                return m
        return None


class _PresentationFormatRE:
    def search(self, text: str):
        for p in _PRESENTATION_FORMAT_RE_BY_LOCALE.values():
            m = p.search(text)
            if m:
                return m
        return None


_DOCUMENT_FORMAT_RE = _DocumentFormatRE()
_PRESENTATION_FORMAT_RE = _PresentationFormatRE()

# "Can you generate images" is a capability question, not a request — but "can you
# generate a report on Q3 sales as a docx" is a real request wearing polite phrasing.
# Treat short "can/could you ..." queries with no subject-matter clause as a capability
# question (route to chat), not a deliverable request, regardless of format keywords.
_CAPABILITY_QUESTION_RE = re.compile(
    r"^\s*(?:so\s+|then\s+|hey\s+|ok(?:ay)?\s+)?(?:can|could)\s+you\b"
    r"|^\s*are\s+you\s+able\s+to\b"
    r"|^\s*do\s+you\s+support\b"
    r"|^\s*(?:你|妳)\s*(?:可以|能不能|能夠|能够)\b"
    r"|^\s*(?:puedes|podrías|podrias|eres capaz de)\b"
    r"|^\s*(?:kannst du|könntest du|koenntest du)\b",
    re.IGNORECASE,
)

_TOPIC_CLAUSE_RE = re.compile(
    r"\b(?:about|on|regarding|covering|titled|called)\s+\w"
    r"|(?:關於|关于|有關|有关)\S"
    r"|\b(?:sobre|acerca de|titulado)\s+\w"
    r"|\b(?:über|ueber|zu|mit dem titel)\s+\w",
    re.IGNORECASE,
)


def _is_bare_capability_question(user_input: str) -> bool:
    text = (user_input or "").strip()
    if not _CAPABILITY_QUESTION_RE.search(text):
        return False
    if len(text.split()) > 12:
        return False
    return not _TOPIC_CLAUSE_RE.search(text)


def output_format_option_keys() -> list[str]:
    return list(USER_OUTPUT_FORMAT_KEYS)


def output_format_select_options() -> dict[str, str]:
    from pipeline.i18n import t

    return {key: t(f"output_format.{key}") for key in output_format_option_keys()}


def deliverable_display_name(output_type: str | None) -> str:
    """Human label for chat success messages."""
    key = normalize_output_type(output_type)
    return {
        "document": "Document",
        "presentation": "Presentation",
        "image": "Image",
        "chat": "Chat",
    }.get(key, "Document")


def normalize_output_type(value: str | None) -> str:
    key = (value or DEFAULT_OUTPUT_FORMAT).strip().lower()
    if key in VALID_OUTPUT_TYPES:
        return key
    return DEFAULT_OUTPUT_FORMAT


def infer_format_from_query(user_input: str) -> str | None:
    """Detect explicit deliverable format named in the user query."""
    if _is_bare_capability_question(user_input):
        return None
    lower = (user_input or "").lower()
    from pipeline.query_intent_i18n import matches

    # Presentation/document checked first: their regexes require specific
    # verb+noun phrasing ("generate ... presentation", "... slides", "as a
    # docx"), so they only match a genuine, explicit request — never a stray
    # single keyword. Checking them before the image-hints block below means an
    # explicit "generate a presentation about X, with infographics/charts to
    # support it" correctly stays a presentation instead of a single mentioned
    # word ("infographic"/"diagram"/"poster") hijacking the whole request into
    # a standalone image (this used to happen every time "infographic" appeared
    # anywhere in a presentation request, since it's also a poster/infographic
    # trigger word below).
    if _PRESENTATION_FORMAT_RE.search(lower):
        return "presentation"
    if _DOCUMENT_FORMAT_RE.search(lower):
        return "document"
    # A "flowchart"/"poster"/"timeline"/"key facts" request otherwise has no
    # explicit-format regex match at all (nothing above names "document" or
    # "presentation"), so it fell through to the embedding classifier, which
    # guessed "document" for phrasing like "produce a workflow image for X" or
    # "timeline of X" — sending it down the wrong pipeline entirely (an
    # illustrated .docx, not an image) before
    # pipeline/direct/image_intent.py's poster/diagram/infographic split ever
    # got a chance to run. Only reached now when neither presentation nor
    # document explicitly matched above.
    if (
        matches(user_input, "diagram_image_hints")
        or matches(user_input, "poster_image_hints")
        or matches(user_input, "infographic_timeline_hints")
        or matches(user_input, "infographic_stat_hints")
        or matches(user_input, "infographic_comparison_hints")
        or matches(user_input, "chart_image_hints")
    ):
        return "image"
    if matches(user_input, "image_deliverable_hints"):
        return "image"
    return None


def resolve_output_type(
    user_input: str,
    preferred: str | None,
    classifier_output: str | None,
) -> str:
    """Query explicit format > dropdown preference > embedding intent > classifier."""
    inferred = infer_format_from_query(user_input)
    if inferred:
        return inferred
    pref = normalize_output_type(preferred)
    if pref != DEFAULT_OUTPUT_FORMAT:
        return pref
    from pipeline.intent_embeddings import classify_intent

    embedded = classify_intent(user_input)
    if embedded:
        return normalize_output_type(embedded)
    return normalize_output_type(classifier_output)


def resolve_deliverable_type(user_input: str, preferred: str | None) -> str:
    """
    Final deliverable for routing and capabilities.

    Priority: explicit format in the user query > UI output-format selector > chat.
    """
    return resolve_output_type(user_input, preferred, None)


# Output types that compile to office artifacts via artifact_store + renderer.
_ARTIFACT_OUTPUT_TYPES = frozenset({"document", "presentation"})


def query_requests_image_deliverable(user_input: str) -> bool:
    """True when the user is asking for a generated image as the deliverable."""
    return infer_format_from_query(user_input) == "image"


_IMAGE_FORMAT_RE = re.compile(
    r"\bas\s+(?:a\s+)?(jpg|jpeg|png|webp|bmp)\b|\.(jpg|jpeg|png|webp|bmp)\b", re.IGNORECASE
)


def infer_image_format_from_query(user_input: str) -> str | None:
    """Detect an explicitly requested image file extension, e.g. 'as a jpg', '.webp'."""
    m = _IMAGE_FORMAT_RE.search(user_input or "")
    if not m:
        return None
    return (m.group(1) or m.group(2) or "").lower() or None


_CONTEXT_RETRIEVAL_SERVICES = frozenset(
    {"document_chunker", "text_search", "context_selector", "model_router", "resource_governor"}
)


def allowed_services_for_deliverable(deliverable_type: str) -> set[str]:
    """Service ids that may appear on a routing decision for this deliverable."""
    deliverable = normalize_output_type(deliverable_type)
    allowed = {"llm_bridge", "file_io", "web_fetch", "graph_generation"}
    if deliverable == "chat":
        allowed |= _CONTEXT_RETRIEVAL_SERVICES | {"media_transcription"}
    if deliverable in _ARTIFACT_OUTPUT_TYPES:
        allowed.update({"artifact_store", "renderer"})
    elif deliverable == "image":
        allowed.add("image_generation")
    return allowed


def constrain_services_to_deliverable(
    required: set[str],
    deliverable_type: str,
    *,
    file_count: int = 0,
    link_count: int = 0,
) -> set[str]:
    """Drop services that contradict the resolved deliverable; re-apply base requirements."""
    deliverable = normalize_output_type(deliverable_type)
    allowed = allowed_services_for_deliverable(deliverable)
    filtered = {s for s in required if s in allowed}
    filtered |= required_services_for_deliverable(
        deliverable, file_count=file_count, link_count=link_count
    )
    return filtered


def required_services_for_deliverable(
    deliverable_type: str,
    *,
    file_count: int = 0,
    link_count: int = 0,
) -> set[str]:
    """Map a deliverable type + input shape to atomic service ids (registry vocabulary)."""
    services = {"llm_bridge"}
    if file_count:
        services.add("file_io")
    if link_count:
        services.add("web_fetch")

    deliverable = normalize_output_type(deliverable_type)
    if deliverable == "chat" and (file_count or link_count):
        services |= _CONTEXT_RETRIEVAL_SERVICES
    if deliverable in _ARTIFACT_OUTPUT_TYPES:
        services.update({"artifact_store", "renderer"})
    elif deliverable == "image":
        services.add("image_generation")
    return services


def apply_mode_for_output(
    output_type: str,
    mode,
    *,
    user_input: str,
    has_docs: bool,
    has_image: bool = False,
    metadata=None,
) -> str | None:
    """Set generation/mutation mode after output type is resolved."""
    if output_type == "chat":
        return None
    if output_type in GENERATION_ONLY_TYPES:
        if output_type == "image" and has_image and _should_force_mutation(user_input, output_type):
            return "mutation"
        return "generation"
    # document / presentation: defer to the single authoritative resolver so routing agrees
    # with the direct planner (whole-document translate/summarize = generation; partial edit
    # of the uploaded file = mutation). Heuristic-only (model=None) — no extra LLM call.
    if metadata is not None:
        from pipeline.direct.mode_resolver import resolve_direct_mode

        return resolve_direct_mode(output_type, metadata, user_input, model=None)
    if has_docs and _should_force_mutation(user_input, output_type):
        return "mutation"
    return mode if mode in ("generation", "mutation") else "generation"


def _should_force_mutation(user_input: str, output_type: str) -> bool:
    if output_type not in ("document", "presentation", "image"):
        return False
    lower = (user_input or "").lower()
    edit_words = (
        "mutate",
        "mutation",
        "translate",
        "translation",
        "convert",
        "rewrite",
        "fix",
        "update",
        "edit",
        "change",
        "replace",
        "localize",
        "localise",
        "preserve",
        "formatting",
        "layout",
        "do not change",
        "only the english",
        "only english",
        "vietnamese",
        "spanish",
        "german",
        "french",
        "modify",
        "rephrase",
    )
    return any(w in lower for w in edit_words)
