# -*- coding: utf-8 -*-
"""Fully offline text-to-speech backends for voice reply.

LOMA Core Edition is documented as offline-only (see EDITIONS.md) — this module replaces
the previous edge-tts backend (which called Microsoft's cloud API over the internet, the
one genuine online dependency voice reply had) with two local options:

  - system voice — Windows SAPI (via pywin32) / macOS `say` / Linux espeak-ng. Zero extra
    download: whatever voice is already installed on the OS. Always available as a
    fallback if Piper synthesis fails, or if the user never picks a Piper voice at all.
  - Piper — a small local neural TTS (https://github.com/rhasspy/piper), much better
    quality than the system voice. NOT bundled — voice reply is off by default and isn't
    a documented Core Edition capability, so shipping ~60MB+ per language in every install
    isn't worth it for users who never turn it on. Offered instead as an optional pick (at
    least one male + one female option per spoken language) in the setup wizard's voice-
    reply step, or later via Settings / conversation mode if skipped there — see
    ui/components/setup_wizard.py and PIPER_VOICE_CATALOG below.

Both backends synthesize to a temporary WAV, then transcode to MP3 with pydub + the
bundled ffmpeg (imageio-ffmpeg) — the rest of the app's voice-reply pipeline (routes,
client-side playback queue) is written around serving .mp3 files, so producing MP3 here
keeps that pipeline unchanged instead of threading a second audio format through it.
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Callable

from services.plugins.paths import loma_app_data_root

# Every Piper voice is downloaded on demand into AppData — none are bundled.
PIPER_VOICES_DIR = os.path.join(loma_app_data_root(), "tts", "piper_voices")
_PIPER_HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

# LOMA localizes into 5 written locales (en, zh_tw, zh_cn, es, de) but only 4 *spoken*
# languages — Traditional and Simplified Chinese share the same spoken language, so
# zh_tw/zh_cn both map to the "zh" voice group.
SPOKEN_LANGUAGES: tuple[str, ...] = ("en", "de", "es", "zh")

_LOCALE_TO_SPOKEN_LANG = {
    "en": "en",
    "zh_tw": "zh",
    "zh_cn": "zh",
    "es": "es",
    "de": "de",
}


def spoken_lang_for_locale(locale: str | None) -> str:
    return _LOCALE_TO_SPOKEN_LANG.get((locale or "en").strip(), "en")


def _voice(
    voice_id: str,
    hf_subpath: str,
    label: str,
    gender: str,
    size_mb: int,
    *,
    num_speakers: int = 1,
    speaker_labels: dict[str, int] | None = None,
) -> dict:
    return {
        "id": voice_id,
        "hf_subpath": hf_subpath,
        "label": label,
        "gender": gender,
        "size_mb": size_mb,
        "num_speakers": num_speakers,
        # Only set for a multi-speaker model whose config *names* specific speaker
        # indices (e.g. sharvard's onnx.json literally maps {"M": 0, "F": 1}) — lets
        # wizard_voice_picks() offer a real male/female pick from it, and the Model
        # Library speaker picker show what each id actually is instead of just a number.
        "speaker_labels": speaker_labels,
    }


# A curated subset of the public rhasspy/piper-voices catalog — not exhaustive (that
# catalog has dozens of voices per language) — at least one male and one female voice per
# spoken language, offered as picks in the setup wizard's voice-reply step (or later via
# Settings / conversation mode). "es" genders are best-effort: Piper's own catalog
# (voices.json) doesn't label gender, unlike en/de/zh where the speaker is well-known.
#
# "vctk" is a multi-speaker model (109 voices in one ~73MB file) instead of a single-
# speaker one — downloaded once like any other entry, then a specific speaker is picked
# afterward in the Model Library (see _synthesize_piper's `#<speaker_id>` suffix), the
# same "download once, configure after" shape as LLM role assignment.
PIPER_VOICE_CATALOG: dict[str, list[dict]] = {
    "en": [
        _voice("en_US-lessac-medium", "en/en_US/lessac/medium", "Lessac — medium quality", "male", 63),
        _voice("en_US-amy-medium", "en/en_US/amy/medium", "Amy — medium quality", "female", 63),
        _voice(
            "en_GB-vctk-medium",
            "en/en_GB/vctk/medium",
            "VCTK — medium quality",
            "multi",
            74,
            num_speakers=109,
        ),
    ],
    "de": [
        _voice("de_DE-thorsten-medium", "de/de_DE/thorsten/medium", "Thorsten — medium quality", "male", 63),
        _voice("de_DE-kerstin-low", "de/de_DE/kerstin/low", "Kerstin — lower quality", "female", 61),
        # MLS is a 236-speaker corpus (mix of male/female, all different people from
        # Thorsten/Kerstin) in one ~73MB file — no per-speaker gender labels are published,
        # so unlike sharvard below this is Model-Library-only (Preview each one to pick),
        # not a wizard quick-pick.
        _voice(
            "de_DE-mls-medium",
            "de/de_DE/mls/medium",
            "MLS — medium quality",
            "multi",
            73,
            num_speakers=236,
        ),
    ],
    "es": [
        _voice("es_ES-davefx-medium", "es/es_ES/davefx/medium", "Davefx — medium quality", "male", 63),
        # sharvard's own onnx.json labels its 2 speakers {"M": 0, "F": 1} — real labels,
        # not a guess (unlike the other "es" genders above/below). wizard_voice_picks()
        # uses speaker 1 as the wizard's female pick; speaker 0 (male, different person
        # from davefx) is reachable via the Model Library's speaker picker.
        _voice(
            "es_ES-sharvard-medium",
            "es/es_ES/sharvard/medium",
            "Sharvard — medium quality",
            "multi",
            74,
            num_speakers=2,
            speaker_labels={"male": 0, "female": 1},
        ),
    ],
    "zh": [
        _voice("zh_CN-huayan-medium", "zh/zh_CN/huayan/medium", "Huayan — medium quality", "female", 63),
        _voice("zh_CN-chaowen-medium", "zh/zh_CN/chaowen/medium", "Chaowen — medium quality", "male", 63),
        # No second male Chinese voice exists in Piper's public catalog today — xiao_ya
        # (小雅) is the only other zh_CN option, gender best-effort from the name (no
        # labeled metadata, same caveat as "es" above). Needs piper-tts>=1.4 (g2pW
        # dependency) and its BZNSYP dataset is non-commercial-use licensed — flagged for
        # awareness, not enforced in code.
        _voice("zh_CN-xiao_ya-medium", "zh/zh_CN/xiao_ya/medium", "Xiao Ya (小雅) — medium quality", "female", 63),
    ],
}

_PIPER_SAMPLE_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/main"


def piper_sample_url(voice_id: str, speaker_id: int = 0) -> str:
    """A small (~150-250KB) sample clip streamable straight from HF — lets a user preview
    a VCTK speaker before committing to a pick, no download of the model itself needed."""
    entry = piper_catalog_entry(voice_id)
    if not entry:
        return ""
    return f"{_PIPER_SAMPLE_BASE}/{entry['hf_subpath']}/samples/speaker_{speaker_id}.mp3"


def split_voice_id(voice_id: str) -> tuple[str, int | None]:
    """"en_GB-vctk-medium#42" -> ("en_GB-vctk-medium", 42); anything without "#" -> (id, None)."""
    if "#" in voice_id:
        base, _, speaker = voice_id.partition("#")
        try:
            return base, int(speaker)
        except ValueError:
            return base, None
    return voice_id, None


def make_voice_id(base_id: str, speaker_id: int | None) -> str:
    return f"{base_id}#{speaker_id}" if speaker_id is not None else base_id


def piper_catalog_entry(voice_id: str) -> dict | None:
    base_id, _ = split_voice_id(voice_id)
    for entries in PIPER_VOICE_CATALOG.values():
        for entry in entries:
            if entry["id"] == base_id:
                return entry
    return None


def wizard_voice_picks(spoken_lang: str) -> dict[str, dict]:
    """{"male": entry, "female": entry} — the setup wizard's two headline picks for this
    language. Plain single-speaker male/female entries win first; a multi-speaker entry
    with labeled speakers (e.g. sharvard's real {"M": 0, "F": 1}) fills in any gender still
    missing, returned as a shallow copy whose "id" is the composite `base#speaker_id` so
    callers (install_voice_reply etc.) need no special-casing. Purely open-ended multi-
    speaker entries (VCTK, MLS — no speaker_labels) are Model Library-only, never a wizard
    pick, since there's no single "the" male/female speaker to default to."""
    entries = PIPER_VOICE_CATALOG.get(spoken_lang) or PIPER_VOICE_CATALOG["en"]
    picks: dict[str, dict] = {}
    for entry in entries:
        if entry["gender"] in ("male", "female"):
            picks.setdefault(entry["gender"], entry)
    for entry in entries:
        for gender, speaker_id in (entry.get("speaker_labels") or {}).items():
            if gender in picks:
                continue
            picks[gender] = {**entry, "id": make_voice_id(entry["id"], speaker_id)}
    return picks


def _configure_pydub_ffmpeg() -> None:
    from pydub import AudioSegment
    from services.ffmpeg_util import resolve_ffmpeg

    ffmpeg_path = resolve_ffmpeg()
    if ffmpeg_path:
        AudioSegment.converter = ffmpeg_path


def _wav_to_mp3(wav_path: str, mp3_path: str) -> bool:
    from pydub import AudioSegment

    _configure_pydub_ffmpeg()
    AudioSegment.from_wav(wav_path).export(mp3_path, format="mp3")
    return os.path.isfile(mp3_path) and os.path.getsize(mp3_path) > 200


def _rate_percent_to_sapi(rate: str) -> int:
    """SAPI Rate is an integer -10 (slowest) .. +10 (fastest); our UI uses the same
    percent strings edge-tts used ("-12%".."+16%") — map onto a similar-feeling range."""
    try:
        pct = int((rate or "+0%").replace("%", ""))
    except ValueError:
        pct = 0
    return max(-10, min(10, round(pct / 2.5)))


def sapi_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401

        return True
    except Exception:
        return False


_SAPI_LANG_KEYWORDS = {
    "zh": ("chinese", "huihui", "yaoyao", "kangkang"),
    "de": ("german", "deutsch", "hedda", "stefan", "katja"),
    "es": ("spanish", "español", "helena", "laura", "pablo", "sabina"),
}


_SYSTEM_VOICE_AVAIL_CACHE: dict[str, bool] = {}


def system_voice_available(spoken_lang: str) -> bool:
    """Whether this OS actually has a voice installed that can speak `spoken_lang` — used
    to keep the "Reply voice" dropdown from offering a system-voice option that will fail
    at synthesis time (e.g. "No zh system voice installed on Windows" on an English-only
    Windows install with no Chinese language pack). English is never gated: every backend
    has a usable default/fallback voice for it.

    Result is cached per language for the session — the answer only changes if the user
    installs/removes an OS language pack (a restart-level event), and each miss otherwise
    costs a full SAPI COM enumeration that resolve_voice_id/default_voice_for_locale/
    tts_voice_options now each trigger, several times per settings-dialog open."""
    if spoken_lang == "en":
        return True
    if spoken_lang in _SYSTEM_VOICE_AVAIL_CACHE:
        return _SYSTEM_VOICE_AVAIL_CACHE[spoken_lang]
    result = _compute_system_voice_available(spoken_lang)
    _SYSTEM_VOICE_AVAIL_CACHE[spoken_lang] = result
    return result


def _compute_system_voice_available(spoken_lang: str) -> bool:
    if sys.platform == "win32":
        if not sapi_available():
            return False
        try:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            try:
                speaker = win32com.client.Dispatch("SAPI.SpVoice")
                return _pick_sapi_voice(speaker, spoken_lang) is not None
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            return False
    if sys.platform == "darwin":
        import subprocess

        voice = _MACOS_SAY_VOICE.get(spoken_lang)
        if not voice:
            return False
        try:
            out = subprocess.check_output(["say", "-v", "?"], text=True, timeout=5)
            return any(line.split()[0] == voice for line in out.splitlines() if line.strip())
        except Exception:
            return False
    import shutil
    import subprocess

    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return False
    try:
        out = subprocess.check_output([exe, "--voices"], text=True, timeout=5)
        return spoken_lang in out
    except Exception:
        return False


def _pick_sapi_voice(voice, spoken_lang: str):
    """Best-effort match: SAPI voice availability depends entirely on what's installed on
    this Windows machine (language packs etc.), so this can't guarantee a locale-correct
    voice the way a cloud catalog could — it picks the closest match available, or the
    system default if nothing matches. English has no keyword list — anything not
    matching another language's keywords is treated as an acceptable English/default."""
    keywords = _SAPI_LANG_KEYWORDS.get(spoken_lang)
    try:
        voices = voice.GetVoices()
        all_keywords = {kw for kws in _SAPI_LANG_KEYWORDS.values() for kw in kws}
        for i in range(voices.Count):
            v = voices.Item(i)
            desc = (v.GetDescription() or "").lower()
            if keywords:
                if any(kw in desc for kw in keywords):
                    return v
            elif not any(kw in desc for kw in all_keywords):
                return v
    except Exception:
        pass
    return None


def _synthesize_sapi(text: str, spoken_lang: str, rate: str, out_mp3: str) -> tuple[bool, str]:
    import pythoncom
    import win32com.client

    tmp_wav = out_mp3 + ".sapi.wav"
    # voice_reply.py calls this from a plain threading.Thread (not nicegui's own COM-aware
    # helpers), and COM requires per-thread initialization — without it, Dispatch() raises
    # "CoInitialize has not been called" on every thread but the very first one that ever
    # touched COM in this process. That exception was being swallowed by the except/return
    # below, so voice reply just went completely silent instead of erroring visibly.
    pythoncom.CoInitialize()
    try:
        speaker = win32com.client.Dispatch("SAPI.SpVoice")
        stream = win32com.client.Dispatch("SAPI.SpFileStream")
        # SpeechStreamFileMode.SSFMCreateForWrite == 3 (avoids needing win32com "gen_py"
        # constants, which aren't reliably pre-generated in a frozen build).
        stream.Open(tmp_wav, 3, False)
        speaker.AudioOutputStream = stream
        matched = _pick_sapi_voice(speaker, spoken_lang)
        if matched is not None:
            speaker.Voice = matched
        elif spoken_lang != "en":
            # No installed SAPI voice matches this language (common on an English-only
            # Windows install with no Chinese/German/Spanish language pack) — speaking the
            # text through the default English engine produces near-silent or garbled
            # audio instead of a visible error, so fail loudly here instead.
            return False, f"No {spoken_lang} system voice installed on Windows"
        speaker.Rate = _rate_percent_to_sapi(rate)
        speaker.Speak(text)
        stream.Close()
        if not os.path.isfile(tmp_wav) or os.path.getsize(tmp_wav) < 200:
            return False, "SAPI produced no audio"
        ok = _wav_to_mp3(tmp_wav, out_mp3)
        return ok, "" if ok else "wav->mp3 conversion failed"
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
    finally:
        try:
            if os.path.isfile(tmp_wav):
                os.remove(tmp_wav)
        except OSError:
            pass
        pythoncom.CoUninitialize()


_MACOS_SAY_VOICE = {"en": "Samantha", "de": "Anna", "es": "Monica", "zh": "Tingting"}


def _synthesize_macos_say(text: str, spoken_lang: str, rate: str, out_mp3: str) -> tuple[bool, str]:
    import subprocess

    tmp_aiff = out_mp3 + ".say.aiff"
    try:
        wpm = 175 + _rate_percent_to_sapi(rate) * 15  # macOS `say` rate is words/minute
        voice = _MACOS_SAY_VOICE.get(spoken_lang, "Samantha")
        try:
            subprocess.check_call(["say", "-v", voice, "-r", str(wpm), "-o", tmp_aiff, text])
        except Exception:
            # Named voice not installed on this Mac — fall back to the system default.
            subprocess.check_call(["say", "-r", str(wpm), "-o", tmp_aiff, text])
        if not os.path.isfile(tmp_aiff) or os.path.getsize(tmp_aiff) < 200:
            return False, "say produced no audio"
        _configure_pydub_ffmpeg()
        from pydub import AudioSegment

        AudioSegment.from_file(tmp_aiff, format="aiff").export(out_mp3, format="mp3")
        return os.path.isfile(out_mp3), ""
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
    finally:
        try:
            if os.path.isfile(tmp_aiff):
                os.remove(tmp_aiff)
        except OSError:
            pass


def _synthesize_espeak(text: str, spoken_lang: str, rate: str, out_mp3: str) -> tuple[bool, str]:
    import shutil
    import subprocess

    exe = shutil.which("espeak-ng") or shutil.which("espeak")
    if not exe:
        return False, "espeak-ng not found on PATH"
    tmp_wav = out_mp3 + ".espeak.wav"
    try:
        wpm = 175 + _rate_percent_to_sapi(rate) * 15
        voice = spoken_lang if spoken_lang in ("de", "es", "zh") else "en"
        subprocess.check_call([exe, "-v", voice, "-s", str(wpm), "-w", tmp_wav, text])
        ok = _wav_to_mp3(tmp_wav, out_mp3)
        return ok, "" if ok else "wav->mp3 conversion failed"
    except Exception as exc:
        return False, str(exc) or type(exc).__name__
    finally:
        try:
            if os.path.isfile(tmp_wav):
                os.remove(tmp_wav)
        except OSError:
            pass


def system_tts_available() -> bool:
    if sys.platform == "win32":
        return sapi_available()
    if sys.platform == "darwin":
        import shutil

        return shutil.which("say") is not None
    import shutil

    return shutil.which("espeak-ng") is not None or shutil.which("espeak") is not None


def synthesize_system_voice(text: str, spoken_lang: str, rate: str, out_mp3: str) -> tuple[bool, str]:
    """The zero-download default backend for this OS. `spoken_lang` is one of
    SPOKEN_LANGUAGES ("en"/"de"/"es"/"zh")."""
    if sys.platform == "win32":
        return _synthesize_sapi(text, spoken_lang, rate, out_mp3)
    if sys.platform == "darwin":
        return _synthesize_macos_say(text, spoken_lang, rate, out_mp3)
    return _synthesize_espeak(text, spoken_lang, rate, out_mp3)


# --- Piper (bundled defaults + downloadable extra voices) --------------------


def piper_voice_path(voice_id: str) -> str | None:
    base_id, _ = split_voice_id(voice_id)
    onnx = os.path.join(PIPER_VOICES_DIR, f"{base_id}.onnx")
    cfg = os.path.join(PIPER_VOICES_DIR, f"{base_id}.onnx.json")
    if os.path.isfile(onnx) and os.path.isfile(cfg):
        return onnx
    return None


def delete_piper_voice(voice_id: str) -> tuple[bool, str]:
    base_id, _ = split_voice_id(voice_id)
    onnx = os.path.join(PIPER_VOICES_DIR, f"{base_id}.onnx")
    cfg = os.path.join(PIPER_VOICES_DIR, f"{base_id}.onnx.json")
    try:
        for path in (onnx, cfg):
            if os.path.isfile(path):
                os.remove(path)
        _PIPER_VOICE_CACHE.pop(onnx, None)
        return True, ""
    except OSError as exc:
        return False, str(exc) or type(exc).__name__


def piper_package_ready() -> bool:
    try:
        import piper  # noqa: F401

        return True
    except Exception:
        return False


def piper_ready(voice_id: str) -> bool:
    return piper_package_ready() and piper_voice_path(voice_id) is not None


def installed_piper_voice_ids() -> list[str]:
    """Every catalog voice id currently usable (bundled or downloaded) — drives the
    "Reply voice" dropdown."""
    if not piper_package_ready():
        return []
    out = []
    for entries in PIPER_VOICE_CATALOG.values():
        for entry in entries:
            if piper_voice_path(entry["id"]):
                out.append(entry["id"])
    return out


def download_piper_voice(
    voice_id: str,
    on_progress: Callable[[str], None] | None = None,
    on_percent: Callable[[float], None] | None = None,
) -> tuple[bool, str]:
    """Download one catalog Piper voice (onnx + config) into AppData. The 4 per-language
    defaults are already bundled with the app (see packaging/loma_core.spec) and never
    hit this path — this is for the extra voices offered in the Model Library. The .onnx
    dominates total size (20-140MB depending on quality tier), so on_percent reports byte
    progress on that file only — the tiny .json config that follows is effectively instant."""
    import requests
    from services.net_errors import is_offline_error

    entry = piper_catalog_entry(voice_id)
    if not entry:
        return False, f"Unknown voice id: {voice_id}"
    base_id = entry["id"]
    os.makedirs(PIPER_VOICES_DIR, exist_ok=True)
    for suffix in (".onnx", ".onnx.json"):
        url = f"{_PIPER_HF_BASE}/{entry['hf_subpath']}/{base_id}{suffix}"
        dest = os.path.join(PIPER_VOICES_DIR, f"{base_id}{suffix}")
        if os.path.isfile(dest):
            continue
        try:
            if on_progress:
                on_progress(f"Downloading {base_id}{suffix}…")
            with requests.get(url, stream=True, timeout=30) as resp:
                resp.raise_for_status()
                total = int(resp.headers.get("Content-Length") or 0)
                downloaded = 0
                tmp = dest + ".part"
                with open(tmp, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if on_percent and total > 0:
                            on_percent(min(0.99, downloaded / total))
                        if on_progress and total > 0:
                            pct = int(100 * downloaded / total)
                            on_progress(f"Downloading {base_id}{suffix}… {pct}%")
                os.replace(tmp, dest)
        except Exception as exc:
            from services.net_errors import friendly_net_error

            return False, friendly_net_error(exc) if is_offline_error(exc) else f"{base_id}{suffix}: {exc}"
    if on_percent:
        on_percent(1.0)
    return True, ""


def _to_simplified(text: str) -> str:
    """All Piper Chinese voices in the catalog are zh_CN (Simplified) — Piper has no
    Traditional-Chinese voice yet. Traditional and Simplified characters for the same
    word are pronounced identically (that's the whole basis for treating zh_tw/zh_cn as
    one spoken language — see spoken_lang_for_locale), but the zh_CN model's espeak-ng-
    based phonemizer only has Simplified characters in its lookup table, so Traditional
    text fed to it directly comes out mispronounced/garbled. Converting first fixes this
    losslessly for pronunciation purposes."""
    try:
        import opencc

        return opencc.OpenCC("t2s").convert(text)
    except Exception:
        return text


_PIPER_VOICE_CACHE: dict[str, object] = {}
_PIPER_VOICE_LOAD_LOCK = threading.Lock()


def _load_piper_voice(model_path: str):
    """PiperVoice.load() re-parses the onnx model AND (for pinyin/g2pW voices like
    xiao_ya) rebuilds ChinesePhonemizer from scratch — reloading its BERT tokenizer and
    g2pW ONNX converter — every single call. Espeak-based voices (huayan, chaowen, every
    non-zh voice) have no such heavy per-load state, so this was invisible for them; for
    xiao_ya specifically it meant every reply re-paid that full initialization cost, which
    is exactly the "English is fine, Chinese is bad" latency gap this fixes. Cached per
    model path — a PiperVoice instance carries no per-call state (synthesize_wav takes the
    text and speaker each call), so reusing it across calls is safe.

    Guarded by a lock: the background startup warm-up (warm_tts_voice_background) and the
    first real synthesis request race to load the same voice on app startup. Without this
    lock both would see an empty cache and call PiperVoice.load() concurrently — for
    xiao_ya that means two threads simultaneously initializing the g2pW ONNX
    session/BERT tokenizer, which isn't safe and was the actual cause of the first reply
    failing right after startup (fixed by reload — by then the warm-up thread had already
    finished and there was no longer a second concurrent load to race against)."""
    voice = _PIPER_VOICE_CACHE.get(model_path)
    if voice is not None:
        return voice
    with _PIPER_VOICE_LOAD_LOCK:
        voice = _PIPER_VOICE_CACHE.get(model_path)
        if voice is None:
            from piper.voice import PiperVoice

            voice = PiperVoice.load(model_path)
            _PIPER_VOICE_CACHE[model_path] = voice
        return voice


def warm_piper_voice(voice_id: str) -> None:
    """Load the given voice into _PIPER_VOICE_CACHE ahead of time — call on save/reload and
    at startup for the currently-selected voice, so the first real synthesis doesn't pay the
    cold-start cost (heaviest for pinyin/g2pW voices like xiao_ya, see _load_piper_voice) that
    can otherwise surface as a "channels not specified" WAV-header error on an empty first
    chunk. No-op for a bare system-voice code or a voice that isn't downloaded."""
    if not voice_id or not piper_ready(voice_id):
        return
    model_path = piper_voice_path(voice_id)
    if not model_path:
        return
    try:
        _load_piper_voice(model_path)
    except Exception:
        pass


def _synthesize_piper(text: str, voice_id: str, out_mp3: str, rate: str = "+0%") -> tuple[bool, str]:
    model_path = piper_voice_path(voice_id)
    if not model_path:
        return False, "Piper voice model not installed"
    entry = piper_catalog_entry(voice_id)
    if entry and entry["id"].split("_")[0] == "zh":
        text = _to_simplified(text)
    _base_id, speaker_id = split_voice_id(voice_id)
    try:
        import wave

        voice = _load_piper_voice(model_path)
        tmp_wav = out_mp3 + ".piper.wav"
        syn_config = None
        # Reply speed: "+20%" = 20% faster -> shorter phoneme length (length_scale < 1).
        try:
            pct = int((rate or "+0%").replace("%", ""))
        except ValueError:
            pct = 0
        length_scale = max(0.5, min(2.0, 1.0 / (1.0 + pct / 100.0)))
        if speaker_id is not None or pct != 0:
            from piper.config import SynthesisConfig

            syn_config = SynthesisConfig(speaker_id=speaker_id, length_scale=length_scale)
        with wave.open(tmp_wav, "wb") as wav_file:
            voice.synthesize_wav(text, wav_file, syn_config=syn_config)
        ok = _wav_to_mp3(tmp_wav, out_mp3)
        try:
            os.remove(tmp_wav)
        except OSError:
            pass
        return ok, "" if ok else "wav->mp3 conversion failed"
    except Exception as exc:
        return False, str(exc) or type(exc).__name__


def synthesize_offline(text: str, voice_id: str, rate: str, out_mp3: str) -> tuple[bool, str]:
    """voice_id is either a bare spoken-language code ("en"/"de"/"es"/"zh" — the system
    voice) or a Piper catalog id ("en_US-lessac-medium" etc). Falls back to the system
    voice if the requested Piper voice isn't ready rather than producing no audio."""
    entry = piper_catalog_entry(voice_id)
    piper_err = ""
    if entry and piper_ready(voice_id):
        ok, piper_err = _synthesize_piper(text, voice_id, out_mp3, rate)
        if ok:
            return True, ""
    spoken_lang = entry["id"].split("_")[0] if entry else voice_id
    if spoken_lang not in SPOKEN_LANGUAGES:
        spoken_lang = "en"
    ok, sys_err = synthesize_system_voice(text, spoken_lang, rate, out_mp3)
    if ok:
        return True, ""
    # The Piper voice was the one actually picked — if it's the one that failed, surface
    # that reason instead of the unrelated system-voice fallback error (previously
    # discarded), which was misleading users into thinking a downloaded/selected Piper
    # voice was somehow "not pointing to" the right model when the real failure was
    # inside Piper synthesis itself (e.g. a missing phonemizer dependency for that voice).
    if piper_err:
        return False, f"{entry['label']}: {piper_err}"
    return False, sys_err
