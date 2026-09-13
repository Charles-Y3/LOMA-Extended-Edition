# -*- coding: utf-8 -*-
"""Build LomaRequest and ContextBundle after routing."""
from __future__ import annotations

import os

from pipeline.schemas.task_schema import ContextBundle, LomaRequest
from services.source_parser import (
    estimate_chars,
    parse_all_context,
    parse_context_entry,
    resolve_office_filename,
    sources_need_retrieval as _parsed_sources_need_retrieval,
    template_office_filename,
)
from services.types import SourceDocument

# Above this size we always run chunking + selection (chars, not tokens).
_RETRIEVAL_THRESHOLD_CHARS = 12_000


def _context_cache_key(context_files: list, web_links: list, query: str = "") -> tuple:
    parts: list[tuple] = []
    has_media = False
    for item in context_files or []:
        if not isinstance(item, dict):
            continue
        path = item.get("path") or item.get("filename") or item.get("name") or ""
        mtime = 0
        if path and os.path.isfile(path):
            try:
                mtime = int(os.path.getmtime(path))
            except OSError:
                pass
        parts.append((item.get("name") or path, path, mtime))
        kind = (item.get("source_kind") or "").lower()
        ftype = (item.get("type") or "").lower()
        if kind in ("audio", "video") or ftype.startswith("media_"):
            has_media = True
    # Audio/video resolution is query-dependent (transcript vs vision scope), so a query
    # change must bust the cache for media. Pure text/doc/image sources are query-independent
    # and keep sharing the cache across follow-up questions on the same files.
    media_key = (query or "").strip().lower() if has_media else ""
    return (tuple(sorted(parts)), tuple(sorted(web_links or [])), media_key)


