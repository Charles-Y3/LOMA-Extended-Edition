# -*- coding: utf-8 -*-
"""Local voice input — faster-whisper transcription (no cloud speech API)."""
from __future__ import annotations

import importlib.util
import os
import re
import uuid

VOICE_INPUT_DIR = os.path.join("data", "temp", "voice_input")

# Live segments whose end is closer than this to the edge of the audio are
# still likely to change on the next pass; keep them as preview only.
_STABLE_MARGIN_S = 2.5
_SAMPLE_RATE = 16000
_MIN_LANG_PROBABILITY = 0.5
# Fast tier for the hybrid-whisper live preview (see _transcribe_clip) — not
# user-configurable, same as the ASR lab's proven hybrid-whisper design.
_HYBRID_FAST_MODEL = "tiny"

# SenseVoiceSmall covers exactly these 5 languages — everything else falls
# back to whisper. It's a non-streaming (whole-buffer re-decode) model like
# whisper, and it has no per-word timestamps to slice a stable/preview split
# from the way whisper's segments do. First attempt just re-transcribed the
# WHOLE recording every live tick and replaced the displayed text outright —
# that worked for a short clip but meant every ~1.5s tick got progressively
# more expensive as a dictation session went on (re-decoding a bigger and
# bigger buffer from scratch each time), which is exactly the growing
# latency that showed up in real use. Fixed the same way whisper's own tail
# offset works, adapted for a model with no word timing: once the trailing
# ~1s of the not-yet-committed tail goes quiet (simple RMS check — no VAD
# model needed), that tail is committed permanently and the offset advances,
# so every live tick only ever re-decodes "since the last pause" (normally a
# few seconds), not the whole session. Never cuts mid-utterance — the commit
# boundary is always a real pause, so this doesn't hit the accuracy collapse
# a naive fixed-size time window caused when tried on Moonshine.
_SENSEVOICE_LANGUAGES = {"en", "zh", "yue", "ja", "ko"}
_SENSEVOICE_MODEL_CACHE: dict[str, object] = {}
_SENSEVOICE_TAG_RE = re.compile(r"<\|[^|]*\|>")
_SENSEVOICE_TRAIL_S = 1.0
_SENSEVOICE_SILENCE_RMS = 0.012
_SENSEVOICE_HF_ID = "FunAudioLLM/SenseVoiceSmall"
_SENSEVOICE_MS_ID = "iic/SenseVoiceSmall"


def _sensevoice_available() -> bool:
    try:
        return importlib.util.find_spec("funasr") is not None
    except Exception:
        return False


def _sensevoice_root_dirs() -> list[str]:
    """Top-level on-disk locations SenseVoice could have been installed into — the whole
    tree under each is what actually owns the downloaded weights, used both to search for
    a usable checkout (_sensevoice_local_candidates) and to fully uninstall
    (delete_sensevoice_model)."""
    home = os.path.expanduser("~")
    return [
        os.path.join("data", "models", "SenseVoiceSmall"),
        os.path.join("models", "SenseVoiceSmall"),
        os.path.join(home, ".cache", "modelscope", "hub", "iic", "SenseVoiceSmall"),
        os.path.join(home, ".cache", "modelscope", "hub", "models", "iic", "SenseVoiceSmall"),
        os.path.join(home, ".cache", "huggingface", "hub", "models--FunAudioLLM--SenseVoiceSmall"),
    ]


def _sensevoice_local_candidates() -> list[str]:
    """Known on-disk locations — try these before any hub download."""
    roots = list(_sensevoice_root_dirs())
    # HF hub layout: models--Org--Name/snapshots/<hash>
    hf_root = next((r for r in roots if os.path.basename(r) == "models--FunAudioLLM--SenseVoiceSmall"), None)
    if hf_root and os.path.isdir(hf_root):
        snap = os.path.join(hf_root, "snapshots")
        if os.path.isdir(snap):
            for name in sorted(os.listdir(snap)):
                roots.append(os.path.join(snap, name))
    out: list[str] = []
    for path in roots:
        if path and os.path.isdir(path) and path not in out:
            # Only accept dirs that actually look like a real model checkout — a bare
            # "SenseVoiceSmall"-named directory with none of these files (e.g. left behind
            # empty/partial by an earlier failed install) used to be accepted anyway,
            # which meant every future load attempt kept retrying and failing against the
            # same stale empty folder instead of falling through to HF/ModelScope.
            if os.path.isfile(os.path.join(path, "config.yaml")) or os.path.isfile(
                os.path.join(path, "configuration.json")
            ) or os.path.isfile(os.path.join(path, "model.pt")):
                out.append(path)
    return out


