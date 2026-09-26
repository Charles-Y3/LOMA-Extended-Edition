# tools/output_generator.py
# -*- coding: utf-8 -*-
import os
import json
import re
import shutil
from typing import Optional

# Cross-module import bindings for the specialized document surgery tool
from pipeline.output_format import EXTENSION_BY_TYPE
from services.office_mutation import mutate_office_file
from pipeline.i18n import t as _tr  # noqa: E402

try:
    from docx import Document
except ImportError:
    Document = None

try:
    from pptx import Presentation
except ImportError:
    Presentation = None

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

try:
    from docx.shared import Inches as DocxInches
except ImportError:
    DocxInches = None

try:
    from pptx.util import Inches as PptxInches
except ImportError:
    PptxInches = None

GENERATED_DIR = os.path.join('data', 'generated')
os.makedirs(GENERATED_DIR, exist_ok=True)

# Two marker shapes seen in the wild: "[IMAGE_PROMPT: desc]" (what the system prompt
# asks for) and "[IMAGE_PROMPT]: desc" (colon after the bracket instead of inside it —
# a common LLM deviation, especially models that treat it like a markdown link
# reference). Group 1 is a colon-inside match; group 2 is a colon-after match, whose
# description runs to end of line since there's no closing bracket to anchor on.
IMAGE_MARKER_RE = re.compile(
    r"\[IMAGE_PROMPT:\s*(.+?)\]|\[IMAGE_PROMPT\]:?\s*(.+)$",
    re.IGNORECASE,
)
MARKDOWN_FILE_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _resolve_markdown_image_path(raw: str) -> str | None:
    path = (raw or "").strip().strip("\"'")
    if not path:
        return None
    if os.path.isfile(path):
        return path
    for base in (GENERATED_DIR, os.getcwd()):
        candidate = os.path.join(base, path)
        if os.path.isfile(candidate):
            return candidate
    return None


def _extract_file_image_path(line: str) -> str | None:
    match = MARKDOWN_FILE_IMAGE_RE.search(line or "")
    if not match:
        return None
    return _resolve_markdown_image_path(match.group(2))


def sanitize_llm_text(text: str) -> str:
    """
    Strips out generic AI disclaimers, excuses about limitations,
    and instructions on how to save files manually.
    """
    patterns = [
        r"(?i)I'm sorry,? but I (cannot|can't) (generate|send|create|save) files.*?\n",
        r"(?i)However, I can provide the (full )?text.*?\n",
        r"(?i)Here is the story,? ready for use:?\\n",
        r"(?i)✅ How to save this.*",
        r"(?i)Copy the entire text.*",
        r"(?i)Open Microsoft Word.*",
        r"(?i)Paste the text.*",
        r"(?i)Save the file as:?.*",
        r"(?i)Let me know if you'd like me to generate.*"
    ]

    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)

    cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
    return cleaned.strip()


_SLIDE_LABEL_RE = re.compile(
    r"^#{0,6}\s*slide\s+\d+\s*[:.\-–—]?\s*(.*)$", re.IGNORECASE
)
_HR_ONLY_RE = re.compile(r"^(-{3,}|\*{3,}|_{3,})$")


def strip_slide_deck_artifacts(text: str) -> str:
    """Weak local models sometimes ignore the 'no slide markers' output rule and structure a
    document as a deck ('Slide 1: Cover Page', '--- Slide N ---' separators). Deterministically
    strip those artifacts before .docx compilation rather than relying on prompt adherence alone.
    """
    out_lines = []
    for line in (text or "").split("\n"):
        stripped = line.strip()
        if _HR_ONLY_RE.match(stripped):
            continue
        m = _SLIDE_LABEL_RE.match(stripped)
        if m:
            rest = m.group(1).strip()
            if rest:
                out_lines.append(f"## {rest}")
            continue
        out_lines.append(line)
    return "\n".join(out_lines)


_EXEC_SUMMARY_HEADING_RE = re.compile(r"^(executive summary|abstract)s?\s*:?$", re.I)
_COVER_OFF_RE_EN = re.compile(
    r"\b(no|without|skip|remove|don'?t\s+(?:want|need))\b[^.\n]{0,20}\bcover\s*(page)?\b", re.I
)
_TOC_OFF_RE_EN = re.compile(
    r"\b(no|without|skip|remove|don'?t\s+(?:want|need))\b[^.\n]{0,25}"
    r"\b(toc|table\s+of\s+contents|contents?\s*page|content\s*page)\b",
    re.I,
)
_CONTENT_ONLY_RE_EN = re.compile(r"\b(just|only)\b[^.\n]{0,15}\bcontent\b", re.I)


def resolve_document_structure(query: str) -> tuple[bool, bool]:
    """Return (want_cover, want_toc) for a document report — both default True (full report
    structure: cover, executive summary, contents, content) unless the user's query opts out."""
    from pipeline.query_intent_i18n import matches

    q = query or ""
    want_cover = not (_COVER_OFF_RE_EN.search(q) or matches(q, "cover_off"))
    want_toc = not (_TOC_OFF_RE_EN.search(q) or matches(q, "toc_off"))
    if _CONTENT_ONLY_RE_EN.search(q) or matches(q, "content_only"):
        want_cover = False
        want_toc = False
    return want_cover, want_toc


_COUNT_CLAIM_WINDOW = 120
_COUNT_CLAIM_UNIT = r"posts?|rows?|records?|entries|items"


