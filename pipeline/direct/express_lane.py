# -*- coding: utf-8 -*-
"""Express lane guards — trivial chat only."""
from __future__ import annotations

import re

from pipeline.output_format import infer_format_from_query, normalize_output_type
from pipeline.schemas.task_schema import InputMetadata

_MULTI_STEP_SEP_EN = re.compile(r"\b(then|and then|after that|next|→|->)\b", re.I)
_TASK_VERBS_EN = re.compile(
    r"\b(translate|translation|summarize|summarise|summar\w*|transcri\w*|"
    r"rewrite|rephrase|convert|localize|localise|edit|polish|fix|mutate|"
    r"revise|extract|analyse|analyze|draft|compose|write|output)\b",
    re.I,
)
_IMAGE_TRANSFORM_EN = re.compile(r"\b(translate|transcri\w*|ocr|extract\s+text|redact)\b", re.I)

_TASK_CONCEPTS = (
    "verb_translate", "verb_summarize", "verb_transcribe", "verb_rewrite",
    "verb_extract", "verb_analyze", "verb_write_author", "image_mutation_hints",
)
_DELIVERABLE_CONCEPTS = (
    "document_format_hint", "presentation_format_hint", "image_deliverable_hints",
)
_DELIVERABLE_EXTRA = ("docx", "pptx", "xlsx", "mp3", "audio file", "narration")


class _MultiStepSepRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        return _MULTI_STEP_SEP_EN.search(text) or (
            matches(text, "multi_step_separator") or None
        )


class _TaskVerbsRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import any_matches

        return _TASK_VERBS_EN.search(text) or (any_matches(text, _TASK_CONCEPTS) or None)


class _ImageTransformRE:
    def search(self, text: str):
        from pipeline.query_intent_i18n import matches

        if _IMAGE_TRANSFORM_EN.search(text):
            return True
        return matches(text, "verb_translate") or matches(text, "verb_transcribe") or None


_MULTI_STEP_SEP = _MultiStepSepRE()
_TASK_VERBS = _TaskVerbsRE()
_IMAGE_TRANSFORM = _ImageTransformRE()


def can_use_express_lane(
    metadata: InputMetadata,
    *,
    output_type: str | None = None,
    needs_vision: bool = False,
) -> bool:
    """
    Express = single general_answer streaming pass.
    Requires: chat output, no files/links, no task verbs, simple Q&A.
    """
    if needs_vision:
        return False
    if metadata.file_count or metadata.link_count:
        return False
    if metadata.has_docs or metadata.has_image:
        return False
    if metadata.has_valid_preview_selection or metadata.has_preview_selection:
        return False

    ot = normalize_output_type(output_type or metadata.preferred_output_format)
    if ot != "chat":
        return False
    if infer_format_from_query(metadata.query or ""):
        return False

    q = (metadata.query or "").strip()
    if not q:
        return False
    lower = q.lower()
    if _MULTI_STEP_SEP.search(lower):
        return False
    if _TASK_VERBS.search(lower):
        return False
    from pipeline.query_intent_i18n import any_matches

    if any(h in lower for h in _DELIVERABLE_EXTRA) or any_matches(lower, _DELIVERABLE_CONCEPTS):
        return False
    if " and " in lower and _TASK_VERBS.search(lower):
        return False
    return len(q) <= 800


def should_use_image_qa_fast_path(metadata: InputMetadata) -> bool:
    """
    Single image + chat Q&A → skip planner LLM (deterministic general_answer step).
    """
    if not metadata.has_image:
        return False
    if metadata.file_count != 1 or metadata.link_count:
        return False
    if metadata.has_docs or metadata.has_valid_preview_selection or metadata.has_preview_selection:
        return False
    ot = normalize_output_type(metadata.preferred_output_format)
    explicit = infer_format_from_query(metadata.query or "")
    if explicit and explicit != "chat":
        return False
    if ot != "chat":
        return False
    q = (metadata.query or "").strip()
    if not q or len(q) > 800:
        return False
    if _MULTI_STEP_SEP.search(q):
        return False
    if _IMAGE_TRANSFORM.search(q):
        return False
    return True


# Backward-compatible alias
is_express_lane = can_use_express_lane
