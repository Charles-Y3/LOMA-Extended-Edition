# -*- coding: utf-8 -*-
"""Resolve generation vs mutation for direct pipeline (heuristic + LLM)."""
from __future__ import annotations

import json
import re

from pipeline.output_format import normalize_output_type
from pipeline.schemas.task_schema import InputMetadata

_OFFICE_EXTS = (".docx", ".pptx", ".xlsx", ".xls")
_OFFICE_TYPES = frozenset({"document", "presentation", "spreadsheet", "text"})

# English structural regex kept for the verbs whose exact word list matters for the
# surrounding logic (edit/update/change/modify/mutate/fix/correct/replace/convert/
# revise/amend/adjust) — non-English equivalents are folded in via the multilingual
# concept phrases in query_intent_i18n ("verb_translate" covers translate/localize;
# the remaining "change"-shaped verbs are covered directly below per locale).
_CHANGE_VERBS_EN = re.compile(
    r"\b(translate|translation|edit|update|change|modify|mutate|rewrite|rephrase|"
    r"fix|correct|replace|localize|localise|convert|revise|amend|adjust)\b",
    re.I,
)
_CHANGE_VERBS_ZH = re.compile(r"翻譯|翻译|編輯|编辑|更新|修改|改寫|改写|修正|更正|替換|替换|調整|调整")
_CHANGE_VERBS_ES = re.compile(
    r"\b(traduc\w*|edit\w*|actualiz\w*|cambi\w*|modific\w*|reescrib\w*|reformul\w*|"
    r"corrig\w*|correct\w*|reemplaz\w*|convert\w*|revis\w*|ajust\w*)\b",
    re.I,
)
_CHANGE_VERBS_DE = re.compile(
    r"\b(übersetz\w*|uebersetz\w*|bearbeit\w*|aktualisier\w*|änder\w*|aender\w*|"
    r"modifizier\w*|umschreib\w*|korrigier\w*|ersetz\w*|konvertier\w*|überarbeit\w*|"
    r"ueberarbeit\w*|anpass\w*)\b",
    re.I,
)


class _ChangeVerbsRE:
    def search(self, text: str):
        for pat in (_CHANGE_VERBS_EN, _CHANGE_VERBS_ZH, _CHANGE_VERBS_ES, _CHANGE_VERBS_DE):
            m = pat.search(text)
            if m:
                return m
        return None


_CHANGE_VERBS = _ChangeVerbsRE()


_CREATE_FROM_SCRATCH_EN = re.compile(
    r"\b(create\s+(?:a\s+)?new|generate\s+(?:a\s+)?new|write\s+(?:a\s+)?new|"
    r"from\s+scratch|brand\s+new|compose\s+(?:a\s+)?new)\b",
    re.I,
)
_CREATE_FROM_SCRATCH_OTHER = re.compile(
    r"全新|從頭開始|从头开始|重新開始|重新开始"
    r"|nuevo\s+documento|nueva\s+presentaci[oó]n|desde\s+cero"
    r"|neues\s+dokument|neue\s+präsentation|neue\s+praesentation|von\s+grund\s+auf",
    re.I,
)


class _CreateFromScratchRE:
    def search(self, text: str):
        return _CREATE_FROM_SCRATCH_EN.search(text) or _CREATE_FROM_SCRATCH_OTHER.search(text)


_CREATE_FROM_SCRATCH = _CreateFromScratchRE()


def _generate_report_search(text: str):
    from pipeline.query_intent_i18n import matches

    return matches(text, "verb_analyze") and re.search(
        r"report|document|docx|報告|文件|报告|文档|informe|documento|bericht|dokument",
        text, re.I,
    )


class _GenerateReportRE:
    def search(self, text: str):
        return _generate_report_search(text)


_GENERATE_REPORT = _GenerateReportRE()


_ANALYSE_REPORT_EN = re.compile(r"\banalys(?:e|is)\b.*\b(?:report|document|docx)\b", re.I)
_ANALYSE_REPORT_OTHER = re.compile(
    r"分析.*(?:報告|文件|报告|文档)"
    r"|an[aá]lisis.*(?:informe|documento)"
    r"|analyse.*(?:bericht|dokument)",
    re.I,
)


class _AnalyseReportRE:
    def search(self, text: str):
        return _ANALYSE_REPORT_EN.search(text) or _ANALYSE_REPORT_OTHER.search(text)