def correct_category_count_claims(text: str, computed_stats: list[dict]) -> str:
    """Fix '<Category> ... (<N> posts)' style claims where the model attaches the wrong count
    to a named category (e.g. giving Instagram's real count to Facebook) — a transcription slip
    that survives even when the correct 'Category breakdowns' table is present elsewhere in the
    report, because this specific sentence is free prose the model wrote from memory rather than
    copied from the table. Only touches a category name immediately followed by an explicit
    count-with-unit mention; leaves everything else untouched."""
    count_by_category: dict[str, int] = {}
    for entry in computed_stats or []:
        for agg in entry.get("group_aggregates") or []:
            for cat, n in (agg.get("count") or {}).items():
                count_by_category[str(cat)] = n
    if not count_by_category:
        return text

    # Longest-first so e.g. "Facebook" isn't shadowed by a shorter category name substring.
    categories = sorted(count_by_category, key=len, reverse=True)
    cat_alt = "|".join(re.escape(c) for c in categories)
    pattern = re.compile(
        rf"\b(?P<cat>{cat_alt})\b(?P<gap>.{{0,{_COUNT_CLAIM_WINDOW}}}?)"
        rf"(?P<num>\d[\d,]*)\s*(?P<unit>{_COUNT_CLAIM_UNIT})\b",
        re.IGNORECASE | re.DOTALL,
    )
    lookup = {c.lower(): (c, n) for c, n in count_by_category.items()}

    def _fix(m: re.Match) -> str:
        canonical, correct = lookup.get(m.group("cat").lower(), (None, None))
        if correct is None:
            return m.group(0)
        raw_num = m.group("num")
        try:
            num = int(raw_num.replace(",", ""))
        except ValueError:
            return m.group(0)
        if num == correct:
            return m.group(0)
        new_num = f"{correct:,}" if "," in raw_num else str(correct)
        start = m.start()
        return (
            m.group(0)[: m.start("num") - start] + new_num + m.group(0)[m.end("num") - start :]
        )

    return pattern.sub(_fix, text)


_R_VALUE_RE = re.compile(r"\br\s*=\s*(-?\d*\.\d+)", re.IGNORECASE)
_CORR_TOLERANCE = 0.001


def flag_unverified_correlation_claims(
    text: str, computed_stats: list[dict], *, log_fn=None
) -> list[str]:
    """Detect 'r = X.XXX' correlation claims and check them against computed correlation pairs.

    Detect-only: returns warning strings (and passes them to log_fn) rather than rewriting the
    text. Silently rewriting an attribution sentence risks producing a grammatically broken or
    still-wrong claim (e.g. the sentence also names a category, like 'TikTok', that was never
    part of any computed correlation at all — there's no safe substitution for that, only a
    flag that a human or a revision pass should look at).

    Which pair a claim is "about" is resolved per-paragraph, preferring the paragraph's own
    topic phrase (e.g. 'Relationship Between Impressions and Likes: ...') over nearest-word
    proximity — a wrong-but-plausible mention elsewhere in the same paragraph (e.g. a stray
    'reach' inside an unrelated sentence) can otherwise masquerade as the claimed pair.
    """
    warnings: list[str] = []
    known_pairs: list[tuple[str, str, float]] = []
    numeric_cols: set[str] = set()
    for entry in computed_stats or []:
        numeric_cols.update(str(c) for c in entry.get("numeric_columns") or [])
        for p in entry.get("top_correlations") or []:
            known_pairs.append((p["a"], p["b"], p["r"]))
        joined = entry.get("joined") or {}
        for p in joined.get("cross_dataset_correlations") or []:
            known_pairs.append((p["a"], p["b"], p["r"]))
    if not known_pairs or not numeric_cols:
        return warnings

    canon = {c.lower(): c for c in numeric_cols}
    col_alt = "|".join(re.escape(c) for c in sorted(numeric_cols, key=len, reverse=True))
    col_finder = re.compile(rf"\b({col_alt})\b", re.IGNORECASE)

    def _ordered_names(segment: str) -> list[str]:
        seen: list[str] = []
        for mm in col_finder.finditer(segment):
            name = canon.get(mm.group(1).lower(), mm.group(1))
            if name not in seen:
                seen.append(name)
        return seen

    for para in re.split(r"\n\s*\n", text):
        for m in _R_VALUE_RE.finditer(para):
            r_val = float(m.group(1))
            before = para[: m.start()]
            matches_any_pair = [p for p in known_pairs if abs(p[2] - r_val) < _CORR_TOLERANCE]
            if not matches_any_pair:
                warnings.append(
                    f"Unverified correlation claim: 'r = {r_val}' near \"...{before[-80:]}\" "
                    "does not match any computed correlation."
                )
                continue

            topic_phrase = re.split(r"[:.\n]", para, maxsplit=1)[0]
            topic_names = _ordered_names(topic_phrase)
            if len(topic_names) == 2:
                mentioned = topic_names
            else:
                nearest: list[str] = []
                for name in reversed(_ordered_names(before)):
                    if name not in nearest:
                        nearest.append(name)
                    if len(nearest) == 2:
                        break
                mentioned = list(reversed(nearest))

            if len(mentioned) == 2:
                claimed = {mentioned[0].lower(), mentioned[1].lower()}
                if not any({p[0].lower(), p[1].lower()} == claimed for p in matches_any_pair):
                    real_pair = matches_any_pair[0]
                    warnings.append(
                        f"Correlation misattribution: text claims r = {r_val} for "
                        f"{mentioned[0]} & {mentioned[1]}, but that r-value actually belongs "
                        f"to {real_pair[0]} vs {real_pair[1]}."
                    )
    if log_fn:
        for w in warnings:
            log_fn(f"[fact-check] {w}")
    return warnings