def _dedupe_paragraphs(text: str) -> str:
    """
    Remove exact repeated paragraphs while preserving order.
    This prevents repeated chunk selection (common with docx extraction / retrieval overlap).
    """
    raw = (text or "").strip()
    if not raw:
        return ""
    paras = [p.strip() for p in raw.split("\n\n") if p.strip()]
    out: list[str] = []
    seen: set[str] = set()
    for p in paras:
        key = " ".join(p.lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return "\n\n".join(out).strip()


def _normalize_context_files(context_files: list) -> list[dict]:
    """Ensure every context entry carries unified source metadata."""
    normalized: list[dict] = []
    for item in context_files or []:
        if not isinstance(item, dict):
            continue
        ps = parse_context_entry(dict(item))
        entry = ps.to_context_dict()
        for key in ("_transcription_gap", "_resolved_transcript"):
            if key in item:
                entry[key] = item[key]
        normalized.append(entry)
    return normalized


def _web_markdown_from_blocks(web_text_blocks: list[tuple[str, str]] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for url, content in web_text_blocks or []:
        if isinstance(content, dict):
            if content.get("error"):
                # A failed scrape's error payload (raw exception text) is not page
                # content — feeding it to the LLM as if it were the article produces
                # a confident-sounding wrong answer instead of "couldn't read this page".
                continue
            text = content.get("simple_text") or content.get("content") or ""
        else:
            text = str(content or "")
        if text.strip():
            out[url] = text.strip()
    return out


def _resolve_media_sources(
    parsed_sources, profile_model: str, user_query: str = ""
) -> tuple[list[tuple[str, str]], list[str]]:
    """Transcribe/describe audio+video sources.

    Successful transcripts are written to ``ps.markdown`` — the parsed-source digest
    loop in build_source_digests picks those up directly. media_blocks carries ONLY
    the gap placeholders (pending transcription / vision required), keyed by source
    name, so a single media file never produces two digests for the same content.
    """
    media_blocks: list[tuple[str, str]] = []
    vision_frames: list[str] = []
    for ps in parsed_sources:
        if ps.kind not in ("audio", "video"):
            continue
        path = ps.media_path or ps.path
        if not path or not os.path.isfile(path):
            if ps.kind == "video" and (ps.name or "").startswith(("http://", "https://")):
                from services.media_query import classify_media_query
                from services.media_fetch import resolve_media_from_links

                mode, _scope = classify_media_query(user_query, is_video=True)
                prefer_video = mode in ("vision", "both")
                path = resolve_media_from_links([ps.name], prefer_video=prefer_video) or ""
                if path:
                    ps.media_path = path
            if not path or not os.path.isfile(path):
                path = os.path.join("data", "uploads", ps.name)
        if not path or not os.path.isfile(path):
            continue
        from services.media_context import resolve_media_to_markdown

        md, gap, frames = resolve_media_to_markdown(path, profile_model, user_query)
        if frames:
            vision_frames.extend(frames)
            if ps.raw:
                ps.raw["_vision_frames"] = frames
        if gap:
            if ps.raw:
                ps.raw["_transcription_gap"] = gap
            from services.media_context import GAP_VISION

            if gap == GAP_VISION:
                media_blocks.append(
                    (ps.name, md or f"## Attached media: {ps.name}\n(Vision model required.)")
                )
            else:
                media_blocks.append(
                    (
                        ps.name,
                        f"## Attached media: {ps.name}\n"
                        "(Transcription pending — install local Whisper when prompted.)",
                    )
                )
        elif md:
            ps.markdown = md
            if ps.raw:
                ps.raw["_resolved_transcript"] = md
    return media_blocks, vision_frames


def _collect_sources_from_parsed(parsed_sources) -> list[SourceDocument]:
    sources: list[SourceDocument] = []
    for ps in parsed_sources:
        if not ps.ok:
            continue
        text = ps.context_text()
        if not text:
            continue
        labeled = f"## Source: {ps.name}\n\n{text}"
        if ps.kind == "web":
            kind = "web"
        elif str(ps.name).lower().startswith("excerpt"):
            kind = "excerpt"
        else:
            kind = "document"
        sources.append(
            SourceDocument(
                name=ps.name,
                text=labeled,
                source_kind=kind,
            )
        )
    return sources


def build_request(
    user_input: str,
    profile_id: str,
    messages: list,
    context_files: list,
    web_links: list,
    settings: dict,
) -> LomaRequest:
    return LomaRequest(
        user_input=user_input,
        profile_id=profile_id,
        messages=list(messages),
        context_files=_normalize_context_files(context_files),
        web_links=list(web_links),
        settings=dict(settings or {}),
    )


def estimate_source_chars(context_files: list, web_text_blocks: list[tuple[str, str]] | None = None) -> int:
    web_md = _web_markdown_from_blocks(web_text_blocks)
    parsed = parse_all_context(context_files, web_links=None, web_markdown=web_md)
    for url, content in web_text_blocks or []:
        if url in web_md:
            continue
        if isinstance(content, dict):
            text = content.get("simple_text") or content.get("content") or ""
        else:
            text = str(content or "")
        if text.strip():
            from services.source_parser.models import ParsedSource

            parsed.append(ParsedSource(name=url, kind="web", markdown=text.strip()))
    return estimate_chars(parsed)


def sources_need_retrieval(
    context_files: list,
    web_text_blocks: list[tuple[str, str]] | None = None,
    *,
    threshold: int = _RETRIEVAL_THRESHOLD_CHARS,
) -> bool:
    web_md = _web_markdown_from_blocks(web_text_blocks)
    parsed = parse_all_context(context_files, web_links=None, web_markdown=web_md)
    for url, content in web_text_blocks or []:
        if url in web_md:
            continue
        if isinstance(content, dict):
            text = content.get("simple_text") or content.get("content") or ""
        else:
            text = str(content or "")
        if text.strip():
            from services.source_parser.models import ParsedSource

            parsed.append(ParsedSource(name=url, kind="web", markdown=text.strip()))
    return _parsed_sources_need_retrieval(parsed, threshold=threshold)


def build_context_bundle(
    request: LomaRequest,
    profile: dict,
    web_text_blocks: list[tuple[str, str]] | None = None,
) -> ContextBundle:
    web_md = _web_markdown_from_blocks(web_text_blocks)
    from services.web_context_cache import merge_cached_links

    web_md = merge_cached_links(request.web_links, web_md)
    cache_key = _context_cache_key(request.context_files, request.web_links, request.user_input)
    from services.session import state as session_state

    reuse_parsed = (
        cache_key == getattr(session_state, "context_bundle_cache_key", None)
        and getattr(session_state, "context_parsed_sources_cache", None) is not None
    )
    if reuse_parsed:
        try:
            from services.session.state import add_log

            add_log("Context bundle: reusing cached parsed sources")
        except Exception:
            pass
        parsed_sources = session_state.context_parsed_sources_cache
        media_blocks = list(session_state.context_media_blocks_cache or [])
        images = list(session_state.context_images_cache or [])
        from services.vision_input import prepare_vision_image_paths

        images = prepare_vision_image_paths(images)
        image_notes = ""
        for ps in parsed_sources:
            if ps.kind == "image":
                image_notes += f"--- ATTACHED IMAGE: {ps.name} ---\n[image data in vision pipeline]\n\n"
        if images and any(ps.kind == "video" for ps in parsed_sources):
            image_notes += (
                f"--- VIDEO FRAMES ({len(images)} attached for vision analysis) ---\n"
                "[frame images in vision pipeline — please wait for inference]\n\n"
            )
    else:
        parsed_sources = parse_all_context(
            request.context_files,
            request.web_links,
            web_markdown=web_md,
        )

        images: list = []
        image_notes = ""
        for ps in parsed_sources:
            if ps.kind != "image":
                continue
            img_path = ps.media_path or ps.path
            if img_path:
                images.append(img_path)
                image_notes += f"--- ATTACHED IMAGE: {ps.name} ---\n[image data in vision pipeline]\n\n"

        profile_model = ""
        try:
            from services.model_router import resolve_general_model

            profile_model = resolve_general_model(profile)
        except Exception:
            pass

        media_blocks, vision_frames = _resolve_media_sources(
            parsed_sources, profile_model, request.user_input
        )
        if vision_frames:
            images.extend(vision_frames)
            image_notes += (
                f"--- VIDEO FRAMES ({len(vision_frames)} attached for vision analysis) ---\n"
                "[frame images in vision pipeline — please wait for inference]\n\n"
            )

        from services.vision_input import prepare_vision_image_paths

        images = prepare_vision_image_paths(images)

        session_state.context_bundle_cache_key = cache_key
        session_state.context_parsed_sources_cache = parsed_sources
        session_state.context_images_cache = list(images)
        session_state.context_media_blocks_cache = list(media_blocks)

    from pipeline.context import assemble_workspace_context, build_source_digests

    if reuse_parsed and getattr(session_state, "context_source_digests_cache", None):
        digests = session_state.context_source_digests_cache
    else:
        digests = build_source_digests(
            parsed_sources,
            media_blocks=media_blocks,
        )
        session_state.context_source_digests_cache = digests

    assembled = assemble_workspace_context(
        digests,
        query=request.user_input,
        profile=profile,
        image_notes=image_notes,
    )
    unified = _dedupe_paragraphs(assembled.unified_text)

    recent_chat = ""
    if len(request.messages) > 3:
        recent_chat = "\n".join(
            f"{m['role'].capitalize()}: {m['content']}"
            for m in request.messages[-6:-1]
            if m.get("role") in ("user", "assistant")
        )

    original_filename = resolve_office_filename(parsed_sources, "output")
    template_filename = template_office_filename(parsed_sources)

    return ContextBundle(
        query=request.user_input,
        profile_id=request.profile_id,
        profile=profile,
        unified_text=unified,
        images=images,
        web_links=list(request.web_links),
        recent_chat=recent_chat,
        original_filename=original_filename,
        template_filename=template_filename,
        context_was_retrieved=assembled.was_truncated,
        context_selection_mode=assembled.selection_mode,
        parsed_sources=parsed_sources,
        source_digests=digests,
        context_strategy=assembled.strategy,
        digest_index_md=assembled.digest_index_md,
        total_source_chars=assembled.total_source_chars,
        context_char_budget=assembled.char_budget,
    )