_ANALYSE_REPORT = _AnalyseReportRE()


def _lang_pair_search(text: str):
    """True when the query names a source AND target language (any supported locale's
    language-name vocabulary), e.g. "chinese to english" / "西班牙語翻譯成英語"."""
    from pipeline.query_intent_i18n import find_language_target, matches

    if not matches(text, "verb_translate") and find_language_target(text) is None:
        return None
    # A translate verb plus a resolvable target language is a strong enough signal on
    # its own — the original English-only regex additionally required a *source*
    # language name, but that's overly strict for non-English phrasing where the
    # source language is often implicit ("translate to Spanish" instead of naming
    # English explicitly).
    return find_language_target(text) is not None or None


class _LangPairRE:
    def search(self, text: str):
        return _lang_pair_search(text)


_LANG_PAIR = _LangPairRE()

_TYPE_ALIASES = {
    "document": frozenset({"document", "text"}),
    "presentation": frozenset({"presentation"}),
    "spreadsheet": frozenset({"spreadsheet"}),
}

# Explicit references to a specific unit of an uploaded office file. Naming a slide,
# cell, column, row, paragraph, page, or sheet number is an unambiguous signal that the
# user wants to change PART of the source (mutation), not transform the whole document.
_PARTIAL_SCOPE_REF = re.compile(
    r"\b(?:slides?|pages?|paragraphs?|paras?|rows?|sheets?|sections?|bullets?)\s+\d+"
    r"|\bcells?\s+[a-z]{1,3}\d+"
    r"|\bcol(?:umn)?s?\s+[a-z]{1,3}\b",
    re.I,
)


def has_explicit_unit_scope(query: str) -> bool:
    """True when the query names a specific slide/cell/column/row/paragraph/page/sheet."""
    return bool(_PARTIAL_SCOPE_REF.search((query or "").strip()))


def _file_output_type(file_meta: dict) -> str | None:
    if not isinstance(file_meta, dict):
        return None
    name = (file_meta.get("name") or file_meta.get("filename") or "").lower()
    ftype = (file_meta.get("type") or "").lower()
    for ot, aliases in _TYPE_ALIASES.items():
        if ftype in aliases:
            return ot
    if name.endswith(".docx"):
        return "document"
    if name.endswith(".pptx"):
        return "presentation"
    if name.endswith((".xlsx", ".xls", ".csv")):
        return "spreadsheet"
    return None


def _primary_source_output_type(metadata: InputMetadata) -> str | None:
    files = [f for f in (metadata.files or []) if isinstance(f, dict)]
    if len(files) != 1:
        return None
    return _file_output_type(files[0])


def _source_matches_output(metadata: InputMetadata, output_type: str) -> bool:
    src = _primary_source_output_type(metadata)
    if not src:
        return False
    return normalize_output_type(src) == normalize_output_type(output_type)


def _is_partial_document_edit(query: str, metadata: InputMetadata) -> bool:
    """True when the user targets part of an upload (selection or explicit in-place hints)."""
    if metadata.has_valid_preview_selection:
        return True
    if has_explicit_unit_scope(query):
        return True
    from pipeline.query_intent_i18n import matches

    lower = (query or "").strip().lower()
    if matches(lower, "selective_scope"):
        return True
    partial_hints_en = (
        "in place", "in the file", "in the document", "in the pptx", "in the docx",
        "in the spreadsheet", "only slide", "only paragraph", "only chinese",
        "only english", "edit the", "edit this", "update slide", "update cell",
    )
    partial_hints_other = (
        "原地", "在檔案中", "在文件中", "在文档中", "只改投影片", "只改幻灯片", "只改段落",
        "en el lugar", "en el archivo", "en el documento", "solo la diapositiva",
        "solo el párrafo", "solo el parrafo", "an ort und stelle", "in der datei",
        "im dokument", "nur folie", "nur absatz",
    )
    return any(h in lower for h in partial_hints_en) or any(h in lower for h in partial_hints_other)


