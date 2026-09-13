# core/artifact_compiler.py
# -*- coding: utf-8 -*-
"""Shared artifact export / template mutation (engine + Preview Export)."""
from __future__ import annotations

import os
import shutil
from typing import Callable, Optional

from services.session import draft as draft_sync, state
from pipeline.base.profile_pack import build_mutation_hint
from services.office_mutation import mutate_office_file, resolve_upload_path
from services.source_parser import parse_file
from services.artifact_build import generate_output
from pipeline.output_format import EXTENSION_BY_TYPE

SlideProgressFn = Callable[[int, int, str], None]

_BINARY_EXPORT_TYPES = frozenset({"image", "sound", "video"})
_COMPILED_OFFICE_TYPES = frozenset({"document", "presentation"})
_EXPORT_SHORTCIRCUIT_TYPES = _BINARY_EXPORT_TYPES | _COMPILED_OFFICE_TYPES


def mutation_source_available(output_type: str, original_filename: str) -> bool:
    src = resolve_upload_path(original_filename)
    if not src:
        return False
    lower = src.lower()
    # .pdf: mutate_office_file already builds a translated .docx from PDF-extracted
    # units (services/office_mutation/mutate.py, pdf_units.py) — this check was the
    # only thing keeping PDF sources out of that path and routing them through the
    # generic multi-step generation pipeline instead, which doesn't reliably
    # translate a full document end to end.
    return lower.endswith((".docx", ".pptx", ".pdf"))


def should_auto_mutate(mode, output_type: str, original_filename: str) -> bool:
    return mode == "mutation" and mutation_source_available(output_type, original_filename)


def sync_preview_from_artifact(path: str, output_type: str, mode: str = "mutation") -> None:
    """Load processed file text into the Preview panel."""
    if not path or not os.path.exists(path):
        return
    parsed_source = parse_file(path)
    legacy = parsed_source.legacy_upload_dict()
    if legacy.get("type") == "text" and legacy.get("content"):
        draft_sync.set_draft(legacy["content"], output_type, mode)
        state.live_workspace_plain = legacy["content"]
        state.draft_content = legacy["content"]
    try:
        from ui.components.preview_workspace import sync_preview_editor

        sync_preview_editor()
    except Exception:
        pass
    ui_mod = state.get_ui_module()
    if hasattr(ui_mod, "render_preview_tab") and hasattr(ui_mod.render_preview_tab, "refresh"):
        ui_mod.render_preview_tab.refresh()


def set_mutation_processing(slide: int, total: int, dest_path: str = "") -> None:
    state.mutation_in_progress = True
    state.mutation_slide_current = slide
    state.mutation_slide_total = total
    if dest_path:
        state.mutation_work_path = dest_path


def clear_mutation_processing() -> None:
    state.mutation_in_progress = False
    state.mutation_slide_current = 0
    state.mutation_slide_total = 0
    state.mutation_work_path = ""


def ready_artifact_path(expected_fingerprint: str | None = None) -> Optional[str]:
    """Return path if a fully processed artifact is already on disk. When
    expected_fingerprint is given, the cached artifact's own fingerprint must
    match it too — otherwise the file on disk belongs to a different request
    (different source file, instruction, or draft content) and must not be
    handed back as if it were this request's result."""
    path = state.last_generated_file_path
    if not (path and os.path.exists(path) and state.artifact_ready):
        return None
    if expected_fingerprint is not None and state.last_artifact_fingerprint != expected_fingerprint:
        return None
    return path