def _ensure_sensevoice_registered() -> None:
    """Belt-and-suspenders: guarantee SenseVoiceSmall (and its encoder) are in funasr's
    class registry before AutoModel tries to look them up. funasr normally self-registers
    all model classes via an import-time pkgutil.walk_packages() scan, which needs funasr's
    real .py files on disk to enumerate — the packaged build bundles them exactly so that
    works (packaging/loma_core.spec). This force-import is a cheap guard against that ever
    silently regressing again: it directly runs the `@tables.register(...)` decorators in
    sense_voice/model.py, which is what raised "model 'SenseVoiceSmall' is not registered.
    Registered model keys (14): ..." when the walk enumerated almost nothing in a frozen
    build. No-op once registered."""
    try:
        from funasr.register import tables

        if "SenseVoiceSmall" in getattr(tables, "model_classes", {}):
            return
        import funasr.models.sense_voice.model  # noqa: F401  (runs the register decorators)
    except Exception:
        pass


def _auto_model(model_ref: str, *, hub: str = "ms"):
    from funasr import AutoModel

    _ensure_sensevoice_registered()

    # funasr's AutoModel defaults to hub="ms" (ModelScope) regardless of where model_ref
    # actually came from — download_model() (funasr/download/download_model_from_hub.py)
    # picks its config-parsing branch (download_from_ms vs download_from_hf) purely off
    # this kwarg, not off where the directory was downloaded from. Passing an HF
    # snapshot_download() path through with the default "ms" hub runs it through the
    # ModelScope-specific configuration.json/config.yaml parsing, which doesn't reliably
    # match HF's snapshot layout — silently leaves kwargs["model"] unresolved and fails
    # deeper in construction with a cryptic FileNotFoundError instead of a clean "wrong
    # hub" error. Natively registered in funasr — do NOT pass trust_remote_code=True.
    return AutoModel(model=model_ref, device="cpu", disable_update=True, hub=hub)


def _get_sensevoice_model():
    """Load SenseVoice: local path first, then HuggingFace, then ModelScope."""
    model = _SENSEVOICE_MODEL_CACHE.get("model")
    if model is not None:
        return model

    last_err: Exception | None = None
    for local in _sensevoice_local_candidates():
        try:
            model = _auto_model(local)
            _SENSEVOICE_MODEL_CACHE["model"] = model
            _SENSEVOICE_MODEL_CACHE["source"] = f"local:{local}"
            return model
        except Exception as exc:
            last_err = exc

    # HuggingFace primary (snapshot to cache, then construct from local path).
    try:
        from huggingface_hub import snapshot_download

        hf_path = snapshot_download(repo_id=_SENSEVOICE_HF_ID)
        model = _auto_model(hf_path, hub="hf")
        _SENSEVOICE_MODEL_CACHE["model"] = model
        _SENSEVOICE_MODEL_CACHE["source"] = f"hf:{_SENSEVOICE_HF_ID}"
        return model
    except Exception as exc:
        last_err = exc

    # ModelScope automatic fallback.
    try:
        model = _auto_model(_SENSEVOICE_MS_ID, hub="ms")
        _SENSEVOICE_MODEL_CACHE["model"] = model
        _SENSEVOICE_MODEL_CACHE["source"] = f"ms:{_SENSEVOICE_MS_ID}"
        return model
    except Exception as exc:
        last_err = exc
        raise RuntimeError(
            f"SenseVoice load failed (local/HF/ModelScope): {last_err}"
        ) from exc


def sensevoice_downloaded() -> bool:
    """True if funasr is installed and a SenseVoice checkout already exists on disk —
    a pure disk check, no load, no network. Used to gate UI that should only appear
    once the model is actually available (e.g. the Traditional Chinese toggle)."""
    return _sensevoice_available() and bool(_sensevoice_local_candidates())


def delete_sensevoice_model() -> tuple[bool, str]:
    """Remove every on-disk SenseVoice checkout this app could have created or downloaded
    into (data/models, HF hub cache, ModelScope hub cache) — there was previously no way
    to uninstall it once downloaded (~1GB), unlike every other model in the Model Library."""
    import shutil

    _SENSEVOICE_MODEL_CACHE.pop("model", None)
    _SENSEVOICE_MODEL_CACHE.pop("source", None)
    errors: list[str] = []
    for path in _sensevoice_root_dirs():
        if os.path.isdir(path):
            try:
                shutil.rmtree(path)
            except OSError as exc:
                errors.append(f"{path}: {exc}")
    return not errors, "; ".join(errors)


