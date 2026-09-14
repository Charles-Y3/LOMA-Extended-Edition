# -*- coding: utf-8 -*-
"""Background job orchestration for Formslator."""
from __future__ import annotations

import os
import shutil
import threading
import time
from typing import Any, Callable

from services.formslator.format_engine import (
    clean_all_hyperlinks,
    format_document,
    insert_glossary_hyperlinks,
)
from services.formslator.glossary_service import build_glossary, build_hyperlink_index, load_config
from services.formslator.language_detect import (
    count_doc_lang_chars,
    detect_source_from_docx,
    stats_char_label,
)
from services.formslator.mapping_service import load_mapping_for_file
from services.formslator.paths import OUTPUT_DIR, STYLES_DIR, UPLOADS_DIR, ensure_dirs, resolve_writable_output_path
from services.formslator.settings import (
    get_model_sec_per_char,
    load_settings,
    record_translate_speed,
)
from services.formslator.translate_engine import (
    extract_nonempty_paragraphs,
    extract_nonempty_paragraphs_single_column,
    translate_paragraphs,
    write_translations_no_template,
    write_translations_single_column_styled,
    write_translations_to_formatted,
    write_translations_two_column_table,
)
from services.formslator.translation_vault_prefill import prefill_translations_from_vault
from services.formslator.translator import FormslatorTranslator
from pipeline.i18n import t as tr


_DEFAULT_DOUBLE_COLUMN_TEMPLATE = "default_template_v3_double_column.docx"
_DEFAULT_SINGLE_COLUMN_TEMPLATE = "default_template_v3_single_column.docx"


def _resolve_template(template_name: str | None) -> str | None:
    """Auto-defaults to the bundled default_template_v3_double_column.docx if nothing
    has been chosen yet — same fallback shape as _resolve_single_column_template."""
    if not template_name:
        settings = load_settings()
        template_name = settings.get("active_template") or ""
    if not template_name and os.path.isfile(os.path.join(STYLES_DIR, _DEFAULT_DOUBLE_COLUMN_TEMPLATE)):
        template_name = _DEFAULT_DOUBLE_COLUMN_TEMPLATE
    if not template_name:
        return None
    path = os.path.join(STYLES_DIR, template_name)
    return path if os.path.isfile(path) else None


def _resolve_single_column_template(template_name: str | None) -> str | None:
    """Same lookup as _resolve_template, but keyed to its own setting — the
    single-column layout remembers its own template independently of the
    double-column active_template — and auto-defaults to the bundled
    default_template_v3_single_column.docx if nothing has been chosen yet."""
    if not template_name:
        settings = load_settings()
        template_name = settings.get("single_column_template") or ""
    if not template_name and os.path.isfile(os.path.join(STYLES_DIR, _DEFAULT_SINGLE_COLUMN_TEMPLATE)):
        template_name = _DEFAULT_SINGLE_COLUMN_TEMPLATE
    if not template_name:
        return None
    path = os.path.join(STYLES_DIR, template_name)
    return path if os.path.isfile(path) else None