def _is_whole_document_transform(query: str, metadata: InputMetadata) -> bool:
    """Translate or summarize the full source → new deliverable (generation, not in-place mutation)."""
    if not metadata_has_office_artifact(metadata):
        return False
    q = (query or "").strip()
    if not q or _is_partial_document_edit(q, metadata):
        return False
    from pipeline.query_intent_i18n import matches

    lower = q.lower()
    if matches(lower, "verb_translate"):
        return True
    if matches(lower, "verb_summarize"):
        return True
    if _LANG_PAIR.search(lower):
        return True
    return False


def _wants_modify_uploaded_file(query: str, output_type: str, metadata: InputMetadata) -> bool:
    """True when user intent is to change content in the uploaded file (same format)."""
    from pipeline.query_intent_i18n import matches

    q = (query or "").strip()
    if not q:
        return False
    if _is_whole_document_transform(q, metadata):
        return False
    lower = q.lower()
    if metadata.has_valid_preview_selection:
        return True
    if _CREATE_FROM_SCRATCH.search(lower) and not _CHANGE_VERBS.search(lower):
        return False
    if _CHANGE_VERBS.search(lower) or _LANG_PAIR.search(lower):
        return True
    if matches(lower, "verb_summarize") or matches(lower, "verb_rewrite"):
        return True
    return False


def metadata_has_office_artifact(metadata: InputMetadata | None) -> bool:
    if metadata is None:
        return False
    for f in metadata.files or []:
        if not isinstance(f, dict):
            continue
        name = (f.get("name") or f.get("filename") or "").lower()
        ftype = (f.get("type") or "").lower()
        if ftype in ("presentation", "document", "spreadsheet"):
            return True
        if any(name.endswith(ext) for ext in _OFFICE_EXTS):
            return True
    return False


def _source_output_mismatch(metadata: InputMetadata, output_type: str) -> bool:
    """Spreadsheet/analysis → new .docx report is generation, not in-place mutation."""
    ot = normalize_output_type(output_type)
    if ot != "document":
        return False
    for f in metadata.files or []:
        if not isinstance(f, dict):
            continue
        ftype = (f.get("type") or "").lower()
        name = (f.get("name") or f.get("filename") or "").lower()
        if ftype == "spreadsheet" or name.endswith((".xlsx", ".xls", ".csv")):
            return True
    return False


def _heuristic_generation(query: str, output_type: str, metadata: InputMetadata) -> bool:
    """New deliverable from data source (e.g. analyse xlsx → write docx)."""
    if not metadata_has_office_artifact(metadata):
        return False
    q = (query or "").strip()
    if not q:
        return False
    if _GENERATE_REPORT.search(q) or _ANALYSE_REPORT.search(q):
        return True
    if _source_output_mismatch(metadata, output_type):
        if _CREATE_FROM_SCRATCH.search(q.lower()) or "report" in q.lower():
            return True
        if re.search(r"\bdocx\b", q, re.I) and not _CHANGE_VERBS.search(q):
            return True
    return False


def _heuristic_mutation(query: str, output_type: str, metadata: InputMetadata) -> bool:
    """Uploaded office artifact + partial change intent → in-place mutation."""
    ot = normalize_output_type(output_type)
    if ot == "chat" or not metadata_has_office_artifact(metadata):
        return False
    q = (query or "").strip()
    if not q:
        return False
    if _is_whole_document_transform(q, metadata):
        return False
    lower = q.lower()
    if _CREATE_FROM_SCRATCH.search(lower) and not _CHANGE_VERBS.search(lower):
        return False
    if metadata.has_valid_preview_selection:
        return True
    if _is_partial_document_edit(q, metadata):
        return True
    if re.search(r"\b(?:edit|update|fix|replace|rewrite)\b", lower):
        return True
    return False


def _parse_mode_json(raw: str) -> str | None:
    text = (raw or "").strip()
    match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    mode = str(data.get("mode") or "").strip().lower()
    if mode in ("mutation", "generation"):
        return mode
    return None