def sensevoice_model_cached() -> bool:
    """True only if the model is already loaded in memory — unlike sensevoice_ready(),
    never triggers a disk load as a side effect. Use this to decide whether a caller
    needs to show a "loading, first time…" notice before the real check/load."""
    return _SENSEVOICE_MODEL_CACHE.get("model") is not None


def sensevoice_ready() -> bool:
    """True when funasr is installed and a SenseVoice runtime can be obtained locally
    (or is already cached in-process). Does not probe the network first."""
    if not _sensevoice_available():
        return False
    if _SENSEVOICE_MODEL_CACHE.get("model") is not None:
        return True
    for local in _sensevoice_local_candidates():
        try:
            model = _auto_model(local)
            _SENSEVOICE_MODEL_CACHE["model"] = model
            _SENSEVOICE_MODEL_CACHE["source"] = f"local:{local}"
            return True
        except Exception:
            continue
    return False


def ensure_sensevoice_model() -> tuple[bool, str]:
    """Install-time / first-use ensure: local → HF → ModelScope. May download."""
    if not _sensevoice_available():
        return False, "funasr"
    try:
        _get_sensevoice_model()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def recommend_voice_engines(languages: list[str] | set[str]) -> str:
    """Return 'sensevoice' | 'whisper' | 'both' from selected dictation languages."""
    langs = {(x or "").strip().lower() for x in languages if (x or "").strip()}
    langs.discard("auto")
    if not langs:
        return "both"
    sv = langs <= _SENSEVOICE_LANGUAGES
    none_sv = langs.isdisjoint(_SENSEVOICE_LANGUAGES)
    if sv:
        return "sensevoice"
    if none_sv:
        return "whisper"
    return "both"


def voice_input_missing(*, want_sensevoice: bool, want_whisper: bool) -> list[str]:
    missing: list[str] = []
    if want_sensevoice and not sensevoice_ready():
        missing.append("sensevoice")
    if want_whisper:
        from services.media_transcription import whisper_ready

        if not whisper_ready():
            missing.append("whisper")
    return missing


def voice_engines_ready(*, want_sensevoice: bool, want_whisper: bool) -> bool:
    return not voice_input_missing(want_sensevoice=want_sensevoice, want_whisper=want_whisper)


def transcribe_voice_stream(
    audio_bytes: bytes,
    *,
    filename: str = "voice.webm",
    model_size: str = "base",
    language: str = "",
    mode: str = "final",
    offset: float = 0.0,
) -> dict:
    """
    Transcribe a mic clip locally. Returns
    {ok, text, preview, language, offset, replace, error}.

    Engine is chosen by `language`: "en"/"zh" route to SenseVoice (if funasr
    is installed) since it covers those with much lower latency; everything
    else uses whisper. Both engines only ever transcribe audio past `offset`
    in live mode, so per-tick cost stays bounded regardless of how long the
    recording runs — whisper slices by word-end timestamp, SenseVoice (no
    timestamps available) commits once a real pause is detected in the tail.

    mode="live": `text` is newly-settled speech to APPEND, `preview` is the
    still-changing uncommitted tail, `offset` advances only when something
    was committed, `replace` is always False.

    mode="final": one accurate pass over the whole clip; `text` is
    everything and `replace` is True (whole-clip result replaces whatever
    partial text the live ticks had built up).
    """
    lang = (language or "").strip()
    empty = {"ok": False, "text": "", "preview": "", "language": lang, "offset": offset, "replace": False, "error": ""}
    if not audio_bytes or len(audio_bytes) < 200:
        return {**empty, "error": "Recording too short."}

    # sensevoice_downloaded() (disk check), not _sensevoice_available() (funasr import
    # check) — funasr ships bundled unconditionally so the import check is always True;
    # gating on it alone let any caller of this function silently trigger a ~1GB HF/
    # ModelScope download with no consent (the UI-level gate in
    # ui/components/voice_input_installer.ensure_voice_before_record is meant to be the
    # only place that ever starts one). See services/voice_input.py memory notes.
    use_sensevoice = lang in _SENSEVOICE_LANGUAGES and sensevoice_downloaded()
    if not use_sensevoice:
        from services.media_transcription import resolve_whisper_model, transcription_deps_available, whisper_ready

        ok, missing = transcription_deps_available()
        if not ok:
            return {**empty, "error": f"Local transcription requires: {missing}. Install faster-whisper."}
        if not whisper_ready(accurate_size=resolve_whisper_model(model_size or None)):
            return {
                **empty,
                "error": "Hybrid Whisper models not ready (need tiny + accurate). Install voice input.",
            }

    os.makedirs(VOICE_INPUT_DIR, exist_ok=True)
    ext = os.path.splitext(filename or "")[1].lower() or ".webm"
    if ext not in (".webm", ".wav", ".mp3", ".m4a", ".ogg", ".mp4"):
        ext = ".webm"
    path = os.path.join(VOICE_INPUT_DIR, f"{uuid.uuid4().hex}{ext}")
    try:
        with open(path, "wb") as f:
            f.write(audio_bytes)
        if use_sensevoice:
            return _transcribe_clip_sensevoice(
                path, language=lang, live=(mode == "live"), offset=max(0.0, float(offset or 0.0))
            )
        return _transcribe_clip(
            path,
            model_size=resolve_whisper_model(model_size or None),
            language=lang,
            live=(mode == "live"),
            offset=max(0.0, float(offset or 0.0)),
        )
    except Exception as exc:
        return {**empty, "error": f"Transcription failed: {exc}"}
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _rms(audio) -> float:
    import numpy as np

    if len(audio) == 0:
        return 0.0
    return float(np.sqrt(np.mean(audio.astype("float64") ** 2)))