def _strip_image_markers(text: str) -> str:
    return IMAGE_MARKER_RE.sub("", text).strip()


def _extract_image_prompt(line: str) -> Optional[str]:
    match = IMAGE_MARKER_RE.search(line or "")
    if not match:
        return None
    return (match.group(1) or match.group(2) or "").strip()


def _debug_log(message: str, data: dict) -> None:
    # #region agent log
    try:
        import time as _time

        with open("debug-6f9f14.log", "a", encoding="utf-8") as _lf:
            _lf.write(
                json.dumps(
                    {
                        "sessionId": "6f9f14",
                        "hypothesisId": "IMG",
                        "location": "output_generator.py",
                        "message": message,
                        "data": data,
                        "timestamp": int(_time.time() * 1000),
                    }
                )
                + "\n"
            )
    except Exception:
        pass
    # #endregion


def _safe_state_log(state, message: str) -> None:
    try:
        from services.session.state import ORCHESTRA_LOG_MAX

        state.orchestra_log.append(message)
        if len(state.orchestra_log) > ORCHESTRA_LOG_MAX:
            state.orchestra_log.pop(0)
        import ui.themes as themes

        if getattr(themes, "log_inner", None) is not None:
            ui_module = state.get_ui_module()
            if hasattr(ui_module, "render_logs") and hasattr(ui_module.render_logs, "refresh"):
                ui_module.render_logs.refresh()
    except Exception:
        pass


def _generate_image_for_marker(
    prompt: str, stem: str, index: int, *,
    prof: dict | None = None, model: str | None = None,
    source_text: str = "", settings: dict | None = None,
) -> str | None:
    """Dispatches through services.marker_visual.resolve_marker_visual() — the
    marker's own text decides plain photo vs. diagram vs. infographic. `prof`/
    `model` are optional: without them this always falls back to a plain photo
    (diagram/infographic authoring needs an LLM call), so callers that don't have a
    profile in scope keep today's behavior. `source_text`/`settings` resolve
    grounding for a diagram/infographic's facts (pipeline/base/grounding.py) —
    `source_text` is normally the document/slide body already drafted, so an
    embedded diagram stays consistent with whatever the surrounding report says."""
    from services.marker_visual import resolve_marker_visual

    output_path = os.path.join(GENERATED_DIR, "images", f"{stem}_image_{index}.png")
    path, _sources = resolve_marker_visual(
        prompt, output_path=output_path, prof=prof, model=model,
        source_text=source_text, settings=settings,
        log_fn=lambda m: _debug_log("marker visual", {"message": m}),
    )
    if not path:
        from services.model_router import image_generation_deps_available

        ok, _ = image_generation_deps_available()
        if not ok:
            from services.capability.gap_handler import offer_image_gen_installer

            offer_image_gen_installer()
    _debug_log(
        "embedded image generated",
        {"path": os.path.basename(path) if path else None, "prompt_len": len(prompt)},
    )
    return path


def _set_docx_picture_alt_text(shape, alt_text: str) -> None:
    """Sets screen-reader alt text on a just-inserted docx picture. Reuses the
    marker's own authoring prompt text as the alt text — it's already a decent
    one-line description of the image and costs no extra LLM call. python-docx
    doesn't expose this as a kwarg/property; `docPr` is the underlying <wp:docPr>
    element's own name/descr attributes, reachable from InlineShape._inline."""
    text = (alt_text or "").strip()
    if not text or shape is None:
        return
    try:
        shape._inline.docPr.set("descr", text[:250])
    except Exception:
        pass


def _flush_docx_table_lines(doc, table_lines: list[str]) -> None:
    """Compile buffered markdown pipe-table lines (e.g. '| a | b |') into a real Word table."""
    from services.markdown_inline import add_inline_markdown_runs

    rows = []
    for raw in table_lines:
        if re.match(r"^\|\s*:?-+:?\s*(\|\s*:?-+:?\s*)+\|$", raw):
            continue
        rows.append([c.strip() for c in raw.strip("|").split("|")])
    if not rows:
        return
    n_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=n_cols)
    table.style = "Table Grid"
    for r_idx, row in enumerate(rows):
        for c_idx in range(n_cols):
            cell_para = table.cell(r_idx, c_idx).paragraphs[0]
            add_inline_markdown_runs(cell_para, row[c_idx] if c_idx < len(row) else "")
            if r_idx == 0:
                for run in cell_para.runs:
                    run.bold = True


