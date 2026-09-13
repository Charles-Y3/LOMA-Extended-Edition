# -*- coding: utf-8 -*-
"""Audio/video transcription to timestamped markdown."""
from __future__ import annotations

import contextlib
import os
import re
from typing import Callable


DEFAULT_ACCURATE_MODEL = "base"
# "large" is a real, distinct tier (resolves to faster-whisper's large-v3 build) — not
# collapsed into "turbo" like older builds of this app did. It's the heaviest/most
# accurate tier and is gated to capable hardware by the Model Library's tier budgeting.
# "tiny" was previously a mandatory hidden baseline (fast first-token tier for a
# whisper-based live-dictation preview pass); live dictation is SenseVoice-only now
# (see ui/components/voice_controls.py), so that tier has no remaining use and isn't
# offered here.
WHISPER_TRANSCRIPTION_SIZES: tuple[str, ...] = ("base", "small", "turbo", "large")
_LEGACY_WHISPER_MODEL_MAP: dict[str, str] = {
    "medium": "small",
    "large-v1": "large",
    "large-v2": "large",
    "large-v3": "large",
    "large-v3-turbo": "turbo",
    "distil-large-v2": "small",
    "distil-large-v3": "turbo",
    "distil-large-v3.5": "turbo",
}


def normalize_whisper_model(size: str | None) -> str:
    """User-facing transcription sizes: tiny, base, small, turbo, large."""
    raw = (size or "").strip().lower() or DEFAULT_ACCURATE_MODEL
    if raw in WHISPER_TRANSCRIPTION_SIZES:
        return raw
    return _LEGACY_WHISPER_MODEL_MAP.get(raw, DEFAULT_ACCURATE_MODEL)


def resolve_whisper_model(size: str | None = None) -> str:
    """Resolve configured or passed size to a downloadable transcription model."""
    if size is not None and str(size).strip():
        return normalize_whisper_model(size)
    try:
        from services.session import state

        settings_size = str((state.current_settings or {}).get("default_whisper_model") or "").strip()
        if settings_size:
            return normalize_whisper_model(settings_size)
    except Exception:
        pass
    return DEFAULT_ACCURATE_MODEL


def transcription_deps_available() -> tuple[bool, str]:
    missing: list[str] = []
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        missing.append("faster-whisper")
    return (len(missing) == 0, ", ".join(missing))


_WHISPER_MODEL_CACHE: dict[str, object] = {}


def evict_whisper_model_cache(size: str) -> None:
    """Drop an in-memory loaded WhisperModel for `size` (e.g. after deleting its on-disk
    cache) so a stale model instance isn't reused on the next transcription."""
    _WHISPER_MODEL_CACHE.pop((size or "").strip(), None)


@contextlib.contextmanager
def _hf_download_progress(on_percent: Callable[[float], None] | None):
    """faster_whisper hardcodes tqdm_class=disabled_tqdm when it downloads a model from
    the Hub (services.media_transcription references this at call time via the module
    global, so patching the attribute here does take effect) — which is why the download
    previously showed no progress at all, just a jump from 0% to done. Swap in a real
    tqdm subclass for the duration of the download so byte progress reaches the UI."""
    if on_percent is None:
        yield
        return
    import faster_whisper.utils as fw_utils
    from tqdm import tqdm as _tqdm

    class _ProgressTqdm(_tqdm):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = False
            super().__init__(*args, **kwargs)

        def update(self, n=1):
            result = super().update(n)
            if self.total:
                on_percent(min(0.99, self.n / self.total))
            return result

    original = fw_utils.disabled_tqdm
    fw_utils.disabled_tqdm = _ProgressTqdm
    try:
        yield
    finally:
        fw_utils.disabled_tqdm = original


def _get_whisper_model(size: str, on_percent: Callable[[float], None] | None = None):
    """Reuse a loaded WhisperModel per size — avoids a multi-second reload on every chunk."""
    model = _WHISPER_MODEL_CACHE.get(size)
    if model is None:
        from faster_whisper import WhisperModel

        with _hf_download_progress(on_percent):
            model = WhisperModel(size, device="cpu", compute_type="int8")
        if on_percent:
            on_percent(1.0)
        _WHISPER_MODEL_CACHE[size] = model
    return model


def _whisper_size_cached(size: str) -> bool:
    """True if this size is already in memory or on disk (no network)."""
    size = (size or DEFAULT_ACCURATE_MODEL).strip() or DEFAULT_ACCURATE_MODEL
    if size in _WHISPER_MODEL_CACHE:
        return True
    try:
        from faster_whisper.utils import download_model

        download_model(size, local_files_only=True)
        return True
    except TypeError:
        # Older faster-whisper without local_files_only — fall through to hub cache scan.
        pass
    except Exception:
        pass
    try:
        from huggingface_hub.constants import HF_HUB_CACHE

        folder = os.path.join(HF_HUB_CACHE, f"models--Systran--faster-whisper-{size}")
        if os.path.isdir(folder):
            snap = os.path.join(folder, "snapshots")
            if os.path.isdir(snap) and any(os.scandir(snap)):
                return True
    except Exception:
        pass
    return False