def _is_trailing_silence(audio, seconds: float, threshold: float) -> bool:
    trail_samples = int(seconds * _SAMPLE_RATE)
    trail = audio[-trail_samples:] if len(audio) >= trail_samples else audio
    return len(trail) == 0 or _rms(trail) < threshold


def _sensevoice_generate(audio, language: str) -> tuple[str, str]:
    """Returns (clean_text, detected_language).

    Guards on sensevoice_downloaded() (disk check) rather than calling _get_sensevoice_model()
    unconditionally — this is the single choke point behind live_tick/live_stop and
    _transcribe_clip_sensevoice (the mic button's actual live-dictation path, which has no
    UI-level gate of its own), so without this check any of those could silently kick off a
    ~1GB HF/ModelScope download with no consent the moment the model isn't on disk. Callers
    that legitimately install/warm the model (ensure_sensevoice_model) go through
    _get_sensevoice_model() directly and are unaffected."""
    if not sensevoice_downloaded():
        raise RuntimeError("SenseVoice model not installed")
    model = _get_sensevoice_model()
    res = model.generate(input=audio, cache={}, language=(language or "auto"), use_itn=True, batch_size_s=60)
    raw = (res[0].get("text", "") if res else "") or ""
    text = _SENSEVOICE_TAG_RE.sub("", raw).strip()
    m = re.match(r"<\|(\w+)\|>", raw)
    detected = m.group(1) if m and m.group(1) in ("en", "zh", "yue", "ja", "ko") else ""
    return text, detected


def _decode_audio_16k(path: str):
    """Decode mic clip to float32 mono @ 16 kHz. Prefer faster-whisper; fallback via ffmpeg."""
    try:
        from faster_whisper.audio import decode_audio

        return decode_audio(path, sampling_rate=_SAMPLE_RATE)
    except Exception:
        pass
    import tempfile

    import numpy as np

    from services.ffmpeg_util import ffmpeg_available, run_ffmpeg

    if not ffmpeg_available():
        raise RuntimeError("Cannot decode audio — install faster-whisper or ffmpeg.")
    out = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    out.close()
    try:
        run_ffmpeg(
            ["-y", "-i", path, "-acodec", "pcm_s16le", "-ar", str(_SAMPLE_RATE), "-ac", "1", out.name],
            check=True,
            capture_output=True,
        )
        import wave

        with wave.open(out.name, "rb") as wf:
            raw = wf.readframes(wf.getnframes())
            audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return audio
    finally:
        try:
            os.unlink(out.name)
        except OSError:
            pass