def _render_docx_markdown_lines(
    doc, lines: list[str], stem: str, image_counter: list[int],
    *, prof: dict | None = None, model: str | None = None,
    source_text: str = "", settings: dict | None = None,
) -> None:
    """Render a block of markdown lines (images, tables, headings, bullets, paragraphs) into doc."""
    from services.markdown_inline import (
        add_docx_heading_with_inline,
        add_docx_paragraph_with_inline,
        parse_markdown_heading,
    )

    table_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
            table_lines.append(stripped)
            continue
        if table_lines:
            _flush_docx_table_lines(doc, table_lines)
            table_lines = []
        if not stripped:
            continue
        file_image = _extract_file_image_path(stripped)
        if file_image and DocxInches:
            doc.add_picture(file_image, width=DocxInches(5.8))
            continue
        image_prompt = _extract_image_prompt(stripped)
        if image_prompt:
            image_counter[0] += 1
            from services.model_router import any_image_model_installed

            if not any_image_model_installed():
                # Skip the doomed generation attempt entirely (same reasoning as
                # presentation_compile.py's equivalent check) and say why, instead of
                # the generic "[Image prompt: ...]" fallback used for other failures.
                doc.add_paragraph(_tr("doc.image_prompt_no_model", prompt=image_prompt))
                continue
            image_path = _generate_image_for_marker(
                image_prompt, stem, image_counter[0], prof=prof, model=model,
                source_text=source_text, settings=settings,
            )
            if image_path and DocxInches:
                shape = doc.add_picture(image_path, width=DocxInches(5.8))
                _set_docx_picture_alt_text(shape, image_prompt)
            else:
                doc.add_paragraph(_tr("doc.image_prompt", prompt=image_prompt))
            continue
        stripped = _strip_image_markers(stripped)
        if not stripped:
            continue
        heading_level, heading_text = parse_markdown_heading(stripped)
        if heading_level is not None:
            add_docx_heading_with_inline(doc, heading_text, heading_level)
        elif stripped.startswith('* ') or stripped.startswith('- '):
            add_docx_paragraph_with_inline(doc, stripped[2:].strip(), style='List Bullet')
        else:
            add_docx_paragraph_with_inline(doc, stripped)
    if table_lines:
        _flush_docx_table_lines(doc, table_lines)


def _extract_cover_title(lines: list[str]) -> tuple[str | None, str | None, list[str]]:
    """Pull the report title (and an optional subtitle line right after it) off the front of the
    document so they can be rendered as a dedicated cover page instead of inline body text."""
    from services.markdown_inline import parse_markdown_heading

    idx = next((i for i, l in enumerate(lines) if l.strip()), None)
    if idx is None:
        return None, None, lines
    first = lines[idx].strip()
    level, text = parse_markdown_heading(first)
    title = text if level is not None else first
    consumed = {idx}
    j = next((k for k in range(idx + 1, len(lines)) if lines[k].strip()), None)
    subtitle = None
    if j is not None:
        cand = lines[j].strip()
        cand_level, _ = parse_markdown_heading(cand)
        is_structural = (
            cand_level is not None
            or cand.startswith(('* ', '- ', '|', '>'))
            or _HR_ONLY_RE.match(cand)
        )
        if not is_structural:
            subtitle = cand
            consumed.add(j)
    remaining = [l for i, l in enumerate(lines) if i not in consumed]
    return title, subtitle, remaining


def _extract_executive_summary(lines: list[str]) -> tuple[list[str], list[str]]:
    """Pull an 'Executive Summary'/'Abstract' section (if present) out of body order so it can be
    placed right after the cover, ahead of the contents page — matching standard report structure."""
    from services.markdown_inline import parse_markdown_heading

    for i, raw in enumerate(lines):
        level, text = parse_markdown_heading(raw.strip())
        if level is None or not _EXEC_SUMMARY_HEADING_RE.match(text.strip()):
            continue
        end = len(lines)
        for j in range(i + 1, len(lines)):
            level2, _ = parse_markdown_heading(lines[j].strip())
            if level2 is not None and level2 <= level:
                end = j
                break
        return lines[i:end], lines[:i] + lines[end:]
    return [], lines


