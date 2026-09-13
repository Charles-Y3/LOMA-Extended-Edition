# -*- coding: utf-8 -*-
"""Fetch audio/video from web URLs for transcription."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlparse

_MEDIA_EXTS = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".flac", ".mp4", ".mkv", ".mov", ".webm", ".avi"})

_VIDEO_HOSTS = (
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "dailymotion.com",
    "twitch.tv",
    "facebook.com",
    "instagram.com",
    "tiktok.com",
)


def link_is_video_or_audio(url: str) -> bool:
    u = (url or "").strip()
    if not u:
        return False
    try:
        host = (urlparse(u).netloc or "").lower().lstrip("www.")
    except Exception:
        host = ""
    if any(h in host for h in _VIDEO_HOSTS):
        return True
    path = (urlparse(u).path or "").lower()
    ext = os.path.splitext(path)[1]
    return ext in _MEDIA_EXTS


def _download_dir() -> str:
    path = os.path.join("data", "uploads", "_web_media")
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(url: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", url)[:48].strip("_")
    return slug or "media"


def _download_with_ytdlp(url: str, dest_dir: str, *, prefer_video: bool = False) -> str | None:
    try:
        import yt_dlp
    except ImportError:
        return None

    outtmpl = os.path.join(dest_dir, "%(id)s.%(ext)s")
    opts = {
        "format": (
            "best[height<=720][ext=mp4]/best[height<=720]/best[ext=mp4]/best"
            if prefer_video
            else "bestaudio/best"
        ),
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None
        ext = info.get("ext") or "mp4"
        vid = info.get("id") or _safe_name(url)
        candidate = os.path.join(dest_dir, f"{vid}.{ext}")
        if os.path.isfile(candidate):
            return candidate
        for name in os.listdir(dest_dir):
            if name.startswith(str(vid)):
                return os.path.join(dest_dir, name)
    return None


def _download_direct(url: str, dest_dir: str) -> str | None:
    try:
        import urllib.request

        ext = os.path.splitext(urlparse(url).path)[1] or ".mp4"
        if ext not in _MEDIA_EXTS:
            ext = ".mp4"
        dest = os.path.join(dest_dir, f"{_safe_name(url)}{ext}")
        urllib.request.urlretrieve(url, dest)
        return dest if os.path.isfile(dest) and os.path.getsize(dest) > 0 else None
    except Exception:
        return None


def _json3_to_text(raw: str) -> str:
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    parts: list[str] = []
    for event in data.get("events") or []:
        for seg in event.get("segs") or []:
            chunk = (seg.get("utf8") or "").strip()
            if chunk and chunk != "\n":
                parts.append(chunk)
    text = re.sub(r"\s+", " ", "".join(parts)).strip()
    return text


def _subtitle_to_text(raw: str, ext: str = "") -> str:
    ext = (ext or "").lower()
    if ext == "json3" or raw.lstrip().startswith("{"):
        body = _json3_to_text(raw)
        if body:
            return body
    return _vtt_to_text(raw)


def _pick_subtitle_track(tracks: list) -> tuple[str, str]:
    """Return (url, ext) preferring human-readable vtt over json3."""
    preferred_ext = ("vtt", "ttml", "srv3", "json3")
    for ext in preferred_ext:
        for track in tracks or []:
            if (track.get("ext") or "").lower() == ext and track.get("url"):
                return track["url"], ext
    for track in tracks or []:
        if track.get("url"):
            return track["url"], (track.get("ext") or "vtt")
    return "", ""


def _vtt_to_text(vtt: str) -> str:
    lines: list[str] = []
    for line in vtt.splitlines():
        s = line.strip()
        if not s or s.startswith("WEBVTT") or "-->" in s or re.match(r"^\d+$", s):
            continue
        if s.startswith("NOTE"):
            continue
        lines.append(re.sub(r"<[^>]+>", "", s))
    return "\n".join(lines).strip()


def fetch_transcript_from_url(url: str, *, log_fn=None) -> dict | None:
    """
    Use platform captions when available (YouTube auto-subs, etc.).
    Returns {type: text, content, filename} or None.
    """
    log = log_fn or (lambda _m: None)
    u = (url or "").strip()
    if not u:
        return None
    try:
        import urllib.request

        import yt_dlp
    except ImportError:
        return None

    host = (urlparse(u).netloc or "").lower()
    if "youtube" not in host and "youtu.be" not in host:
        return None

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as ydl:
            info = ydl.extract_info(u, download=False)
        if not info:
            return None
        title = (info.get("title") or "video").strip()
        vid = info.get("id") or _safe_name(u)
        manual = info.get("subtitles") or {}
        auto = info.get("automatic_captions") or {}
        merged = {**auto, **manual}
        lang_order = (
            "en",
            "en-US",
            "en-GB",
            "a.en",
            "en-orig",
            "zh-Hans",
            "zh",
            "zh-Hant",
        )
        sub_url = ""
        sub_ext = ""
        for lang in lang_order:
            tracks = merged.get(lang)
            if tracks:
                sub_url, sub_ext = _pick_subtitle_track(tracks)
                if sub_url:
                    break
        if not sub_url:
            for tracks in merged.values():
                sub_url, sub_ext = _pick_subtitle_track(list(tracks or []))
                if sub_url:
                    break
        if not sub_url:
            return None
        req = urllib.request.Request(sub_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        body = _subtitle_to_text(raw, sub_ext)
        if not body.strip():
            return None
        log(f"Using captions from {host} (no local transcribe needed).")
        md = f"# Transcript: {title}\n\n{body}"
        return {"filename": f"{vid}.txt", "type": "text", "content": md}
    except Exception:
        return None


def resolve_media_from_links(
    links: list[str],
    *,
    log_fn=None,
    prefer_video: bool = False,
) -> str | None:
    """Download the first transcribable media URL; returns local file path."""
    log = log_fn or (lambda _m: None)
    for link in links or []:
        u = (link or "").strip()
        if not u or not link_is_video_or_audio(u):
            continue
        dest_dir = _download_dir()
        log(
            f"Fetching {'video' if prefer_video else 'media'} from link: {u[:120]}"
        )
        path = _download_with_ytdlp(u, dest_dir, prefer_video=prefer_video)
        if not path:
            path = _download_direct(u, dest_dir)
        if path and os.path.isfile(path):
            log(f"Media ready: {os.path.basename(path)}")
            return path
        log("Could not download media from link (install yt-dlp for YouTube/Vimeo).")
    return None