def _transcribe_clip_sensevoice(path: str, *, language: str, live: bool, offset: float) -> dict:
    audio = _decode_audio_16k(path)
    duration = len(audio) / _SAMPLE_RATE

    if not live:
        # One accurate pass over the whole clip, same as whisper's final mode.
        text, detected = _sensevoice_generate(audio, language)
        return {
            "ok": True, "text": text, "preview": "", "language": language or detected,
            "offset": duration, "replace": True, "error": "",
        }

    start = min(offset, duration)
    tail = audio[int(start * _SAMPLE_RATE):]
    tail_duration = len(tail) / _SAMPLE_RATE
    empty = {"ok": True, "text": "", "preview": "", "language": language, "offset": offset, "replace": False, "error": ""}
    if tail_duration < 0.4:
        return empty

    # SenseVoice has no VAD of its own and hallucinates short filler words
    # ("Yeah.", "Okay.") when fed audio that's silence throughout — same
    # failure mode whisper guards against with vad_filter=True. Skip the call
    # entirely rather than risk committing a hallucination as real text.
    if _rms(tail) < _SENSEVOICE_SILENCE_RMS:
        return empty

    text, detected = _sensevoice_generate(tail, language)
    pinned = language or detected

    paused = tail_duration >= _SENSEVOICE_TRAIL_S and _is_trailing_silence(
        tail, _SENSEVOICE_TRAIL_S, _SENSEVOICE_SILENCE_RMS
    )
    if paused and text:
        # Real pause reached — commit what's been said since the last commit
        # and advance the offset, so the NEXT tick starts a fresh, short tail
        # instead of re-decoding this segment again forever.
        return {"ok": True, "text": text, "preview": "", "language": pinned, "offset": duration, "replace": False, "error": ""}
    return {"ok": True, "text": "", "preview": text, "language": pinned, "offset": offset, "replace": False, "error": ""}


class _LiveSession:
    """Raw-PCM tail buffer for one SenseVoice live-dictation session.

    Unlike the WebM-blob path above, the client here sends only NEW audio
    each tick (already-decoded Int16 PCM @ 16kHz — no container/codec, so no
    per-tick re-decode of a growing clip). This mirrors the SOTA reference
    implementation's LiveTranscriber: the server holds the uncommitted tail
    in memory and only ever re-transcribes it, never the whole session.
    """

    def __init__(self, language: str):
        self.language = language
        self.pinned_lang = language
        self.chunks: list = []

    def tail(self):
        import numpy as np

        return np.concatenate(self.chunks) if self.chunks else np.zeros(0, dtype=np.float32)

    def append(self, pcm_bytes: bytes) -> None:
        import numpy as np

        if not pcm_bytes:
            return
        audio = np.frombuffer(pcm_bytes, dtype="<i2").astype(np.float32) / 32768.0
        self.chunks.append(audio)

    def commit(self) -> None:
        self.chunks = []


_LIVE_SESSIONS: dict[str, _LiveSession] = {}


def _maybe_traditional(text: str) -> str:
    """Re-exported for callers already importing this name (e.g. main.py) — the
    actual implementation lives in pipeline.i18n.maybe_traditional since it's not
    voice-specific (Whisper file transcription uses it too, via media_transcription.py)."""
    from pipeline.i18n import maybe_traditional

    return maybe_traditional(text)


def live_start(language: str = "") -> str:
    """Begin a SenseVoice live-dictation session; returns its session id."""
    session_id = uuid.uuid4().hex
    _LIVE_SESSIONS[session_id] = _LiveSession((language or "").strip())
    return session_id


def live_tick(session_id: str, pcm_bytes: bytes) -> dict:
    """Append one tick's raw PCM to the session tail and check for a commit.

    Same commit-on-pause logic as _transcribe_clip_sensevoice's live branch,
    just reading the tail from memory instead of decode-then-slice.
    """
    empty = {"ok": False, "text": "", "preview": "", "language": "", "error": ""}
    sess = _LIVE_SESSIONS.get(session_id)
    if sess is None:
        return {**empty, "error": "no-session"}
    sess.append(pcm_bytes)

    tail = sess.tail()
    tail_duration = len(tail) / _SAMPLE_RATE
    ok_empty = {"ok": True, "text": "", "preview": "", "language": sess.pinned_lang, "error": ""}
    if tail_duration < 0.4:
        return ok_empty
    if _rms(tail) < _SENSEVOICE_SILENCE_RMS:
        return ok_empty

    try:
        text, detected = _sensevoice_generate(tail, sess.language)
    except Exception as exc:
        return {**empty, "language": sess.pinned_lang, "error": str(exc)}
    if detected and not sess.language:
        sess.pinned_lang = detected

    paused = tail_duration >= _SENSEVOICE_TRAIL_S and _is_trailing_silence(
        tail, _SENSEVOICE_TRAIL_S, _SENSEVOICE_SILENCE_RMS
    )
    if paused and text:
        sess.commit()
        return {"ok": True, "text": _maybe_traditional(text), "preview": "", "language": sess.pinned_lang, "error": ""}
    return {"ok": True, "text": "", "preview": _maybe_traditional(text), "language": sess.pinned_lang, "error": ""}