def installed_whisper_sizes() -> list[str]:
    """Which of WHISPER_TRANSCRIPTION_SIZES are actually on disk/memory right now.
    Used to filter transcription-model pickers to what the user actually installed."""
    ok, _ = transcription_deps_available()
    if not ok:
        return []
    return [size for size in WHISPER_TRANSCRIPTION_SIZES if _whisper_size_cached(size)]


def whisper_ready(*, accurate_size: str | None = None) -> bool:
    """True when faster-whisper and the given transcription tier are on disk/memory."""
    ok, _ = transcription_deps_available()
    if not ok:
        return False
    accurate = resolve_whisper_model(accurate_size)
    return _whisper_size_cached(accurate)


def ensure_whisper_model(
    *, accurate_size: str | None = None, on_percent: Callable[[float], None] | None = None
) -> tuple[bool, str]:
    """Install-time ensure: load the given transcription tier so it's cached for next use."""
    ok, missing = transcription_deps_available()
    if not ok:
        return False, missing
    accurate = resolve_whisper_model(accurate_size)
    try:
        _get_whisper_model(accurate, on_percent=on_percent)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _prepare_wav_for_ollama_audio(filepath: str) -> tuple[str, str | None]:
    """
    Return (wav_path, temp_to_delete).
    Gemma 4 via Ollama expects WAV with RIFF header (16 kHz mono ideal).
    """
    import tempfile

    from services.ffmpeg_util import ffmpeg_available, run_ffmpeg

    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".wav":
        return filepath, None

    if not ffmpeg_available():
        return "", "ffmpeg not found"

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    try:
        run_ffmpeg(
            [
                "-y",
                "-i",
                filepath,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                out.name,
            ],
            check=True,
            capture_output=True,
        )
        return out.name, out.name
    except Exception as exc:
        try:
            os.unlink(out.name)
        except OSError:
            pass
        return "", str(exc)


