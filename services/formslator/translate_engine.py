# -*- coding: utf-8
"""Translation utilities for Formslator (ported from reference/translate_utils_v2.py)."""
from __future__ import annotations

import math
import re
from typing import Callable, Dict, List, Optional

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.shared import Pt

from services.formslator.format_engine import column_indices

LogFn = Callable[[str], None]
CJK_RE = re.compile(r"[\u3400-\u9FFF\uF900-\uFAFF]")
PUNCT_RE = re.compile(r"[。！？!?；;]")

_ENGLISH_STOPWORDS = {
    "the", "and", "is", "of", "to", "that", "it", "was", "for", "with", "this",
    "are", "be", "by", "from", "or", "have", "has", "not", "but", "which",
    "you", "we", "they", "their", "there", "these", "those", "would", "could",
    "should", "will", "your", "our",
}
_ENGLISH_DISTINCT_TARGETS = {"es", "fr", "pt", "it", "vi", "id", "ms", "tl", "hi", "ar"}


def _slice_glossary_for_text(text: str, glossary: Dict[str, str], max_terms: int = 40) -> Dict[str, str]:
    if not glossary or not text:
        return {}

    out: Dict[str, str] = {}
    used_spans: list[tuple[int, int]] = []
    sorted_terms = sorted(glossary.items(), key=lambda kv: len(kv[0]), reverse=True)

    for term, tr in sorted_terms:
        pos = text.find(term)
        if pos == -1:
            continue
        end = pos + len(term)
        overlap = any(pos < e and end > s for s, e in used_spans)
        if overlap:
            continue
        out[term] = tr
        used_spans.append((pos, end))
        if len(out) >= max_terms:
            break
    return out


def _segment_paragraph(text: str, max_chars: int = 600) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    final_segments: list[str] = []
    remaining_text = text
    hard_punct = "。！？；"
    soft_punct = "，：、 "
    all_punct = hard_punct + soft_punct

    while len(remaining_text) > max_chars:
        split_at = -1
        search_end = max_chars
        for i in range(search_end - 1, max(0, search_end - 200), -1):
            if i < len(remaining_text) and remaining_text[i] in all_punct:
                split_at = i + 1
                break
        if split_at == -1:
            split_at = max_chars
        final_segments.append(remaining_text[:split_at].strip())
        remaining_text = remaining_text[split_at:].strip()

    if remaining_text:
        final_segments.append(remaining_text)
    return final_segments


def post_clean_output(text: str, source: str = "") -> str:
    if not text:
        return ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    return text.replace("<think>", "").replace("</think>", "").strip()