def _valid_cn_style(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and (n.startswith("C") or n.startswith("页"))


def _valid_en_style(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and (n.startswith("E") or n in ("Header", "Footer") or n.startswith("页"))


def _usable_style_mapping(
    mapping: dict | None,
    template_styles: list[str] | None = None,
) -> dict | None:
    """Return mapping entries with valid template C/E styles."""
    if not mapping:
        return None
    valid_names = set(template_styles or [])
    cleaned: dict = {}
    for sig, pair in mapping.items():
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            continue
        orig, trans = (pair[0] or "").strip(), (pair[1] or "").strip()
        if valid_names:
            if orig and orig not in valid_names:
                orig = ""
            if trans and trans not in valid_names:
                trans = ""
        if not _valid_cn_style(orig) and not _valid_en_style(trans):
            continue
        if orig or trans:
            cleaned[sig] = (orig, trans)
    return cleaned or None


def _merge_style_mapping(
    input_path: str,
    style_mapping: dict | None,
) -> dict | None:
    """Saved mapping file + session/UI mapping (session wins on conflict)."""
    merged: dict = {}
    saved, _, _ = load_mapping_for_file(input_path)
    if saved:
        merged.update(saved)
    if style_mapping:
        merged.update(style_mapping)
    return merged or None


def _count_cjk_chars(text: str) -> int:
    import re

    return len(re.findall(r"[\u3400-\u9FFF\uF900-\uFAFF]", text or ""))


def _format_duration(seconds: float) -> tuple[int, int]:
    secs = max(0, int(seconds))
    return secs // 60, secs % 60


def _bilingual_chat_block(original: str, translation: str, *, vault: bool = False) -> str:
    import html

    o = html.escape((original or "").strip())
    t_raw = (translation or "").strip()
    if vault and t_raw:
        t = f'<span style="color:#186A3B">{html.escape(t_raw)}</span>'
    else:
        t = html.escape(t_raw)
    if not o and not t_raw:
        return ""
    parts = []
    if o:
        parts.append(f"**Original**\n\n{o}")
    if t_raw:
        label = "**Translation** (vault)" if vault else "**Translation**"
        parts.append(f"{label}\n\n{t}")
    return "\n\n".join(parts)


def _post_translate_pair_to_chat(original: str, translation: str, *, vault: bool = False) -> None:
    block = _bilingual_chat_block(original, translation, vault=vault)
    if not block:
        return
    from services.session.chat_post import post_assistant_message
    from services.session.workflow_control import schedule_on_ui

    schedule_on_ui(lambda: post_assistant_message(block))


def _estimate_eta_seconds(elapsed: float, frac: float, baseline: float) -> float:
    """Inverse-proportional ETA (matches reference Translate_v2._eta_add_units)."""
    frac = max(0.0, min(1.0, frac))
    if frac < 0.05:
        return max(0.0, baseline * (1.0 - frac))
    safe_frac = max(frac, 0.0001)
    total_predicted = elapsed / safe_frac
    return max(0.0, total_predicted - elapsed)


def _copy_to_output(src_path: str, suffix: str) -> str:
    ensure_dirs()
    base = os.path.splitext(os.path.basename(src_path))[0]
    dest = resolve_writable_output_path(os.path.join(OUTPUT_DIR, f"{base}{suffix}.docx"))
    shutil.copy2(src_path, dest)
    return dest


def run_format_job(
    input_path: str,
    template_path: str,
    *,
    mode: int,
    style_mapping: dict | None = None,
    original_column: str = "left",
    single_column: bool = False,
    log_fn: Callable[[str], None] | None = None,
) -> str:
    """Mode 1=format, 2=format+glossary, 3=glossary only, 4=remove hyperlinks,
    5=format+vault prefill, 6=format+glossary+vault prefill."""
    log = log_fn or (lambda _m: None)
    ensure_dirs()
    base = os.path.splitext(os.path.basename(input_path))[0]

    if mode == 4:
        out = _copy_to_output(input_path, "_no_hyperlinks")
        clean_all_hyperlinks(out)
        log(tr("formslator.log.saved_path", path=out))
        return out

    if mode == 3:
        out = _copy_to_output(input_path, "_glossary")
        cfg = load_config()
        term_info = build_hyperlink_index(cfg)
        insert_glossary_hyperlinks(
            out,
            term_info=term_info,
            url_template=cfg.get("url_template"),
            original_column=original_column,
        )
        log(tr("formslator.log.saved_path", path=out))
        return out

    expected = os.path.join(OUTPUT_DIR, f"{base}_formatted.docx")
    out = resolve_writable_output_path(expected)
    if out != expected:
        log(tr("formslator.log.output_in_use", name=os.path.basename(out)))
    template_styles = []
    if template_path and os.path.isfile(template_path):
        from services.formslator.format_engine import list_styles_in_template

        template_styles = list_styles_in_template(template_path)
    merged = _merge_style_mapping(input_path, style_mapping)
    applied = _usable_style_mapping(merged, template_styles)
    format_document(
        input_path,
        output_path=out,
        template_path=template_path,
        style_mapping=applied,
        original_column=original_column,
        single_column=single_column,
    )
    if applied:
        log(tr("formslator.log.applied_mapping", count=len(applied)))
    log(tr("formslator.log.formatted", path=out))

    if mode in (2, 6):
        cfg = load_config()
        term_info = build_hyperlink_index(cfg)
        insert_glossary_hyperlinks(
            out,
            term_info=term_info,
            url_template=cfg.get("url_template"),
            original_column=original_column,
        )
        log(tr("formslator.log.glossary_inserted"))

    if mode in (5, 6):
        from services.formslator.settings import load_settings

        vs = load_settings()
        prefill_translations_from_vault(
            out,
            original_column=original_column,
            log_fn=log,
            vault_scope=str(vs.get("vault_scope") or "all"),
            vault_library_ids=list(vs.get("vault_library_ids") or []),
        )

    return out


LAYOUT_ALIASES = {
    # Pre-restructure output_layout values, kept so settings.json saved by an older
    # build still resolve sensibly instead of silently falling back to the default.
    # "auto" favored a template when one was available, which is what most existing
    # users' active_template setting already pointed at — template_double preserves that.
    "auto": "template_double",
    "double_column": "template_double",
    "single_column": "template_single",
}


def run_translate_job(
    input_path: str,
    *,
    model_name: str,
    target_code: str,
    template_path: str | None = None,
    style_mapping: dict | None = None,
    original_column: str = "left",
    stop_event: threading.Event | None = None,
    log_fn: Callable[[str], None] | None = None,
    progress_fn: Callable[..., None] | None = None,
    use_vault_prefill: bool = False,
    vault_scope: str = "all",
    vault_library_ids: list[str] | None = None,
    target_name: str | None = None,
    output_layout: str = "source_document",
) -> str:
    """output_layout: "source_document" (translation inserted directly below each
    paragraph of the source document, no template), "template_single" (translation
    below each paragraph, formatted with default_template_v3_single_column.docx), or
    "template_double" (translation in the table's right-hand column, formatted with
    default_template_v3_double_column.docx)."""
    log = log_fn or (lambda _m: None)
    ensure_dirs()
    base = os.path.splitext(os.path.basename(input_path))[0]

    mapping, template_from_map, meta = load_mapping_for_file(input_path)
    original_column = "left"

    layout = LAYOUT_ALIASES.get(output_layout, output_layout)
    if layout not in ("source_document", "template_single", "template_double"):
        layout = "template_double"

    tpl = template_path or _resolve_template(template_from_map) or _resolve_template(None)
    sc_tpl = _resolve_single_column_template(None)
    expected_formatted = os.path.join(OUTPUT_DIR, f"{base}_formatted.docx")
    formatted_path = resolve_writable_output_path(expected_formatted)

    use_table_format = layout == "template_double" and bool(tpl)
    use_single_column_styled = layout == "template_single" and bool(sc_tpl)
    active_tpl = tpl if use_table_format else (sc_tpl if use_single_column_styled else None)

    template_styles: list[str] = []
    if active_tpl:
        from services.formslator.format_engine import list_styles_in_template

        template_styles = list_styles_in_template(active_tpl)

    merged = _merge_style_mapping(input_path, style_mapping if style_mapping is not None else mapping)
    style_mapping = _usable_style_mapping(merged, template_styles)

    if use_single_column_styled and not style_mapping:
        # No usable mapping for this template — fall back to the plain paragraph
        # interleave rather than formatting with unmapped/default template styles,
        # which single_column mode isn't set up to do (see format_document's
        # single_column docstring: requires an effective style_mapping).
        use_single_column_styled = False

    if use_table_format:
        format_document(
            input_path,
            output_path=formatted_path,
            template_path=tpl,
            style_mapping=style_mapping,
            original_column=original_column,
        )
        mapping_file = meta.get("mapping_path") or ""
        if style_mapping and mapping_file:
            log(f"Applied style mapping → {mapping_file}")
        elif style_mapping:
            log("Applied style mapping (from session)")
        else:
            log(f"Formatted with default template styles → {formatted_path}")
    elif use_single_column_styled:
        format_document(
            input_path,
            output_path=formatted_path,
            template_path=sc_tpl,
            style_mapping=style_mapping,
            original_column=original_column,
            single_column=True,
        )
        mapping_file = meta.get("mapping_path") or ""
        if mapping_file:
            log(f"Applied style mapping (single-column) → {mapping_file}")
        else:
            log("Applied style mapping (single-column, from session)")
    else:
        formatted_path = _copy_to_output(input_path, "_working")
        if layout == "source_document":
            log("Source document layout selected — using plain paragraph interleave.")
        elif layout == "template_single" and (tpl or sc_tpl):
            log("Single-column layout selected — no usable style mapping, using plain paragraph interleave.")
        else:
            log("No template available — using copy for extraction.")

    if use_single_column_styled:
        paragraphs = extract_nonempty_paragraphs_single_column(formatted_path)
    else:
        paragraphs = extract_nonempty_paragraphs(formatted_path, original_column)
    source_code = detect_source_from_docx(input_path)
    glossary = build_glossary(load_config())
    log("=" * 60)
    log("Translation begins...\n")
    log(f"Detected source: {source_code}; paragraphs: {len(paragraphs)}")

    nonempty_total = sum(1 for p in paragraphs if (p or "").strip())
    total_chars = sum(_count_cjk_chars(p) for p in paragraphs) or sum(
        len(p) for p in paragraphs if (p or "").strip()
    )
    sec_per_char = get_model_sec_per_char(model_name)
    baseline = max(40.0, total_chars * sec_per_char)
    chars_done = 0

    if progress_fn:
        progress_fn(
            paragraph=0,
            total=nonempty_total,
            chars_done=0,
            total_chars=total_chars,
            percent=0,
            eta_seconds=baseline,
            elapsed=0.0,
        )

    translator = FormslatorTranslator(
        model_name=model_name,
        source_code=source_code,
        target_code=target_code,
        target_name=target_name,
        log_fn=log,
    )

    t0 = time.time()
    done_paras = 0

    def _progress(**kwargs):
        nonlocal done_paras, chars_done
        done_paras += kwargs.get("segment_done", 0)
        chars_done += kwargs.get("char_count", 0)
        if total_chars > 0:
            chars_done = min(chars_done, total_chars)
        elapsed = time.time() - t0
        if total_chars > 0:
            frac = chars_done / total_chars
        elif nonempty_total > 0:
            frac = done_paras / nonempty_total
        else:
            frac = 0.0
        eta = _estimate_eta_seconds(elapsed, frac, baseline)
        if progress_fn:
            progress_fn(
                paragraph=kwargs.get("paragraph", done_paras),
                total=kwargs.get("total", nonempty_total),
                original=kwargs.get("original", ""),
                latest=kwargs.get("latest", ""),
                elapsed=elapsed,
                chars_done=chars_done,
                total_chars=total_chars,
                percent=round(frac * 100),
                eta_seconds=eta,
            )
        orig = (kwargs.get("original") or "").strip()
        latest = (kwargs.get("latest") or "").strip()
        if orig or latest:
            _post_translate_pair_to_chat(orig, latest, vault=bool(kwargs.get("vault")))

    vault_index = None
    if use_vault_prefill:
        from extensions.knowledge_vault.translation import translation_index_for_scope

        vault_index = translation_index_for_scope(
            vault_scope or "all",
            vault_library_ids,
        )
        if vault_index.pairs:
            log(tr("formslator.log.vault_pairs", count=len(vault_index.pairs)))
        else:
            log(tr("formslator.log.vault_empty"))

    vault_flags: list[bool] = []
    translations = translate_paragraphs(
        paragraphs,
        translator,
        glossary,
        stop_event=stop_event,
        log_fn=log,
        progress_callback=_progress,
        vault_index=vault_index,
        vault_flags=vault_flags,
    )

    if stop_event and stop_event.is_set():
        log("Translation stopped.")
        return formatted_path

    out_name = (
        f"{base}_prefill_translation.docx"
        if use_vault_prefill
        else f"{base}_translation.docx"
    )
    out_path = resolve_writable_output_path(os.path.join(OUTPUT_DIR, out_name))
    if use_table_format:
        write_translations_to_formatted(
            formatted_path,
            translations,
            output_doc_path=out_path,
            original_column=original_column,
            vault_flags=vault_flags if use_vault_prefill else None,
        )
    elif use_single_column_styled:
        write_translations_single_column_styled(
            formatted_path,
            translations,
            output_doc_path=out_path,
            vault_flags=vault_flags if use_vault_prefill else None,
        )
    elif layout == "template_double":
        # Double-column layout chosen but no style template produced a table to slot
        # translations into — build a plain two-column table from scratch instead.
        write_translations_two_column_table(
            paragraphs, translations, output_doc_path=out_path, original_column=original_column
        )
    else:
        # Single-column layout: no table to slot translations into (write_translations_to_
        # formatted requires doc.tables) — without this branch every translation was
        # silently discarded and the output was just a copy of the original.
        write_translations_no_template(formatted_path, translations, output_doc_path=out_path)
    log(tr("formslator.log.translation_complete", path=out_path))

    total_seconds = time.time() - t0
    source_code = detect_source_from_docx(input_path)
    char_label = stats_char_label(source_code)
    orig_count = count_doc_lang_chars(input_path, source_code)
    out_count = count_doc_lang_chars(out_path, source_code)
    mapping_text = (
        tr("formslator.log.mapping_provided")
        if style_mapping
        else tr("formslator.log.mapping_not_provided")
    )
    corr_m, corr_s = _format_duration(total_seconds)

    log(tr("formslator.log.stats_sep"))
    log(tr("formslator.log.stats_title"))
    log(tr("formslator.log.stats_file", name=os.path.basename(input_path), mapping=mapping_text))
    log(tr("formslator.log.stats_original", label=char_label, count=orig_count))
    log(tr("formslator.log.stats_output", label=char_label, count=out_count))
    log(tr("formslator.log.stats_time", mins=corr_m, secs=corr_s))
    log(tr("formslator.log.stats_sep"))
    if not (stop_event and stop_event.is_set()):
        record_translate_speed(model_name, float(total_chars), total_seconds)
    return out_path


def save_upload(filename: str, data: bytes) -> str:
    ensure_dirs()
    safe = os.path.basename(filename)
    dest = os.path.join(UPLOADS_DIR, safe)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def save_template(filename: str, data: bytes) -> str:
    ensure_dirs()
    safe = os.path.basename(filename)
    if not safe.lower().endswith(".docx"):
        safe += ".docx"
    dest = os.path.join(STYLES_DIR, safe)
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def list_uploads() -> list[str]:
    ensure_dirs()
    return sorted(f for f in os.listdir(UPLOADS_DIR) if f.lower().endswith(".docx"))


def list_templates() -> list[str]:
    ensure_dirs()
    return sorted(f for f in os.listdir(STYLES_DIR) if f.lower().endswith(".docx"))