def transcribe_with_audio_llm(
    filepath: str,
    model: str,
    *,
    profile: dict | None = None,
    prompt: str = "Transcribe the attached audio verbatim. Output only the spoken words.",
) -> dict:
    """Transcribe using an Ollama audio-capable model (e.g. gemma4:e2b)."""
    import base64

    from services.llm_bridge import build_chat_request, chat
    from services.model_router import check_model_supports_audio

    filename = os.path.basename(filepath)
    if not check_model_supports_audio(model):
        return {
            "filename": filename,
            "type": "error",
            "content": f"Model `{model}` does not support audio input.",
        }

    wav_path, temp_path = _prepare_wav_for_ollama_audio(filepath)
    if not wav_path:
        from services.capability.gap_handler import GAP_FFMPEG

        return {
            "filename": filename,
            "type": "error",
            "content": GAP_FFMPEG,
        }

    try:
        with open(wav_path, "rb") as audio_file:
            audio_b64 = base64.b64encode(audio_file.read()).decode("ascii")
        kwargs, _ = build_chat_request(
            profile,
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                    "images": [audio_b64],
                }
            ],
            stream=False,
        )
        response = chat(**kwargs)
        text = ""
        if isinstance(response, dict):
            text = (response.get("message") or {}).get("content") or ""
        else:
            msg = getattr(response, "message", None)
            text = getattr(msg, "content", "") if msg else ""
        body = (text or "").strip()
        if not body:
            return {
                "filename": filename,
                "type": "error",
                "content": "Audio model returned an empty transcript.",
            }
        return {"filename": filename, "type": "text", "content": body}
    except Exception as exc:
        return {
            "filename": filename,
            "type": "error",
            "content": f"Audio model transcription failed: {exc}",
        }
    finally:
        if temp_path and os.path.isfile(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def query_wants_timestamps(query: str) -> bool:
    lower = (query or "").lower()
    return any(
        w in lower
        for w in (
            "timestamp",
            "time stamp",
            "timecode",
            "time code",
            "with times",
            "at what time",
            "when did they say",
        )
    )


def transcribe_media_file(
    filepath: str,
    *,
    model_size: str | None = None,
    include_timestamps: bool = False,
) -> dict:
    """
    Return {type, content, filename}.
    Uses faster-whisper when installed; otherwise a clear install hint.
    """
    filename = os.path.basename(filepath)
    ext = os.path.splitext(filepath)[1].lower()

    if ext in (".mp4", ".mkv", ".mov", ".avi"):
        audio_path, extract_err = _extract_audio_track(filepath)
        if not audio_path:
            from services.ffmpeg_util import ffmpeg_available, resolve_ffmpeg
            from services.capability.gap_handler import GAP_FFMPEG

            err_l = (extract_err or "").lower()
            if any(
                phrase in err_l
                for phrase in (
                    "does not contain any stream",
                    "no audio",
                    "no stream",
                    "invalid argument",
                )
            ) or "stream map" in err_l:
                return {
                    "filename": filename,
                    "type": "text",
                    "content": f"# Transcript: {filename}\n\nThis video has no audio track to transcribe.",
                }
            if ffmpeg_available():
                detail = extract_err or "Could not extract audio from this video."
                if "no audio" in detail.lower() or "does not contain any stream" in detail.lower():
                    return {
                        "filename": filename,
                        "type": "text",
                        "content": f"# Transcript: {filename}\n\nThis video has no audio track to transcribe.",
                    }
                ff = resolve_ffmpeg() or ""
                if ff:
                    detail = f"{detail} (ffmpeg: {ff})"
                return {
                    "filename": filename,
                    "type": "error",
                    "content": detail,
                }
            return {
                "filename": filename,
                "type": "error",
                "content": GAP_FFMPEG,
            }
        filepath = audio_path
        filename = os.path.basename(filepath)

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        from services.capability.gap_handler import GAP_TRANSCRIPTION

        return {
            "filename": filename,
            "type": "error",
            "content": GAP_TRANSCRIPTION,
        }

    try:
        size = resolve_whisper_model(model_size)
        model = _get_whisper_model(size)
        segments, info = model.transcribe(filepath, beam_size=1)
        lines = [f"# Transcript: {filename}", ""]
        prev_line = ""
        for seg in segments:
            text = _clean_transcript_segment((seg.text or "").strip())
            if not text:
                continue
            if text.lower() == prev_line.lower():
                continue
            prev_line = text
            if include_timestamps:
                lines.append(f"## [{_format_ts(seg.start)}]")
            lines.append(text)
            lines.append("")
        body = _clean_transcript_body("\n".join(lines).strip()) or "(empty transcript)"
        if getattr(info, "language", "") == "zh":
            # Whisper emits Simplified script for "zh" regardless of UI locale, same
            # as SenseVoice — route through the shared "traditional_chinese" setting
            # so both engines respect the same Traditional/Simplified preference.
            from pipeline.i18n import maybe_traditional

            body = maybe_traditional(body)
        return {"filename": filename, "type": "text", "content": body}
    except Exception as exc:
        return {"filename": filename, "type": "error", "content": f"Transcription failed: {exc}"}


def _clean_transcript_segment(text: str) -> str:
    import re

    out = (text or "").strip()
    out = re.sub(r"\b(\w+)(?:\s+\1\b){1,}", r"\1", out, flags=re.I)
    out = re.sub(r"(\b\w{1,4}\b)(?:\s+\1\b){2,}", r"\1", out, flags=re.I)
    out = re.sub(r"[.!?…]{2,}", ".", out)
    return out.strip()


def _clean_transcript_body(body: str) -> str:
    import re

    lines = []
    seen: set[str] = set()
    for raw in (body or "").splitlines():
        line = raw.strip()
        if not line:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        key = re.sub(r"\s+", " ", line.lower())
        if key in seen and not line.startswith("#"):
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines).strip()


def _format_ts(seconds: float) -> str:
    total = int(max(0, seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _extract_audio_track(video_path: str) -> tuple[str | None, str]:
    import tempfile

    from services.ffmpeg_util import ffmpeg_available, resolve_ffmpeg, run_ffmpeg

    if not ffmpeg_available():
        return None, "ffmpeg not found on PATH or in settings."

    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    try:
        proc = run_ffmpeg(
            [
                "-y",
                "-i",
                video_path,
                "-vn",
                "-acodec",
                "pcm_s16le",
                "-ar",
                "16000",
                "-ac",
                "1",
                out.name,
            ],
            check=True,
            capture_output=True,
        )
        if proc.returncode != 0:
            err = (proc.stderr or b"").decode("utf-8", errors="replace")[-400:]
            return None, err or f"ffmpeg exited {proc.returncode}"
        return out.name, ""
    except Exception as exc:
        try:
            os.unlink(out.name)
        except OSError:
            pass
        msg = str(exc).lower()
        if any(
            phrase in msg
            for phrase in (
                "does not contain any stream",
                "no audio",
                "invalid argument",
                "returned non-zero exit status",
            )
        ):
            return None, "This video has no audio track to transcribe."
        ff = resolve_ffmpeg() or "unknown"
        return None, f"{exc} (ffmpeg: {ff})"


def search_transcript_for_term(transcript_md: str, term: str) -> list[dict]:
    """Find timestamp sections mentioning term (for media Q&A)."""
    if not term or not transcript_md:
        return []
    pattern = re.compile(
        r"^## \[([^\]]+)\]\s*\n((?:(?!^## \[).)*)",
        re.MULTILINE | re.DOTALL,
    )
    hits = []
    lower_term = term.lower()
    for match in pattern.finditer(transcript_md):
        ts, block = match.group(1), match.group(2)
        if lower_term in block.lower():
            hits.append({"timestamp": ts, "excerpt": block.strip()[:500]})
    return hits