def infer_mode_llm(
    metadata: InputMetadata,
    output_type: str,
    *,
    model: str,
    log_fn=None,
) -> str | None:
    """Small LLM call: mutation vs generation when an office file is attached."""
    if not model or not metadata_has_office_artifact(metadata):
        return None
    ot = normalize_output_type(output_type)
    if ot == "chat":
        return "generation"

    files = ", ".join(
        f"{f.get('name', '?')} ({f.get('type', '?')})" for f in (metadata.files or [])
    )
    prompt = (
        "Decide execution mode for LOMA direct pipeline.\n"
        "Return ONLY JSON: {\"mode\": \"mutation\" | \"generation\", \"reason\": \"...\"}\n\n"
        "mutation = edit part of the uploaded office file in place (selected text, one slide, one cell).\n"
        "generation = work with the full source and produce a new deliverable (translate entire document, "
        "summarize entire document, new report/deck from source data).\n"
        "If user uploads a spreadsheet and asks for a new analysis report as .docx → generation.\n\n"
        f"Output deliverable type: {ot}\n"
        f"Attached files: {files}\n"
        f"User request: {metadata.query}\n\n"
        "If the user wants to change content in the uploaded file, choose mutation."
    )
    try:
        from services import llm_bridge as chat_client
        from services.resource_governor import ResourceGovernor

        from services.inference.ollama_chat import build_planner_chat_request

        with ResourceGovernor.acquire("llm_chat"):
            resp = chat_client.chat(
                **build_planner_chat_request(
                    None,
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                )
            )
        raw = (resp.get("message") or {}).get("content") or ""
        mode = _parse_mode_json(raw)
        if mode and log_fn:
            log_fn(f"Mode resolver (LLM): {mode}")
        return mode
    except Exception as exc:
        if log_fn:
            log_fn(f"Mode resolver LLM failed: {exc}")
        return None


def resolve_direct_mode(
    output_type: str,
    metadata: InputMetadata,
    query: str,
    *,
    proposed_mode: str = "generation",
    model: str | None = None,
    log_fn=None,
) -> str:
    """Final mode: chat→generation; in-place file edit→mutation when deliverable is not chat."""
    ot = normalize_output_type(output_type)
    if ot == "chat":
        return "generation"
    if metadata.has_valid_preview_selection:
        return "mutation"
    if _is_whole_document_transform(query, metadata):
        if log_fn:
            log_fn("Mode resolver: generation (whole-document translate/summarize)")
        return "generation"
    # The PDF-specific carve-out that used to live here (force generation whenever
    # a PDF + translate query reached this point) is now redundant: PDF is a
    # first-class "document" kind (services/source_parser/parse.py), so
    # _is_whole_document_transform above already covers whole-document PDF
    # translation the same way it covers .docx — and unlike this carve-out, it
    # correctly excludes partial-scope PDF requests ("translate only page 3"),
    # which now fall through to the mutation logic below like any other format.
    if not metadata_has_office_artifact(metadata):
        return proposed_mode if proposed_mode in ("generation", "mutation") else "generation"

    if _heuristic_generation(query, ot, metadata):
        if log_fn:
            log_fn("Mode resolver: generation (new deliverable from source data)")
        return "generation"

    file_count = metadata.file_count or len(metadata.files or [])
    if file_count == 1 and not _source_matches_output(metadata, ot):
        src = _primary_source_output_type(metadata)
        if (
            ot == "document"
            and src == "presentation"
            and _is_translate_query(query)
            and _wants_modify_uploaded_file(query, ot, metadata)
        ):
            if log_fn:
                log_fn("Mode resolver: generation (translate pptx → docx export)")
            return "generation"
        if log_fn:
            log_fn("Mode resolver: generation (output format differs from source file)")
        return "generation"

    if file_count == 1 and _source_matches_output(metadata, ot):
        if _wants_modify_uploaded_file(query, ot, metadata):
            if log_fn:
                log_fn("Mode resolver: mutation (in-place edit of uploaded file)")
            return "mutation"
        if log_fn:
            log_fn("Mode resolver: generation (use source to create new content)")
        return "generation"

    if file_count > 1:
        if model:
            llm_mode = infer_mode_llm(metadata, ot, model=model, log_fn=log_fn)
            if llm_mode:
                return llm_mode
        if _wants_modify_uploaded_file(query, ot, metadata) and _source_matches_output(metadata, ot):
            if log_fn:
                log_fn("Mode resolver: mutation (multi-file, modify matching upload)")
            return "mutation"
        if log_fn:
            log_fn("Mode resolver: generation (multi-file source)")
        return "generation"

    if proposed_mode == "mutation":
        return "mutation"

    if model:
        llm_mode = infer_mode_llm(metadata, ot, model=model, log_fn=log_fn)
        if llm_mode:
            return llm_mode

    return proposed_mode if proposed_mode in ("generation", "mutation") else "generation"