def _title_slug_from_content(content: str) -> str:
    """First markdown heading in generated content, slugified — the actual document
    title, when there is one, beats any slug derived from the user's raw query."""
    import re

    for line in (content or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^#{1,2}\s+(.{3,80})$", line)
        if m:
            slug = re.sub(r"[^a-z0-9]+", "_", m.group(1).lower())[:48].strip("_")
            if slug:
                return slug
        break  # only the very first non-blank line counts as a title candidate
    return ""


def resolve_generated_stem(
    *,
    query: str = "",
    source_names: list[str] | None = None,
    output_type: str = "document",
    content: str = "",
) -> str:
    """Task-aware filename stem — not always the first uploaded source."""
    import re

    from pipeline.output_format import normalize_output_type

    q = (query or "").strip().lower()
    ot = normalize_output_type(output_type)

    def _topic_slug() -> str:
        m = re.search(r"\b(?:on|about|for)\s+([\w\s-]{3,48})", q, re.I)
        if not m:
            return ""
        return re.sub(r"[^a-z0-9]+", "_", m.group(1).lower())[:36].strip("_")

    if ot == "presentation":
        slug = _topic_slug()
        if slug:
            return slug
        return "presentation"
    if ot == "spreadsheet":
        return "spreadsheet"
    from pipeline.query_intent_i18n import matches

    names = [str(n).strip() for n in (source_names or []) if str(n).strip()]
    if re.search(r"\b(?:similarit|compare|contrast|difference|versus|vs\.?)\b", q) or matches(
        q, "cross_source_compare"
    ):
        if len(names) >= 2:
            a = os.path.splitext(os.path.basename(names[0]))[0][:24]
            b = os.path.splitext(os.path.basename(names[1]))[0][:24]
            return f"comparison_{a}_and_{b}"
        return "document_comparison"
    if re.search(r"\b(?:combin|all sources|across sources|multi[- ]source)\b", q) or matches(
        q, "combined_summary"
    ):
        return "combined_summary"
    if len(names) > 1 and (
        re.search(r"\b(?:summar|synthesis|report|analysis)\b", q)
        or matches(q, "verb_summarize")
        or matches(q, "verb_analyze")
    ):
        return "multi_source_report"
    if len(names) == 1:
        return os.path.splitext(os.path.basename(names[0]))[0]
    if not names:
        title_slug = _title_slug_from_content(content)
        if title_slug:
            return title_slug
    slug = _topic_slug()
    if slug:
        return slug
    if q:
        slug = re.sub(r"[^a-z0-9]+", "_", q)[:48].strip("_")
        if slug:
            return slug
    return "output"


def _export_dest_basename(
    original_filename: str,
    output_type: str,
    *,
    query: str = "",
    source_names: list[str] | None = None,
) -> str:
    base_name = resolve_generated_stem(
        query=query,
        source_names=source_names or ([original_filename] if original_filename else None),
        output_type=output_type,
    )
    for suffix in ("_en", "_zh", "_loma", "_LOMA", "_loma", "_LOMA"):
        if base_name.endswith(suffix):
            base_name = base_name[: -len(suffix)]
    ext_mapping = dict(EXTENSION_BY_TYPE)
    ext_mapping.setdefault("sound", ".wav")
    # "image" already resolves to ".png" via EXTENSION_BY_TYPE — the canonical
    # format (lossless, alpha-channel support for Artwork Studio's Composite
    # mode). No .jpg default here.
    detected_ext = ext_mapping.get(output_type, ".txt")
    return f"{base_name}_LOMA{detected_ext}"


def export_ready_artifact(
    output_type: str,
    original_filename: str,
    *,
    log_fn=None,
    expected_fingerprint: str | None = None,
) -> Optional[str]:
    """
    Copy an on-disk artifact for Download/Save without re-synthesis.
    Applies to images, audio/video, and compiled office files (docx/pptx).
    Skipped when preview_dirty (user edited draft), or when expected_fingerprint
    doesn't match the cached artifact's — see ready_artifact_path().
    """
    if output_type not in _EXPORT_SHORTCIRCUIT_TYPES:
        return None
    # Binary artifacts are the file on disk — draft edits must not force re-synthesis
    # on Download. Office compiled types still respect preview_dirty.
    if output_type not in _BINARY_EXPORT_TYPES and state.preview_dirty:
        return None
    existing = ready_artifact_path(expected_fingerprint)
    if not existing:
        return None

    log = log_fn or state.add_log
    os.makedirs("data/generated", exist_ok=True)
    dest = os.path.join("data", "generated", _export_dest_basename(original_filename, output_type))

    if output_type == "image":
        from services.artifact_build import _convert_image_to_jpg

        existing_abs = os.path.abspath(existing)
        dest_abs = os.path.abspath(dest)
        # Same-file check must cover every extension, not just jpg/jpeg — a
        # png (or any other) source landing on the same destination path hit
        # shutil.copy2's SameFileError here since this only guarded jpg.
        if os.path.normcase(existing_abs) == os.path.normcase(dest_abs):
            log(f"Exporting existing image → {os.path.basename(existing_abs)}")
            return existing_abs
        if dest.lower().endswith(".jpg") and not existing.lower().endswith((".jpg", ".jpeg")):
            if os.path.exists(dest):
                try:
                    os.remove(dest)
                except OSError:
                    pass
            result = _convert_image_to_jpg(existing, dest)
            log(f"Exporting existing image → {os.path.basename(result)}")
            return result
        shutil.copy2(existing_abs, dest_abs)
        log(f"Exporting existing image → {os.path.basename(dest_abs)}")
        return dest_abs

    existing_abs = os.path.abspath(existing)
    dest_abs = os.path.abspath(dest)
    if os.path.normcase(existing_abs) == os.path.normcase(dest_abs):
        log(f"Exporting existing {output_type} → {os.path.basename(existing_abs)}")
        return existing_abs
    shutil.copy2(existing_abs, dest_abs)
    log(f"Exporting existing {output_type} → {os.path.basename(dest_abs)}")
    return dest_abs


# Backward-compatible alias
export_ready_binary_artifact = export_ready_artifact


def request_fingerprint(
    mode,
    output_type: str,
    original_filename: str,
    *,
    instruction: str = "",
    content: str = "",
) -> str:
    """Shared fingerprint formula for every reuse-shortcut below, and for the
    Preview panel's Download button (ui/components/preview_workspace.py) — one
    formula so a cached artifact is only ever reused when it truly matches the
    current request. Mutation-mode artifacts are keyed by instruction (the
    source file is edited in place); generation-mode artifacts by the actual
    draft content (what gets rendered into the new file)."""
    basis = instruction if mode == "mutation" else content
    return state.artifact_fingerprint(mode or "generation", output_type, original_filename, basis)


def compile_artifact(
    *,
    output_type: str,
    original_filename: str,
    gen_model: str,
    mode,
    instruction: str,
    content: str = "",
    profile: dict | None = None,
    replan: bool = True,
    on_slide_progress: SlideProgressFn | None = None,
    log_fn=None,
) -> Optional[str]:
    """
    Build data/generated artifact. Template mutation when mode=mutation and source exists;
    otherwise markdown synthesis from content.
    """
    log = log_fn or state.add_log
    prof = profile or {}
    profile_hint = build_mutation_hint(prof)
    use_template = should_auto_mutate(mode, output_type, original_filename)

    if use_template and not (instruction or "").strip():
        log("Mutation aborted — no user instruction.")
        return None

    if use_template:
        fp = request_fingerprint(mode, output_type, original_filename, instruction=instruction)
        existing = ready_artifact_path(fp) if not replan else None
        if existing:
            log(f"Artifact already ready → {os.path.basename(existing)}")
            return existing

        src = resolve_upload_path(original_filename)
        log(f"Template mutation → {os.path.basename(src)}")
        set_mutation_processing(0, 0, "")

        def _slide_cb(slide_idx: int, total: int, dest_path: str) -> None:
            set_mutation_processing(slide_idx + 1, total, dest_path)
            sync_preview_from_artifact(dest_path, output_type, "mutation")
            if on_slide_progress:
                on_slide_progress(slide_idx + 1, total, dest_path)

        try:
            cached_map = None if replan else state.mutation_map
            from services.source_parser import parse_all_context, primary_office_source

            office_source = primary_office_source(parse_all_context(state.active_context_files))
            result = mutate_office_file(
                output_type,
                original_filename,
                gen_model,
                instruction=instruction,
                text_map=cached_map,
                profile_hint=profile_hint,
                replan=replan,
                on_unit_applied=_slide_cb,
                src_path=office_source.path if office_source else None,
                units=office_source.mutation_units if office_source else None,
            )
            if result and os.path.exists(result):
                state.last_generated_file_path = result
                state.artifact_ready = True
                state.last_artifact_fingerprint = fp
        finally:
            clear_mutation_processing()
        return result

    if not (content or "").strip():
        return None

    fp = request_fingerprint(mode, output_type, original_filename, content=content)
    if output_type in _EXPORT_SHORTCIRCUIT_TYPES and not state.preview_dirty:
        exported = export_ready_artifact(output_type, original_filename, log_fn=log, expected_fingerprint=fp)
        if exported:
            return exported

    log(f"Synthesizing {output_type} from markdown draft…")
    result = generate_output(output_type, content, original_filename, gen_model)
    if result and os.path.exists(result):
        state.last_generated_file_path = result
        state.artifact_ready = True
        state.preview_dirty = False
        state.last_artifact_fingerprint = fp
    return result


def save_preview_to_artifact(
    *,
    output_type: str | None = None,
    original_filename: str = "output",
    gen_model: str = "",
    mode=None,
    profile: dict | None = None,
    log_fn=None,
    use_editor: bool = True,
) -> Optional[str]:
    """Compile current Preview draft to data/generated/ (always uses latest editor text)."""
    log = log_fn or state.add_log
    if use_editor:
        draft_sync.sync_editor_to_state()
    content = draft_sync.get_draft_for_export()
    out_type = output_type or state.live_workspace_output_type or "document"
    if not content:
        log("Save aborted — preview draft is empty.")
        return None

    if not gen_model:
        from config import ROLES
        profile_id = state.current_settings.get("active_profile", "simple_assistant")
        try:
            from pipeline.base import profile_pack as profile_manager
            prof = profile_manager.load_profile(profile_id) or {}
        except Exception:
            prof = profile or {}
        gen_model = prof.get("MODEL", {}).get("preferred_llm") or ROLES.get("General")
        if not gen_model:
            from services.startup_warmup import routing_model_name

            gen_model = routing_model_name()
    else:
        prof = profile

    mode = mode if mode is not None else state.live_workspace_mode

    if should_auto_mutate(mode, out_type, original_filename):
        instruction = draft_sync.get_last_user_instruction(state.messages, state.last_user_instruction)
        result = compile_artifact(
            output_type=out_type,
            original_filename=original_filename,
            gen_model=gen_model,
            mode=mode,
            instruction=instruction,
            content=content,
            profile=prof,
            replan=True,
            log_fn=log,
        )
    else:
        fp = request_fingerprint(mode or "generation", out_type, original_filename, content=content)
        if out_type in _EXPORT_SHORTCIRCUIT_TYPES and not state.preview_dirty:
            result = export_ready_artifact(out_type, original_filename, log_fn=log, expected_fingerprint=fp)
            if result:
                state.last_generated_file_path = result
                state.artifact_ready = True
                state.preview_dirty = False
                state.last_artifact_fingerprint = fp
                log(f"Saved → data/generated/{os.path.basename(result)}")
                return result
        log(f"Saving {out_type} from Preview ({len(content):,} chars)…")
        result = compile_artifact(
            output_type=out_type,
            original_filename=original_filename,
            gen_model=gen_model,
            mode=mode or "generation",
            instruction="",
            content=content,
            profile=prof,
            replan=True,
            log_fn=log,
        )

    if result and os.path.exists(result):
        state.last_generated_file_path = result
        state.artifact_ready = True
        state.preview_dirty = False
        log(f"Saved → data/generated/{os.path.basename(result)}")
    return result