def live_stop(session_id: str) -> dict:
    """Final accurate pass over whatever's left uncommitted, then drop the session."""
    sess = _LIVE_SESSIONS.pop(session_id, None)
    if sess is None:
        return {"ok": False, "text": "", "error": "no-session"}
    tail = sess.tail()
    if len(tail) / _SAMPLE_RATE < 0.3 or _rms(tail) < _SENSEVOICE_SILENCE_RMS:
        return {"ok": True, "text": "", "language": sess.pinned_lang, "error": ""}
    try:
        text, detected = _sensevoice_generate(tail, sess.language)
    except Exception as exc:
        return {"ok": False, "text": "", "language": sess.pinned_lang, "error": str(exc)}
    return {"ok": True, "text": _maybe_traditional(text.strip()), "language": sess.pinned_lang or detected, "error": ""}


def _whisper_segment_texts(segments) -> list[tuple[float, str]]:
    from services.media_transcription import _clean_transcript_segment

    texts: list[tuple[float, str]] = []
    prev = ""
    for seg in segments:
        text = _clean_transcript_segment((seg.text or "").strip())
        if not text or text.lower() == prev.lower():
            continue
        prev = text
        texts.append((seg.end, text))
    return texts


def _transcribe_clip(path: str, *, model_size: str, language: str, live: bool, offset: float) -> dict:
    from faster_whisper.audio import decode_audio

    from services.media_transcription import _get_whisper_model

    audio = decode_audio(path, sampling_rate=_SAMPLE_RATE)
    duration = len(audio) / _SAMPLE_RATE

    if live:
        start = min(offset, duration)
        audio = audio[int(start * _SAMPLE_RATE):]
    tail_duration = len(audio) / _SAMPLE_RATE
    if tail_duration < 0.4:
        return {"ok": True, "text": "", "preview": "", "language": language, "offset": offset, "replace": False, "error": ""}

    model = _get_whisper_model(model_size)
    segments, info = model.transcribe(
        audio,
        language=language or None,
        beam_size=1 if live else 5,
        vad_filter=True,
        condition_on_previous_text=False if live else True,
    )
    texts = _whisper_segment_texts(segments)

    # Pin the language once detection is confident; silence-only ticks return
    # no segments and must not pin a bogus guess.
    pinned = language
    if not pinned and texts and (getattr(info, "language_probability", 0) or 0) >= _MIN_LANG_PROBABILITY:
        pinned = getattr(info, "language", "") or ""

    if not live:
        full = " ".join(t for _, t in texts).strip()
        return {"ok": True, "text": full, "preview": "", "language": pinned, "offset": duration, "replace": False, "error": ""}

    stable_parts: list[str] = []
    stable_end = 0.0
    for end, text in texts:
        if end <= tail_duration - _STABLE_MARGIN_S:
            stable_parts.append(text)
            stable_end = end

    # Hybrid-whisper design (proven in the ASR lab): the accurate model above
    # decides what's stable enough to commit, same as before. For the
    # still-changing remainder, run the much faster "tiny" model instead of
    # trusting the accurate model's own (slower, same-pass) guess for that
    # tail — this is what gave hybrid-whisper its faster first-token time in
    # lab testing without hurting committed accuracy, since tiny's output
    # never gets committed, only shown as a rough, fast-updating preview.
    preview_audio = audio[int(stable_end * _SAMPLE_RATE):]
    preview_text = ""
    if len(preview_audio) / _SAMPLE_RATE >= 0.4:
        fast_model = _get_whisper_model(_HYBRID_FAST_MODEL)
        fast_segments, _fast_info = fast_model.transcribe(
            preview_audio, language=pinned or None, beam_size=1, vad_filter=True,
            condition_on_previous_text=False,
        )
        preview_text = " ".join(t for _, t in _whisper_segment_texts(fast_segments)).strip()

    return {
        "ok": True,
        "text": " ".join(stable_parts).strip(),
        "preview": preview_text,
        "language": pinned,
        "offset": offset + stable_end,
        "replace": False,
        "error": "",
    }
