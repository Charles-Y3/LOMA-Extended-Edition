# -*- coding: utf-8 -*-
"""Resize uploaded images before Ollama vision — full-resolution files crash llama-server on Windows."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

_VISION_TEMP_DIR = Path("data/temp/vision")
_DEFAULT_MAX_EDGE = 768
_FALLBACK_MAX_EDGE = 512


def _resolve_image_path(raw: str) -> Path | None:
    text = (raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        return path.resolve()
    root = Path(os.getcwd())
    candidate = (root / text).resolve()
    if candidate.is_file():
        return candidate
    return None


def prepare_vision_image_path(src_path: str, *, max_edge: int = _DEFAULT_MAX_EDGE) -> str:
    """
    Return absolute path to a vision-safe JPEG (bounded dimensions).
    Ollama reads the entire file into memory — always normalize before chat.
    """
    path = _resolve_image_path(src_path)
    if path is None:
        return str(src_path or "").strip()

    _VISION_TEMP_DIR.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(f"{path}|{path.stat().st_mtime_ns}|{max_edge}".encode()).hexdigest()[:16]
    dest = (_VISION_TEMP_DIR / f"ollama_{digest}.jpg").resolve()

    if dest.is_file() and dest.stat().st_mtime >= path.stat().st_mtime:
        return str(dest)

    try:
        from PIL import Image, ImageOps

        with ImageOps.exif_transpose(Image.open(path)) as img:
            img = img.convert("RGB")
            w, h = img.size
            if max(w, h) > max_edge:
                scale = max_edge / max(w, h)
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            img.save(dest, format="JPEG", quality=85, optimize=True)
        return str(dest)
    except Exception:
        return str(path)


def prepare_vision_image_paths(
    images: list,
    *,
    max_edge: int = _DEFAULT_MAX_EDGE,
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in images or []:
        prepared = prepare_vision_image_path(str(item), max_edge=max_edge)
        if prepared and prepared not in seen:
            seen.add(prepared)
            out.append(prepared)
    return out


def is_vision_server_crash(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return "0xc0000409" in msg or "stack-based buffer" in msg


def vision_fallback_max_edge() -> int:
    return _FALLBACK_MAX_EDGE


def vision_crash_user_message() -> str:
    from pipeline.i18n import t as tr

    return tr("errors.vision_server_crash")


def downscale_images_in_messages(messages: list, *, max_edge: int) -> list:
    out: list = []
    for msg in messages or []:
        entry = dict(msg)
        imgs = entry.get("images")
        if imgs:
            entry["images"] = prepare_vision_image_paths(list(imgs), max_edge=max_edge)
        out.append(entry)
    return out


def _guess_image_mime(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def image_item_to_data_uri(item: str) -> str | None:
    """Normalize one Ollama-style "images" list entry — a file path (what LOMA's own
    pipelines produce via prepare_vision_image_path), a raw base64 string, or already
    a data: URI — into an OpenAI-compatible `data:<mime>;base64,...` URI, for a
    provider with no native Ollama-style images field (e.g. LM Studio's
    OpenAI-compatible /v1/chat/completions).

    Returns None if the entry isn't recognizably an image, rather than guessing a
    mime type — one caller (services/media_transcription.py's audio-via-LLM path)
    reuses this same "images" field for raw WAV audio, which is an Ollama-only trick
    with no OpenAI content-type equivalent here; sending it as a fake image would be
    worse than dropping it, so the magic-byte check doubles as that filter."""
    text = (item or "").strip()
    if not text:
        return None
    if text.startswith("data:image/"):
        return text
    import base64

    path = _resolve_image_path(text)
    try:
        if path is not None:
            data = path.read_bytes()
        else:
            data = base64.b64decode(text, validate=True)
    except Exception:
        return None
    mime = _guess_image_mime(data)
    if not mime:
        return None
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def to_openai_content(text: str, images: list) -> list[dict] | str:
    """Build OpenAI-style message `content` (a list of text/image_url parts) from
    LOMA's internal (text, images) pair. Falls back to a plain text string if none
    of the `images` entries turn out to be recognizable images (see
    image_item_to_data_uri) — e.g. the audio-via-images trick — so a non-image
    payload is silently dropped rather than sent as a broken image."""
    parts: list[dict] = []
    if text:
        parts.append({"type": "text", "text": text})
    added_image = False
    for item in images or []:
        data_uri = image_item_to_data_uri(item)
        if data_uri:
            parts.append({"type": "image_url", "image_url": {"url": data_uri}})
            added_image = True
    return parts if added_image else text