def clean_repetitions(text: str) -> str:
    if not text:
        return ""
    t = re.sub(r"[ \t]+", " ", text)
    t = re.sub(r"([,.!?;:])\s*\1+", r"\1 ", t)
    t = re.sub(r"\b(\w+)(\s+\1\b)+", r"\1", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def is_translation_valid(
    output: str,
    source: str,
    *,
    is_cjk_target: bool = False,
    source_code: str = "",
    target_code: str = "",
    log_fn: Optional[LogFn] = None,
) -> bool:
    if not output:
        return False

    out = output.strip()
    src_cjk = CJK_RE.findall(source)
    src_len = len(src_cjk) or 1
    out_cjk = CJK_RE.findall(out)

    if not is_cjk_target:
        max_allowed_cjk = max(1, math.ceil(src_len * 0.30))
        if len(out_cjk) > max_allowed_cjk:
            return False
    else:
        # `src_len` only means something when the SOURCE already contains CJK (e.g.
        # zh -> ja). When translating from a non-CJK source (en, es, ...) into a CJK
        # target, comparing against the source's near-zero CJK count rejects every
        # correctly translated (CJK-heavy) output outright, while an untranslated
        # passthrough (0 CJK) trivially passes — the exact inverse of what this check
        # should do. Require the output to actually BE mostly CJK, checked directly.
        non_space_len = len(out.replace(" ", "")) or 1
        cjk_ratio = len(out_cjk) / non_space_len
        if len(out) >= 8 and cjk_ratio < 0.3:
            if log_fn:
                log_fn(f"[reject: output isn't CJK ({cjk_ratio:.2f} ratio), target requires it]")
            return False
        if src_cjk and len(out_cjk) > src_len * 1.25:
            return False

    # Non-CJK targets have no character-set signal to catch a model that just echoes
    # the source untranslated (e.g. target=French but the model leaves English as-is) —
    # fall back to a similarity check when source and target are supposed to differ.
    if (
        not is_cjk_target
        and source_code
        and target_code
        and source_code != target_code
        and len(source.strip()) > 15
    ):
        import difflib

        ratio = difflib.SequenceMatcher(None, out.lower(), source.strip().lower()).ratio()
        if ratio > 0.85:
            if log_fn:
                log_fn(f"[reject: output too similar to source ({ratio:.2f}), likely untranslated]")
            return False

    # Catches the other failure shape: the model doesn't echo the source, it just
    # *answers in English regardless of the instructed target* (paraphrase, not copy),
    # which the similarity check above can't see. Only checked against target languages
    # whose function words don't overlap with English's, to avoid false positives on
    # e.g. German ("in", "an") or English itself.
    if not is_cjk_target and target_code in _ENGLISH_DISTINCT_TARGETS and len(words := out.split()) >= 6:
        hits = sum(1 for w in words if w.strip(".,!?;:\"'()").lower() in _ENGLISH_STOPWORDS)
        if hits / len(words) >= 0.15:
            if log_fn:
                log_fn(f"[reject: output looks like English, target was {target_code}]")
            return False

    words = out.split()
    if len(words) > 6:
        ratio = len(set(words)) / len(words)
        if ratio < 0.35:
            return False

    if re.search(r"(.{5,40})\1{2,}", out):
        return False

    if len(out) > len(source) * 10 and len(source) > 10:
        return False

    return True


def to_title_case(text: str) -> str:
    exceptions = [
        "a", "an", "the", "and", "but", "or", "for", "nor", "on", "at", "to", "from", "by", "of", "in", "with",
    ]
    words = text.split()
    if not words:
        return ""
    final = [words[0].capitalize()]
    for word in words[1:]:
        word_lower = word.lower()
        final.append(word_lower if word_lower in exceptions else word_lower.capitalize())
    return " ".join(final)


def clean_basic_grammar(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+([,.?!:;])", r"\1", text)
    text = re.sub(r"([,.?!:;])([^\s])", r"\1 \2", text)
    return text.strip()


def extract_nonempty_paragraphs(path: str, original_column: str = "left") -> List[str]:
    orig_idx, _ = column_indices(original_column)
    try:
        doc = Document(path)
    except Exception:
        return []

    if doc.tables:
        try:
            table = doc.tables[0]
            out: List[str] = []
            for row in table.rows:
                if len(row.cells) <= orig_idx:
                    out.append("")
                    continue
                orig_cell = row.cells[orig_idx]
                cell_lines: List[str] = []
                for p in orig_cell.paragraphs:
                    txt = p.text.strip()
                    cell_lines.append(txt if txt else "")
                text = "\n".join(cell_lines).rstrip()
                out.append(text)
            return out
        except Exception:
            pass

    paras: List[str] = []
    for p in doc.paragraphs:
        txt = p.text.strip()
        paras.append(txt if txt else "")
    return paras


def extract_nonempty_paragraphs_single_column(path: str) -> List[str]:
    """Like extract_nonempty_paragraphs, but for a document produced by
    format_document(single_column=True) — one row per (original, translation-slot)
    pair in the single-column table; pulls the original (first) paragraph of each
    row's cell."""
    try:
        doc = Document(path)
    except Exception:
        return []
    if not doc.tables:
        return []
    table = doc.tables[0]
    out: List[str] = []
    for row in table.rows:
        paras = row.cells[0].paragraphs
        txt = paras[0].text.strip() if paras else ""
        out.append(txt if txt else "")
    return out


def translate_paragraphs(
    paragraphs: List[str],
    translator,
    glossary: Dict[str, str],
    stop_event=None,
    log_fn: Optional[LogFn] = None,
    progress_callback: Optional[Callable[..., None]] = None,
    max_batch_chars: int = 550,
    vault_index=None,
    vault_flags: Optional[List[bool]] = None,
) -> List[str]:
    """Translate one paragraph at a time (matches SOFT reference behaviour)."""
    del max_batch_chars  # kept for API compatibility
    log = log_fn or (lambda *_a, **_k: None)
    results: List[str] = []
    rolling_context = ""
    vault_match_fn = None
    if vault_index is not None:
        from services.formslator.vault_alignment import ALIGN_STRICT, search_vault_translation

        def vault_match_fn(text: str) -> str | None:
            hit = search_vault_translation(
                text,
                vault_index,
                min_alignment=ALIGN_STRICT,
                allow_llm=True,
                borderline_llm=True,
            )
            return hit.translation_text if hit else None

    for idx, para in enumerate(paragraphs):
        if stop_event and stop_event.is_set():
            results.extend("" for _ in range(len(paragraphs) - len(results)))
            break

        p_strip = (para or "").strip()
        if not p_strip:
            results.append("")
            if vault_flags is not None:
                vault_flags.append(False)
            continue

        is_vault = False
        try:
            vault_text = vault_match_fn(p_strip) if vault_match_fn else None
            if vault_text:
                out = clean_repetitions(vault_text.strip())
                is_vault = True
                log(f"Vault match (skipped LLM): {p_strip[:60]}{'…' if len(p_strip) > 60 else ''}")
            else:
                translated = translator.translate(
                    p_strip,
                    glossary=glossary,
                    log_fn=log,
                    previous_context=rolling_context,
                )
                out = clean_repetitions((translated or "").strip())
            if out:
                rolling_context = out[-250:]
            results.append(out)
        except Exception as exc:
            log(f"Translation error: {exc}")
            results.append(p_strip)

        if vault_flags is not None:
            vault_flags.append(is_vault)

        if progress_callback:
            cjk_len = len(CJK_RE.findall(p_strip)) or len(p_strip)
            nonempty_done = sum(
                1 for j, p in enumerate(paragraphs)
                if j <= idx and (p or "").strip()
            )
            progress_callback(
                segment_done=1,
                char_count=cjk_len,
                original=p_strip,
                latest=results[-1] or "",
                paragraph=nonempty_done,
                total=sum(1 for p in paragraphs if (p or "").strip()),
                vault=is_vault,
            )

    while len(results) < len(paragraphs):
        results.append("")
    return results


def write_translations_to_formatted(
    formatted_doc_path: str,
    translations: list[str],
    output_doc_path: str | None = None,
    original_column: str = "left",
    vault_flags: list[bool] | None = None,
):
    from services.formslator.translation_vault_prefill import VAULT_GREEN

    orig_idx, trans_idx = column_indices(original_column)

    if output_doc_path is None:
        output_doc_path = formatted_doc_path

    doc = Document(formatted_doc_path)
    rows_all = []
    for table in doc.tables:
        for row in table.rows:
            rows_all.append(row)

    for idx, row in enumerate(rows_all):
        orig_cell = row.cells[orig_idx]
        trans_cell = row.cells[trans_idx]
        translation_text = translations[idx] if idx < len(translations) else ""
        is_vault = bool(vault_flags[idx]) if vault_flags and idx < len(vault_flags) else False

        if trans_cell.paragraphs:
            trans_para = trans_cell.paragraphs[0]
        else:
            trans_para = trans_cell.add_paragraph("")

        for r in list(trans_para.runs):
            r._element.getparent().remove(r._element)

        if translation_text:
            has_manual_break = False
            for p in orig_cell.paragraphs:
                if "<w:br" in p._element.xml:
                    has_manual_break = True
                    break
            if not has_manual_break and len(orig_cell.paragraphs) > 1:
                has_manual_break = True

            style_name = trans_para.style.name.strip() if trans_para.style else ""
            txt_to_process = translation_text
            if txt_to_process:
                if style_name and style_name.startswith("E1"):
                    txt_to_process = to_title_case(txt_to_process)
                elif len(txt_to_process) > 1:
                    txt_to_process = txt_to_process[0].upper() + txt_to_process[1:]
                elif txt_to_process:
                    txt_to_process = txt_to_process.upper()
                txt_to_process = clean_basic_grammar(txt_to_process)

            if has_manual_break:
                split_idx = -1
                if ":" in translation_text:
                    split_idx = translation_text.find(":")
                elif "：" in translation_text:
                    split_idx = translation_text.find("：")
                if split_idx != -1:
                    prefix = translation_text[: split_idx + 1]
                    rest = translation_text[split_idx + 1 :].strip()
                    r_prefix = trans_para.add_run(prefix)
                    r_prefix.add_break()
                    txt_to_process = rest

            if style_name.startswith("E4"):
                bracket_re = re.compile(r"[（(︵︽《].*?[）})︶︾》]")
                last = 0
                for m in bracket_re.finditer(txt_to_process):
                    if m.start() > last:
                        trans_para.add_run(txt_to_process[last : m.start()])
                    inside = m.group(0)
                    r_inside = trans_para.add_run(inside)
                    try:
                        for st in doc.styles:
                            if st.type == WD_STYLE_TYPE.CHARACTER and st.name.strip().startswith("E4a"):
                                r_inside.style = st
                                break
                    except Exception:
                        pass
                    last = m.end()
                if last < len(txt_to_process):
                    trans_para.add_run(txt_to_process[last:])
            else:
                run = trans_para.add_run(txt_to_process)
                if is_vault:
                    run.font.color.rgb = VAULT_GREEN
        else:
            trans_para.add_run("")

        if orig_cell.paragraphs and orig_cell.paragraphs[0].alignment is not None:
            trans_para.alignment = orig_cell.paragraphs[0].alignment

    def post_cleanup_font(table):
        for row in table.rows:
            try:
                dst_para = row.cells[trans_idx].paragraphs[0]
            except Exception:
                continue
            try:
                style_name = dst_para.style.name
            except Exception:
                style_name = None
            if style_name in ("E1 Topic Title", "E2 Heading"):
                continue
            for r in dst_para.runs:
                r.font.size = Pt(11)

    for table in doc.tables:
        post_cleanup_font(table)

    doc.save(output_doc_path)


def write_translations_single_column_styled(
    formatted_doc_path: str,
    translations: list[str],
    output_doc_path: str | None = None,
    vault_flags: list[bool] | None = None,
) -> None:
    """Paragraph-pair analog of write_translations_to_formatted: formatted_doc_path
    must be a document produced by format_document(single_column=True) — one row per
    (original, translation-slot) pair in the single-column table. Same per-style
    rules (E1 title-case, E4 bracket character-style, vault highlight, Pt(11)
    normalize except headings) applied to each row's translation-slot paragraph."""
    from services.formslator.translation_vault_prefill import VAULT_GREEN

    if output_doc_path is None:
        output_doc_path = formatted_doc_path

    doc = Document(formatted_doc_path)
    pairs = []
    if doc.tables:
        for row in doc.tables[0].rows:
            row_paras = row.cells[0].paragraphs
            if len(row_paras) >= 2:
                pairs.append((row_paras[0], row_paras[1]))

    for idx, (orig_para, trans_para) in enumerate(pairs):
        translation_text = translations[idx] if idx < len(translations) else ""
        is_vault = bool(vault_flags[idx]) if vault_flags and idx < len(vault_flags) else False

        for r in list(trans_para.runs):
            r._element.getparent().remove(r._element)

        if translation_text:
            has_manual_break = "<w:br" in orig_para._p.xml

            style_name = trans_para.style.name.strip() if trans_para.style else ""
            txt_to_process = translation_text
            if txt_to_process:
                if style_name and style_name.startswith("E1"):
                    txt_to_process = to_title_case(txt_to_process)
                elif len(txt_to_process) > 1:
                    txt_to_process = txt_to_process[0].upper() + txt_to_process[1:]
                elif txt_to_process:
                    txt_to_process = txt_to_process.upper()
                txt_to_process = clean_basic_grammar(txt_to_process)

            if has_manual_break:
                split_idx = -1
                if ":" in translation_text:
                    split_idx = translation_text.find(":")
                elif "：" in translation_text:
                    split_idx = translation_text.find("：")
                if split_idx != -1:
                    prefix = translation_text[: split_idx + 1]
                    rest = translation_text[split_idx + 1 :].strip()
                    r_prefix = trans_para.add_run(prefix)
                    r_prefix.add_break()
                    txt_to_process = rest

            if style_name.startswith("E4"):
                bracket_re = re.compile(r"[（(︵︽《].*?[）})︶︾》]")
                last = 0
                for m in bracket_re.finditer(txt_to_process):
                    if m.start() > last:
                        trans_para.add_run(txt_to_process[last : m.start()])
                    inside = m.group(0)
                    r_inside = trans_para.add_run(inside)
                    try:
                        for st in doc.styles:
                            if st.type == WD_STYLE_TYPE.CHARACTER and st.name.strip().startswith("E4a"):
                                r_inside.style = st
                                break
                    except Exception:
                        pass
                    last = m.end()
                if last < len(txt_to_process):
                    trans_para.add_run(txt_to_process[last:])
            else:
                run = trans_para.add_run(txt_to_process)
                if is_vault:
                    run.font.color.rgb = VAULT_GREEN
        elif not orig_para.text.strip():
            # A spacer row (both original and translation are empty) — format_document's
            # insert_spacer_row_single already sized this paragraph's own mark down to a
            # near-invisible line via w:pPr/w:rPr so the two spacer paragraphs together
            # read as a single-line gap. Give the fresh empty run the same explicit size
            # instead of leaving it unstyled, so the Pt(11) normalize pass below — which
            # doesn't know this row is a spacer — has nothing oversized to work with.
            run = trans_para.add_run("")
            run.font.name = "Book Antiqua"
            run.font.size = Pt(9)
        else:
            trans_para.add_run("")

        if orig_para.alignment is not None:
            trans_para.alignment = orig_para.alignment

    for _orig_para, trans_para in pairs:
        if not _orig_para.text.strip() and not trans_para.text.strip():
            continue  # spacer row — keep insert_spacer_row_single's sizing, don't normalize to Pt(11)
        try:
            style_name = trans_para.style.name
        except Exception:
            style_name = None
        if style_name in ("E1 Topic Title", "E2 Heading"):
            continue
        for r in trans_para.runs:
            r.font.size = Pt(11)

    doc.save(output_doc_path)


def write_translations_no_template(
    formatted_doc_path: str,
    translations: list[str],
    output_doc_path: str | None = None,
) -> None:
    """No-template fallback: there's no template table to slot translations into, so
    each translation is inserted as its own paragraph directly after its original,
    section by section through the document."""
    from docx.oxml import OxmlElement
    from docx.text.paragraph import Paragraph

    if output_doc_path is None:
        output_doc_path = formatted_doc_path

    def _dominant_run_font(p):
        """First run with visible text carries the paragraph's actual direct
        formatting (many templates set size/font on runs, not the paragraph style),
        so sample that one rather than falling back to the style's bare defaults."""
        for r in p.runs:
            if (r.text or "").strip():
                return r.font
        return p.runs[0].font if p.runs else None

    doc = Document(formatted_doc_path)
    orig_paragraphs = list(doc.paragraphs)

    for idx, para in enumerate(orig_paragraphs):
        translation_text = (translations[idx] if idx < len(translations) else "").strip()
        if not translation_text:
            continue
        new_p = OxmlElement("w:p")
        para._p.addnext(new_p)
        new_para = Paragraph(new_p, para._parent)
        try:
            new_para.style = para.style
        except Exception:
            pass
        run = new_para.add_run(translation_text)
        run.italic = True
        # Match the original paragraph's actual run-level formatting (size/font/bold) —
        # without this, the translation silently fell back to the paragraph style's
        # bare default size, which visibly mismatched originals using direct
        # character formatting (the common case with mapped styles/templates).
        src_font = _dominant_run_font(para)
        if src_font is not None:
            if src_font.size is not None:
                run.font.size = src_font.size
            if src_font.name:
                run.font.name = src_font.name
            if src_font.bold is not None:
                run.font.bold = src_font.bold

    doc.save(output_doc_path)


def write_translations_two_column_table(
    paragraphs: List[str],
    translations: List[str],
    output_doc_path: str,
    original_column: str = "left",
) -> None:
    """Build a fresh two-column table (original | translation), one row per paragraph.

    Used for the double-column layout when no style template produced a table to slot
    translations into — unlike write_translations_to_formatted, this doesn't require an
    existing formatted document with tables; it builds one from plain paragraph text."""
    orig_idx, trans_idx = column_indices(original_column)

    doc = Document()
    table = doc.add_table(rows=0, cols=2)
    table.style = "Table Grid"

    for i, orig_text in enumerate(paragraphs):
        if not (orig_text or "").strip():
            continue
        translation_text = translations[i] if i < len(translations) else ""
        row = table.add_row()
        cells = [row.cells[0], row.cells[1]]
        cells[orig_idx].text = orig_text or ""
        cells[trans_idx].text = translation_text or ""

    doc.save(output_doc_path)