def _add_toc_field(paragraph) -> None:
    """Insert a real Word TOC field (References > Table of Contents equivalent) bound to
    Heading 1-3 styles. Word computes entries/page numbers when the user updates the field
    (F9, or the "Update Field" prompt Word shows on open) — python-docx cannot pre-compute
    page numbers itself, so this is the standard OOXML approach for a real, refreshable TOC."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    run = paragraph.add_run()
    fld_begin = OxmlElement('w:fldChar')
    fld_begin.set(qn('w:fldCharType'), 'begin')
    instr = OxmlElement('w:instrText')
    instr.set(qn('xml:space'), 'preserve')
    instr.text = 'TOC \\o "1-3" \\h \\z \\u'
    fld_sep = OxmlElement('w:fldChar')
    fld_sep.set(qn('w:fldCharType'), 'separate')
    placeholder = OxmlElement('w:t')
    placeholder.text = _tr("doc.toc_placeholder")
    fld_end = OxmlElement('w:fldChar')
    fld_end.set(qn('w:fldCharType'), 'end')
    r = run._r
    r.append(fld_begin)
    r.append(instr)
    r.append(fld_sep)
    r.append(placeholder)
    r.append(fld_end)


def _pretranslate_marker_prompts(markdown_text: str) -> None:
    """One up-front LLM call translating every image marker in the document, so the per-image
    generation loop never calls the LLM (which would evict the loaded image pipeline each
    time — see services.image_generation.pretranslate_prompts). No-op for English text."""
    try:
        from services.image_generation import pretranslate_prompts

        prompts = [p for p in map(_extract_image_prompt, (ln.strip() for ln in markdown_text.splitlines())) if p]
        if prompts:
            pretranslate_prompts(prompts)
    except Exception:
        pass


def build_docx_from_markdown(
    markdown_text: str,
    filepath: str,
    *,
    want_cover: bool = True,
    want_toc: bool = True,
    dataset_overview_md: str = "",
    prof: dict | None = None,
    model: str | None = None,
    settings: dict | None = None,
) -> str:
    """Fallback compiler to generate a Word Document from scratch when no template exists.
    Returns the path actually written — a `.txt` when python-docx is unavailable.

    Default full report structure: cover page, executive summary (if the draft has one), a
    deterministic Dataset Overview (if a spreadsheet was profiled — code-generated, not
    LLM-authored, so it can't misstate a computed fact), a real Word contents page, then the
    body. `want_cover`/`want_toc` let a caller drop either section when the user explicitly
    asked for a different structure.
    """
    if not Document:
        txt_path = filepath.replace(".docx", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(markdown_text)
        return txt_path

    from services.model_router import any_image_model_installed as _img_ok

    if _img_ok():
        _pretranslate_marker_prompts(markdown_text)

    doc = Document()
    try:
        from docx.enum.section import WD_ORIENT

        for section in doc.sections:
            section.orientation = WD_ORIENT.PORTRAIT
    except Exception:
        pass

    from services.markdown_inline import add_inline_markdown_runs

    lines = markdown_text.split('\n')
    stem = os.path.splitext(os.path.basename(filepath))[0]
    image_counter = [0]

    title_text = subtitle_text = None
    if want_cover:
        title_text, subtitle_text, lines = _extract_cover_title(lines)

    exec_block: list[str] = []
    if want_cover or want_toc:
        exec_block, lines = _extract_executive_summary(lines)

    if want_cover and title_text:
        title_heading = doc.add_heading("", level=0)
        add_inline_markdown_runs(title_heading, title_text)
        if subtitle_text:
            subtitle_para = doc.add_paragraph()
            add_inline_markdown_runs(subtitle_para, subtitle_text)
        doc.add_page_break()

    if exec_block:
        _render_docx_markdown_lines(
            doc, exec_block, stem, image_counter, prof=prof, model=model,
            source_text=markdown_text, settings=settings,
        )

    if dataset_overview_md.strip():
        _render_docx_markdown_lines(
            doc, dataset_overview_md.split('\n'), stem, image_counter, prof=prof, model=model,
            source_text=markdown_text, settings=settings,
        )

    if want_toc:
        doc.add_heading(_tr("doc.contents"), level=1)
        _add_toc_field(doc.add_paragraph())
        doc.add_page_break()

    _render_docx_markdown_lines(
        doc, lines, stem, image_counter, prof=prof, model=model,
        source_text=markdown_text, settings=settings,
    )

    doc.save(filepath)
    return filepath


def build_pptx_from_markdown(
    markdown_text: str, filepath: str, *,
    prof: dict | None = None, model: str | None = None, settings: dict | None = None,
) -> str:
    """Fallback compiler to generate a PowerPoint slide deck from scratch when no template exists.
    Returns the path actually written — a `.txt` when python-pptx is unavailable."""
    if not Presentation:
        txt_path = filepath.replace(".pptx", ".txt")
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(markdown_text)
        return txt_path

    from services.presentation_markdown import (
        normalize_presentation_markdown,
        parse_speaker_notes_line,
        partition_slide_extras,
        sanitize_bullet_text,
        sanitize_slide_title,
        split_presentation_slides,
    )

    prs = Presentation()
    title_layout = prs.slide_layouts[0]
    content_layout = prs.slide_layouts[1]
    normalized = normalize_presentation_markdown(markdown_text)
    slides_content = split_presentation_slides(normalized)
    if not slides_content and normalized.strip():
        slides_content = [normalized.strip()]
    image_count = 0
    stem = os.path.splitext(os.path.basename(filepath))[0]
    from services.model_router import any_image_model_installed

    # Fallback compiler has no log_fn to surface a "download one from Model Library"
    # note through (unlike compile_agentic_presentation's main path) — just skip the
    # doomed per-slide generation attempts rather than silently failing on each one.
    skip_images = not any_image_model_installed()
    if not skip_images:
        _pretranslate_marker_prompts(markdown_text)

    for slide_idx, slide_data in enumerate(slides_content):
        lines = [line.strip() for line in slide_data.split('\n') if line.strip()]
        if not lines:
            continue

        title_text = ""
        subtitle_text = ""
        bullet_points = []
        image_prompts = []
        speaker_notes = ""

        for line in lines:
            if re.match(r"^---\s*Slide\s+\d+\s*---\s*$", line, re.IGNORECASE):
                continue
            notes_line = parse_speaker_notes_line(line)
            if notes_line:
                speaker_notes = (
                    f"{speaker_notes}\n\n{notes_line}".strip()
                    if speaker_notes
                    else notes_line
                )
                continue
            img_m = re.match(r"^\[IMAGE:\s*(.+?)\]\s*$", line, re.I)
            if img_m:
                image_prompts.append(img_m.group(1).strip())
                continue
            image_prompt = _extract_image_prompt(line)
            if image_prompt:
                image_prompts.append(image_prompt)
                line = _strip_image_markers(line)
                if not line:
                    continue
            if line.startswith('# ') and not title_text:
                title_text = sanitize_slide_title(re.sub(r'^#+\s+', '', line).strip())
                continue
            if line.startswith('## '):
                title_text = sanitize_slide_title(re.sub(r'^#+\s+', '', line).strip())
                continue
            if line.startswith('* ') or line.startswith('- '):
                bullet_points.append(sanitize_bullet_text(line[2:].strip()))
            elif not title_text:
                title_text = sanitize_slide_title(line)
            else:
                bullet_points.append(sanitize_bullet_text(line))

        bullet_points, extra_image, extra_notes = partition_slide_extras(bullet_points)
        from pipeline.deck_i18n import localize_standard_title

        title_text = localize_standard_title(title_text)
        if extra_image:
            image_prompts.append(extra_image)
        if extra_notes:
            speaker_notes = (
                f"{speaker_notes}\n\n{extra_notes}".strip()
                if speaker_notes
                else extra_notes
            )

        from services.markdown_inline import set_pptx_paragraph_inline

        use_title_layout = slide_idx == 0 and bool(title_text) and len(bullet_points) <= 1
        if use_title_layout and len(bullet_points) == 1:
            subtitle_text = bullet_points[0]
            bullet_points = []

        slide_layout = title_layout if use_title_layout else content_layout
        slide = prs.slides.add_slide(slide_layout)

        if slide.shapes.title:
            title_shape = slide.shapes.title
            if title_text:
                set_pptx_paragraph_inline(title_shape.text_frame.paragraphs[0], title_text)
            else:
                title_shape.text = _tr("deck.slide_section")

        if use_title_layout and subtitle_text and len(slide.placeholders) > 1 and slide.placeholders[1]:
            set_pptx_paragraph_inline(slide.placeholders[1].text_frame.paragraphs[0], subtitle_text)
        elif bullet_points and len(slide.placeholders) > 1 and slide.placeholders[1]:
            tf = slide.placeholders[1].text_frame
            tf.clear()
            for i, bp in enumerate(bullet_points):
                p = tf.add_paragraph() if i > 0 else tf.paragraphs[0]
                set_pptx_paragraph_inline(p, bp)
                p.level = 0

        placed_side_image = False
        for image_prompt in image_prompts:
            image_count += 1
            image_path = None
            if not skip_images:
                image_path = _generate_image_for_marker(
                    image_prompt, stem, image_count, prof=prof, model=model,
                    source_text=markdown_text, settings=settings,
                )
            if image_path and PptxInches:
                try:
                    from pipeline.deliverables.presentation_compile import (
                        _add_slide_image,
                        _apply_left_text_column,
                    )

                    if not placed_side_image:
                        _apply_left_text_column(
                            slide, PptxInches, title_layout=use_title_layout
                        )
                        placed_side_image = True
                    _add_slide_image(slide, image_path, PptxInches, position="right", alt_text=image_prompt)
                except Exception:
                    slide.shapes.add_picture(
                        image_path, PptxInches(5.45), PptxInches(1.35), width=PptxInches(3.9)
                    )

        if speaker_notes:
            try:
                tf = slide.notes_slide.notes_text_frame
                tf.clear()
                tf.text = speaker_notes.strip()
            except Exception:
                pass

    prs.save(filepath)
    return filepath


def _convert_image_to_jpg(source_path: str, dest_path: str) -> str:
    if not Image or not os.path.exists(source_path):
        return source_path
    try:
        with Image.open(source_path) as img:
            rgb = img.convert("RGB")
            rgb.save(dest_path, "JPEG", quality=90)
        if source_path != dest_path and source_path.lower().endswith(".png"):
            try:
                os.remove(source_path)
            except OSError:
                pass
        return dest_path
    except Exception:
        return source_path


def build_image_artifact(content: str, filepath: str):
    """Generates a styled, high-fidelity PNG chart/diagram or card layout based on the text."""
    if not Image:
        with open(filepath.replace(".png", ".txt"), "w", encoding="utf-8") as f:
            f.write(f"[IMAGE DESCRIPTION LOG]\n\n{content}")
        return

    img = Image.new('RGB', (1200, 675), color='#0f0f13')
    draw = ImageDraw.Draw(img)

    draw.rectangle([50, 50, 1150, 625], outline='#2a2b36', width=4)
    draw.chord([900, 100, 1100, 300], start=0, end=360, fill='#1e3a8a')
    draw.rectangle([950, 400, 1050, 500], fill='#701a75')

    lines = content.split('\n')
    y_text = 100
    for line in lines[:15]:
        clean_line = line.strip()
        if clean_line:
            draw.text((100, y_text), clean_line, fill='#e2e8f0')
            y_text += 35

    img.save(filepath)


def build_software_artifact(content: str, filepath: str):
    """Cleans markdown syntax markers and writes pure programmatic workspace scripts."""
    code_content = content
    if "```" in content:
        blocks = content.split("```")
        for block in blocks:
            if len(block.strip()) > 30:
                lines = block.split('\n')
                if len(lines) > 1 and not lines[0].isalnum():
                    code_content = "\n".join(lines[1:])
                else:
                    code_content = "\n".join(lines)
                break

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(code_content.strip())


def _copy_ready_artifact_if_unchanged(compiled_path: str, expected_fingerprint: str | None = None) -> str | None:
    """Return compiled_path when a ready on-disk artifact can be copied instead of
    re-synthesizing. When expected_fingerprint is given, the cached artifact must
    also match it (see state.artifact_fingerprint) — artifact_ready/preview_dirty
    alone only say "something is cached", not "this request's content is what's
    cached", so without this check a stale file from an unrelated request/source
    would get silently copied in as this request's result."""
    from services.session import state

    existing = state.last_generated_file_path
    if not (
        existing
        and os.path.exists(existing)
        and state.artifact_ready
        and not state.preview_dirty
    ):
        return None
    if expected_fingerprint is not None and state.last_artifact_fingerprint != expected_fingerprint:
        return None
    if os.path.normcase(existing) != os.path.normcase(compiled_path):
        shutil.copy2(existing, compiled_path)
    _debug_log(
        "office export copied existing",
        {"path": os.path.basename(compiled_path)},
    )
    return compiled_path


def generate_output(output_type: str, content: str, original_filename: str, gen_model: str):
    """
    Consolidates raw LLM outputs, resolves correct names/formats, and generates local files.
    Routes presentation and document processing through modify_report if a template exists.
    """
    if not content or not content.strip():
        return None

    clean_text = sanitize_llm_text(content)

    source_file_path = None
    from services.session import state
    from services.session.artifact import request_fingerprint

    # Generation-mode requests only ever reach here (mutation goes through
    # mutate_office_file directly) — same formula the caller (compile_artifact/
    # save_preview_to_artifact) used to fingerprint this request before deciding
    # whether to reuse a cached artifact, so the two checks agree.
    artifact_fp = request_fingerprint("generation", output_type, original_filename, content=content)
    if state.active_context_files:
        for f in state.active_context_files:
            if f.get('filename') == original_filename:
                candidate_path = os.path.join('data', 'uploads', original_filename)
                if os.path.exists(candidate_path):
                    source_file_path = candidate_path
                    break

    # Clean name markers
    base_name = original_filename if original_filename else "output"
    for suffix in ["_en", "_zh", "_loma", "_LOMA", "_loma", "_LOMA"]:
        if base_name.endswith(suffix):
            base_name = base_name[:-len(suffix)]

    if "." in base_name:
        base_name = os.path.splitext(base_name)[0]

    ext_mapping = dict(EXTENSION_BY_TYPE)
    # "image" already resolves to ".png" via EXTENSION_BY_TYPE — the canonical
    # format (lossless, alpha-channel support for Artwork Studio's Composite
    # mode). No .jpg default here.

    detected_ext = ext_mapping.get(output_type, ".txt")
    filename = f"{base_name}_LOMA{detected_ext}"
    compiled_path = os.path.join(GENERATED_DIR, filename)

    try:
        # Route through modify_report when classified as mutation and template is present
        from services.session import draft as draft_sync, state as loma_state
        from pipeline.base.profile_pack import build_mutation_hint

        profile_id = state.current_settings.get("active_profile", "none")
        try:
            from pipeline.base import profile_pack as profile_manager
            prof = profile_manager.default_profile(profile_id)
        except Exception:
            prof = {}

        mode = loma_state.live_workspace_mode
        if (
            mode == "mutation"
            and source_file_path
            and os.path.exists(source_file_path)
            and (
                (output_type == "presentation" and source_file_path.endswith('.pptx'))
                or (output_type == "document" and source_file_path.endswith('.docx'))
            )
        ):
            profile_hint = build_mutation_hint(prof)

            _safe_state_log(
                state,
                f"[LOMA Output Engine]: Template detected at {source_file_path}. Handing off to Document Mutator.",
            )

            mutated_file_path = mutate_office_file(
                output_type=output_type,
                original_filename=source_file_path,
                model_name=gen_model,
                instruction=draft_sync.get_last_user_instruction(
                    loma_state.messages, loma_state.last_user_instruction
                ),
                profile_hint=profile_hint,
                replan=True,
            )
            if mutated_file_path and os.path.exists(mutated_file_path):
                return mutated_file_path

        if source_file_path and os.path.exists(source_file_path):
            if (output_type == "presentation" and source_file_path.endswith('.pptx')) or \
                    (output_type == "document" and source_file_path.endswith('.docx')):
                _safe_state_log(
                    state,
                    "[LOMA Output Engine]: Office template present but mode is not mutation — "
                    "synthesizing from markdown draft instead of in-place edit.",
                )

        # Fallback to structural synthesis from scratch if no master template matches
        _safe_state_log(
            state,
            f"[LOMA Output Engine]: Synthesizing fresh asset payload from scratch for format: '{output_type}'.",
        )
        if output_type == "document":
            clean_text = strip_slide_deck_artifacts(clean_text)
            charts = getattr(state, "chart_artifacts", None) or []
            if charts:
                from services.graph_generation.report_layout import finalize_report_markdown

                clean_text = finalize_report_markdown(clean_text, charts)
            computed_stats = getattr(state, "dataset_computed_stats", None) or []
            if computed_stats:
                clean_text = correct_category_count_claims(clean_text, computed_stats)
                flag_unverified_correlation_claims(
                    clean_text, computed_stats, log_fn=lambda m: _safe_state_log(state, m)
                )
            copied = _copy_ready_artifact_if_unchanged(compiled_path, artifact_fp)
            if copied:
                return copied
            want_cover, want_toc = resolve_document_structure(state.last_user_instruction or "")
            compiled_path = build_docx_from_markdown(
                clean_text,
                compiled_path,
                want_cover=want_cover,
                want_toc=want_toc,
                dataset_overview_md=getattr(state, "dataset_overview_md", "") or "",
                prof=prof,
                model=gen_model,
                settings=state.current_settings,
            )
        elif output_type == "presentation":
            copied = _copy_ready_artifact_if_unchanged(compiled_path, artifact_fp)
            if copied:
                return copied
            query = (state.last_user_instruction or "").strip()
            try:
                from pipeline.deliverables.presentation_direct import prepare_direct_presentation
                from pipeline.deliverables.presentation_compile import compile_agentic_presentation

                prep = prepare_direct_presentation(clean_text, query=query)
                themed = compile_agentic_presentation(
                    prep.markdown,
                    original_filename or "presentation.pptx",
                    prep.theme,
                    query=query,
                    include_images=prep.include_images,
                    slide_visuals=prep.slide_visuals,
                    prof=prof,
                    model=gen_model,
                    settings=state.current_settings,
                    log_fn=lambda m: _safe_state_log(state, m),
                )
                if themed and os.path.exists(themed):
                    return themed
            except Exception as ex:
                _safe_state_log(state, f"Presentation themed compile fallback: {ex}")
            compiled_path = build_pptx_from_markdown(
                clean_text, compiled_path, prof=prof, model=gen_model, settings=state.current_settings,
            )
        elif output_type == "software":
            if "import react" in clean_text.lower() or "const react" in clean_text.lower():
                compiled_path = compiled_path.replace(".py", ".jsx")
            elif "import sys" in clean_text.lower() or "def " in clean_text.lower():
                compiled_path = compiled_path.replace(".py", ".py")
            elif "<html>" in clean_text.lower() or "<!doctype html>" in clean_text.lower():
                compiled_path = compiled_path.replace(".py", ".html")
            build_software_artifact(clean_text, compiled_path)
        elif output_type == "image":
            from services.image_generation import generate_image, prepare_image_prompt

            existing = state.last_generated_file_path
            if existing and os.path.exists(existing) and state.artifact_ready:
                # Copy existing image for export/download — do not regenerate when
                # the prompt draft was merely touched (preview_dirty).
                if compiled_path.lower().endswith(".jpg"):
                    compiled_path = _convert_image_to_jpg(existing, compiled_path)
                elif os.path.normcase(existing) != os.path.normcase(compiled_path):
                    shutil.copy2(existing, compiled_path)
                else:
                    compiled_path = existing
                _debug_log(
                    "image export copied existing",
                    {"path": os.path.basename(compiled_path)},
                )
                return compiled_path

            prompt_text = (state.last_image_diffusion_prompt or "").strip()
            if not prompt_text:
                prompt_text = prepare_image_prompt(clean_text)
                # Only the fallback branch above is genuinely unvetted — the common case
                # reuses last_image_diffusion_prompt, which already passed this same check
                # at original generation time (pipeline/direct/step_executor.py). Checked
                # again here anyway since it's cheap and this is a real, separate
                # generate_image() call site (found while auditing every such call site
                # for the presentation-slide-image gap — see marker_visual.py's _try_photo).
                from pipeline.image_safety_embeddings import is_explicit_prompt

                if is_explicit_prompt(prompt_text):
                    from pipeline.i18n import t as tr

                    from services.image_generation import ImageGenerationDeclinedError

                    raise ImageGenerationDeclinedError(tr("chat.image_explicit_declined"))
            wants_jpg = compiled_path.lower().endswith(".jpg")
            png_path = compiled_path[:-4] + ".png" if wants_jpg else compiled_path
            image_result = generate_image(
                prompt_text,
                output_path=png_path,
                name_hint=(state.last_user_instruction or clean_text or "")[:120],
            )
            # PNG is the canonical image format (EXTENSION_BY_TYPE) — lossless,
            # and required for downstream alpha-channel use (Artwork Studio's
            # Composite/background-removal mode). This used to unconditionally
            # convert every fresh generation to jpg regardless of what was
            # actually requested; now it only converts if the destination was
            # explicitly resolved to .jpg before generation (wants_jpg, above).
            compiled_path = image_result.path
            if wants_jpg and compiled_path.lower().endswith(".png"):
                jpg_path = compiled_path[:-4] + ".jpg"
                compiled_path = _convert_image_to_jpg(compiled_path, jpg_path)
            _debug_log(
                "standalone image generated",
                {
                    "path": os.path.basename(compiled_path),
                    "fallback": image_result.fallback,
                    "prompt_len": len(clean_text),
                },
            )
        elif output_type == "chat":
            with open(compiled_path, "w", encoding="utf-8") as f:
                f.write(clean_text)
            # #region agent log
            try:
                import json as _json
                import time as _time
                with open("debug-6f9f14.log", "a", encoding="utf-8") as _lf:
                    _lf.write(_json.dumps({"sessionId": "6f9f14", "hypothesisId": "TXT", "location": "output_generator.py:chat", "message": "plain text saved", "data": {"path": os.path.basename(compiled_path), "bytes": len(clean_text)}, "timestamp": int(_time.time() * 1000)}) + "\n")
            except Exception:
                pass
            # #endregion
        else:
            with open(compiled_path, "w", encoding="utf-8") as f:
                f.write(f"[VIDEO COMPILATION FRAME MAP METADATA]\n\n{clean_text}")

        return compiled_path

    except Exception as e:
        print(f"Output Generator critical failure: {str(e)}")
        if output_type == "image":
            raise
        try:
            fallback_txt_path = os.path.join(GENERATED_DIR, f"{base_name}_LOMA.txt")
            with open(fallback_txt_path, "w", encoding="utf-8") as f:
                f.write(clean_text)
            return fallback_txt_path
        except Exception:
            return None