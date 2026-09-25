# -*- coding: utf-8 -*-
"""Voice reply (TTS) for workspace chat.

Speaks the assistant's reply as it streams in rather than waiting for the
whole message to finish: as tokens arrive (see start_stream_reply /
feed_stream_token / finish_stream_reply) they're buffered until a complete
sentence forms, each sentence is synthesized on its own background thread,
and finished audio is pushed to the browser in order via a small JS playback
queue (so chunk 2 can be synthesizing while chunk 1 is still playing).
Non-streamed output (e.g. a translation or image-ready notice set in one
shot via sink.set_assistant_content) never calls feed_stream_token, so
finish_or_fallback_reply() falls back to speaking the whole message at once,
same as before.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

VOICE_DIR = os.path.join("data", "temp", "voice")
VOICE_FILE = os.path.join(VOICE_DIR, "latest.mp3")


# Offline TTS only — see services/tts_engines.py. LOMA's 5 written locales collapse to 4
# *spoken* languages ("en"/"de"/"es"/"zh" — Traditional and Simplified Chinese share one
# spoken language). Each entry in the dropdown is either a bare spoken-language code (the
# system voice — SAPI/say/espeak, whatever's already on this OS) or a Piper catalog voice
# id; one Piper voice per language ships bundled with the app (zero download), the rest
# are offered through the Model Library and only appear here once downloaded. Voices are
# labeled in their own language natively, not translated by the current UI locale — same
# convention as a language picker.
_SPOKEN_LANG_NATIVE_LABEL = {"en": "English", "de": "Deutsch", "es": "Español", "zh": "中文"}


def tts_voice_options() -> dict[str, str]:
    from services.session import state
    from services.tts_engines import (
        PIPER_VOICE_CATALOG,
        SPOKEN_LANGUAGES,
        make_voice_id,
        piper_voice_path,
        system_voice_available,
    )

    options: dict[str, str] = {}
    speaker_prefs = (state.current_settings or {}).get("piper_speaker_choices") or {}
    for lang in SPOKEN_LANGUAGES:
        native = _SPOKEN_LANG_NATIVE_LABEL[lang]
        if system_voice_available(lang):
            options[lang] = f"{native} — system voice"
        for entry in PIPER_VOICE_CATALOG.get(lang, []):
            if not piper_voice_path(entry["id"]):
                continue
            if entry["num_speakers"] > 1:
                # One configured speaker (picked in the Model Library) represents this
                # entry in the dropdown — listing all 109 VCTK speakers here isn't usable.
                speaker = int(speaker_prefs.get(entry["id"], 0))
                composite = make_voice_id(entry["id"], speaker)
                options[composite] = f"{native} — {entry['label']} (speaker {speaker})"
            else:
                options[entry["id"]] = f"{native} — {entry['label']}"
    return options


TTS_RATE_OPTIONS: dict[str, str] = {
    "-12%": "Slower",
    "+0%": "Normal",
    "+8%": "Faster",
    "+16%": "Fastest",
}

_play_generation = 0
_play_lock = threading.Lock()
_notified_failed_generations: set[int] = set()


def _notify_synthesis_failed(generation: int, error: str) -> None:
    """Surface a TTS failure to the user instead of leaving them with dead silence and no
    explanation (e.g. no matching system voice installed for the requested language) —
    once per turn, since a streamed reply can fail on several chunks in a row."""
    with _play_lock:
        if generation in _notified_failed_generations:
            return
        # Generations are monotonically increasing per turn — a new one means every
        # previous entry is stale, so drop them instead of growing this set forever.
        _notified_failed_generations.clear()
        _notified_failed_generations.add(generation)

    def _show() -> None:
        from nicegui import ui

        from pipeline.i18n import t as tr

        ui.notify(tr("voice.reply_synthesis_failed", error=error), type="negative")

    from services.session.workflow_control import schedule_on_ui

    schedule_on_ui(_show)

# --- streaming (sentence-chunked) state -----------------------------------
_TERMINATORS = ".!?。！？"
_CLOSERS = ")\"'”’]"
_MIN_CHUNK_CHARS = 6
_MAX_CHUNK_CHARS = 260

_stream_state_lock = threading.Lock()
_stream_generation = 0
_stream_buffer = ""
_stream_had_output = False
_chunk_seq = 0
_chunk_ready: dict[int, str | None] = {}
_chunk_next_send = 0
# Set once finish_stream_reply knows no more chunks will ever be dispatched
# for this generation (the trailing-sentence flush, if any, has been sent).
# Only once _chunk_next_send reaches this count has every chunk actually
# been delivered to the client — that combination is the real "done"
# signal; queue-empty-and-not-playing alone is also true in the (very
# common) gap between two chunks while the next one is still synthesizing.
_stream_total_chunks: int | None = None


def voice_reply_enabled(settings: dict | None) -> bool:
    return bool((settings or {}).get("voice_reply_enabled"))


def voice_reply_needs_pick(settings: dict | None) -> bool:
    """True if this locale's spoken language has no Piper voice picked/downloaded yet —
    resolve_voice_id() falls back to the bare system-voice code in that case. Used to
    decide whether turning voice reply on (Settings or conversation mode) should offer
    the pick-a-voice dialog; the system voice already works either way, so this is an
    upsell, not a blocker."""
    from services.tts_engines import SPOKEN_LANGUAGES

    return resolve_voice_id(settings) in SPOKEN_LANGUAGES


def default_voice_for_locale(locale: str | None = None) -> str:
    """The zero-download default reply voice for this locale — the locale's spoken-language
    system voice when that voice is actually installed on this OS, otherwise the first entry
    that IS available in the reply-voice dropdown (English system voice is always available,
    so this never returns nothing). MUST stay consistent with tts_voice_options(): returning
    a system voice that voice detection hides from the dropdown left ui.select with a value
    not in its options, which crashed the whole UI with "Invalid value: zh" when the locale
    was set to a language whose system voice isn't installed (e.g. Traditional Chinese on an
    English-only Windows)."""
    from pipeline.i18n import get_locale, normalize_locale
    from services.tts_engines import spoken_lang_for_locale, system_voice_available

    from services.tts_engines import piper_catalog_entry

    loc = normalize_locale(locale) if locale else get_locale()
    lang = spoken_lang_for_locale(loc)
    if system_voice_available(lang):
        return lang
    options = tts_voice_options()
    if lang in options:
        return lang
    # No system voice for this language — prefer a downloaded Piper voice in the SAME
    # spoken language (e.g. a zh_tw user who downloaded xiao_ya) before falling back to
    # another language's voice.
    for key in options:
        entry = piper_catalog_entry(key)
        if entry and entry["id"].split("_")[0] == lang:
            return key
    return next(iter(options), "en")


_CJK_RE = re.compile(r"[一-鿿]")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
# Very common function words — enough to tell German/Spanish/English apart on a sentence.
_LANG_STOPWORDS = {
    "de": {"der", "die", "das", "und", "ist", "nicht", "ich", "sie", "es", "ein", "eine", "mit", "für", "auf", "den", "zu", "von", "wir", "kann", "hallo"},
    "es": {"el", "la", "los", "las", "de", "que", "y", "es", "un", "una", "en", "por", "con", "para", "no", "se", "puedo", "hola", "está", "cómo"},
    "en": {"the", "and", "is", "are", "to", "of", "you", "in", "it", "that", "for", "with", "can", "this", "i", "how"},
}


def guess_text_lang(text: str | None) -> str | None:
    """Spoken-language guess for reply text: "zh"/"de"/"es"/"en", or None when unsure."""
    if not text:
        return None
    if len(_CJK_RE.findall(text)) >= 2:
        return "zh"
    words = [w.lower() for w in _WORD_RE.findall(text[:600])]
    if len(words) < 3:
        return None
    scores = {lang: sum(1 for w in words if w in sw) for lang, sw in _LANG_STOPWORDS.items()}
    best = max(scores, key=scores.get)
    if scores[best] < 2 or list(scores.values()).count(scores[best]) > 1:
        return None
    return best


def _installed_voice_for_lang(lang: str, saved: str, options) -> str | None:
    """The saved voice if it is an installed Piper voice in `lang`, else any installed one."""
    from services.tts_engines import piper_catalog_entry, piper_voice_path

    for key in [saved] + [k for k in options if k != saved]:
        entry = piper_catalog_entry(key) if key else None
        if entry and entry["id"].split("_")[0] == lang and piper_voice_path(key):
            return key
    return None


def resolve_voice_id(settings: dict | None, text: str | None = None) -> str:
    """Prefer saved voice when it's usable for the UI locale's spoken language AND actually
    selectable in the current dropdown; else the default for that locale. Always returns a
    value present in tts_voice_options() so the reply-voice ui.select never gets an
    out-of-range value (see default_voice_for_locale for the crash that guards against).

    When `text` (what is about to be spoken) is clearly in another language than the UI
    locale (e.g. a Chinese/German/Spanish reply under an English UI), the locale voice can't
    read it (the English system voice produces no audio for Chinese) — so use an installed
    Piper voice for the text's language, preferring the saved one."""
    from pipeline.i18n import get_locale
    from services.tts_engines import piper_catalog_entry, piper_voice_path, spoken_lang_for_locale

    settings = settings or {}
    loc = get_locale(settings)
    lang = spoken_lang_for_locale(loc)
    options = tts_voice_options()
    saved = (settings.get("tts_voice_id") or "").strip()
    text_lang = guess_text_lang(text)
    if text_lang and text_lang != lang:
        text_voice = _installed_voice_for_lang(text_lang, saved, options)
        if text_voice:
            return text_voice
    if saved and saved in options:
        if saved == lang:
            return saved
        entry = piper_catalog_entry(saved)
        if entry and entry["id"].split("_")[0] == lang and piper_voice_path(saved):
            return saved
    return default_voice_for_locale(loc)


def _client_js(js: str) -> None:
    try:
        from nicegui.client import Client

        for client in Client.instances.values():
            client.run_javascript(js)
    except Exception:
        pass


# Lazily-defined, idempotent client-side playback queue: audio for chunk N+1
# can arrive (be pushed) while chunk N is still playing; onended advances the
# queue automatically so chunks play back-to-back in order.
_QUEUE_INIT_JS = """
    (function() {
        if (window.__lomaVoiceInit) return;
        window.__lomaVoiceInit = true;
        window.__lomaVoiceQueue = [];
        window.__lomaVoicePlaying = false;
        window.__lomaVoiceGen = -1;
        window.__lomaVoiceCompleteGen = -1;
        window.__lomaVoiceAudio = null;
        window.__lomaVoicePlayNext = function() {
            if (window.__lomaVoiceQueue.length === 0) { window.__lomaVoicePlaying = false; return; }
            window.__lomaVoicePlaying = true;
            const item = window.__lomaVoiceQueue.shift();
            const a = new Audio(item.url);
            window.__lomaVoiceAudio = a;
            a.onended = window.__lomaVoicePlayNext;
            a.onerror = window.__lomaVoicePlayNext;
            a.play().catch(function() { window.__lomaVoicePlayNext(); });
        };
    })();
"""


def stop_voice_reply() -> None:
    """Cancel pending/queued TTS and stop any browser audio already playing."""
    global _play_generation
    with _play_lock:
        _play_generation += 1
        generation = _play_generation
    _reset_stream_state(generation)
    js = _QUEUE_INIT_JS + f"""
        (function() {{
            window.__lomaVoiceGen = {generation};
            window.__lomaVoiceCompleteGen = -1;
            window.__lomaVoiceQueue = [];
            window.__lomaVoicePlaying = false;
            try {{
                if (window.__lomaVoiceAudio) {{
                    window.__lomaVoiceAudio.pause();
                    window.__lomaVoiceAudio.currentTime = 0;
                    window.__lomaVoiceAudio = null;
                }}
            }} catch (e) {{}}
        }})();
    """
    _client_js(js)


def _push_chunk_to_clients(generation: int, url: str) -> None:
    with _play_lock:
        if generation != _play_generation:
            return
    js = _QUEUE_INIT_JS + f"""
        (function() {{
            if ({generation} !== window.__lomaVoiceGen) {{
                window.__lomaVoiceGen = {generation};
                window.__lomaVoiceCompleteGen = -1;
                window.__lomaVoiceQueue = [];
            }}
            window.__lomaVoiceQueue.push({{url: '{url}'}});
            if (!window.__lomaVoicePlaying) {{ window.__lomaVoicePlayNext(); }}
        }})();
    """
    _client_js(js)


def _push_generation_complete(generation: int) -> None:
    """Tell the client no more chunks will ever be dispatched for this
    generation — combined with the client's own queue-empty/not-playing
    state, this is what actually means "done speaking"."""
    with _play_lock:
        if generation != _play_generation:
            return
    js = _QUEUE_INIT_JS + f"""
        (function() {{
            if ({generation} === window.__lomaVoiceGen) {{
                window.__lomaVoiceCompleteGen = {generation};
            }}
        }})();
    """
    _client_js(js)


def _play_on_clients(generation: int) -> None:
    ts = int(time.time())
    _push_chunk_to_clients(generation, f"/loma-voice/latest.mp3?t={ts}")


def _strip_for_speech(text: str) -> str:
    """Plain speakable text: drop headings/fences, strip markup, cap length."""
    lines = []
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("```"):
            continue
        s = re.sub(r"\[IMAGE_PROMPT:.*?\]", "", s, flags=re.IGNORECASE)
        s = re.sub(r"\*\*|__", "", s)
        lines.append(s)
    body = " ".join(lines).strip()
    return body[:8000] if body else "LOMA voice reply."


def _clean_chunk_for_speech(text: str) -> str:
    """Lighter-weight cleanup for a single streamed sentence chunk."""
    s = re.sub(r"\[IMAGE_PROMPT:.*?\]", "", text or "", flags=re.IGNORECASE)
    s = re.sub(r"```[\s\S]*?```", "", s)
    s = re.sub(r"[`*_#]", "", s)
    return s.strip()


def _extract_ready_sentences(buffer: str) -> tuple[list[str], str]:
    """Split a growing text buffer into complete sentences, returning
    (sentences_ready_to_speak, remaining_tail_still_uncommitted).

    A run of terminator punctuation (".", "!", "?", or their fullwidth CJK
    equivalents) followed by whitespace/end/closing-quote ends a sentence, as
    long as it's at least _MIN_CHUNK_CHARS long (avoids firing on "Mr." etc.
    mid-buffer — short fragments just keep accumulating). A hard cap forces a
    flush at the nearest whitespace if no terminator shows up for a while, so
    one long run-on line can't stall audio indefinitely.
    """
    sentences: list[str] = []
    start = 0
    i = 0
    n = len(buffer)
    while i < n:
        ch = buffer[i]
        forced = ch.isspace() and (i - start) >= _MAX_CHUNK_CHARS
        is_terminator = ch in _TERMINATORS
        if is_terminator or forced:
            j = i + 1 if is_terminator else i
            while j < n and buffer[j] in _TERMINATORS:
                j += 1
            boundary_ok = forced or j >= n or buffer[j].isspace() or buffer[j] in _CLOSERS
            if boundary_ok:
                candidate = buffer[start:j]
                if forced or len(candidate.strip()) >= _MIN_CHUNK_CHARS:
                    sentences.append(candidate)
                    start = j
                    i = j
                    continue
        i += 1
    return sentences, buffer[start:]


# Piper/SAPI/say/espeak zh voices read the Latin product name "LOMA" letter-by-letter or
# garbled — never rendered to the UI, only substituted in the text handed to the TTS
# engine so it comes out as a natural-sounding Chinese approximation ("LOH-mah").
_LOMA_TTS_ZH_PHONETIC = {"zh_tw": "羅瑪", "zh_cn": "罗玛"}
_LOMA_WORD_RE = re.compile(r"\bLOMA\b", re.IGNORECASE)


def _localize_loma_for_tts(text: str, locale: str | None) -> str:
    phonetic = _LOMA_TTS_ZH_PHONETIC.get((locale or "").strip())
    if not phonetic:
        return text
    return _LOMA_WORD_RE.sub(phonetic, text)


def _synthesize_mp3_to(spoken: str, voice_id: str, rate: str, path: str, locale: str | None = None) -> tuple[bool, str]:
    """Offline synthesis only — see services/tts_engines.py. Core Edition is documented
    as offline-only; this used to call Microsoft's edge-tts cloud API, which was this
    feature's one real internet dependency."""
    from services.tts_engines import synthesize_offline

    spoken = _localize_loma_for_tts(spoken, locale)
    tmp = path + ".part"
    try:
        ok, err = synthesize_offline(spoken, voice_id, rate, tmp)
        if ok and os.path.isfile(tmp) and os.path.getsize(tmp) > 200:
            os.replace(tmp, path)
            return True, ""
        if not ok:
            logging.getLogger(__name__).warning("TTS synthesis failed: %s", err)
    except Exception as exc:
        logging.getLogger(__name__).exception("TTS synthesis raised")
        err = str(exc)
    try:
        if os.path.isfile(tmp):
            os.remove(tmp)
    except OSError:
        pass
    return False, err or "unknown error"


def _synthesize_mp3(text: str, voice_id: str, rate: str, locale: str | None = None) -> tuple[bool, str]:
    spoken = _strip_for_speech(text)
    if not spoken or len(spoken) < 2:
        return False, ""
    spoken = spoken[:2000]
    os.makedirs(VOICE_DIR, exist_ok=True)
    return _synthesize_mp3_to(spoken, voice_id, rate, VOICE_FILE, locale)


def _speak_worker(content: str, settings: dict, generation: int) -> None:
    with _play_lock:
        if generation != _play_generation:
            return
    from pipeline.i18n import get_locale

    voice = resolve_voice_id(settings, content)
    rate = (settings.get("tts_rate") or "+0%").strip()
    if rate not in TTS_RATE_OPTIONS:
        rate = "+0%"
    ok, err = _synthesize_mp3(content, voice, rate, get_locale(settings))
    if ok:
        _play_on_clients(generation)
    elif err:
        _notify_synthesis_failed(generation, err)
    # Whether synthesis succeeded or not, this is the only "chunk" the
    # whole-message fallback ever produces — no more audio is coming.
    _push_generation_complete(generation)


def maybe_play_assistant_reply() -> None:
    """Whole-message fallback: speak the last assistant message in one pass.
    Used when the turn's content was never streamed token-by-token (e.g. a
    translation or image-ready message set via set_assistant_content)."""
    from services.session import state

    settings = state.current_settings or {}
    if not voice_reply_enabled(settings):
        return
    msgs = state.messages or []
    if not msgs or msgs[-1].get("role") != "assistant":
        return
    content = (msgs[-1].get("content") or "").strip()
    if not content or content.startswith("_"):
        return
    if "can't verify live information" in content.lower():
        return
    global _play_generation
    with _play_lock:
        _play_generation += 1
        generation = _play_generation
    threading.Thread(
        target=_speak_worker,
        args=(content, dict(settings), generation),
        daemon=True,
        name="loma-voice-reply",
    ).start()


# --- streaming entry points ------------------------------------------------


def _reset_stream_state(generation: int) -> None:
    global _stream_generation, _stream_buffer, _stream_had_output
    global _chunk_seq, _chunk_ready, _chunk_next_send, _stream_total_chunks
    with _stream_state_lock:
        _stream_generation = generation
        _stream_buffer = ""
        _stream_had_output = False
        _chunk_seq = 0
        _chunk_ready = {}
        _chunk_next_send = 0
        _stream_total_chunks = None


def start_stream_reply(settings: dict | None = None) -> int:
    """Call once when a new assistant turn begins (before any tokens arrive).
    Bumps the playback generation (cancelling any previous turn's queued/
    playing audio) and resets the sentence buffer. Returns the generation id."""
    global _play_generation
    with _play_lock:
        _play_generation += 1
        generation = _play_generation
    _reset_stream_state(generation)
    # Announce the new generation immediately rather than waiting for the
    # first chunk to arrive — otherwise a caller that starts waiting on this
    # generation right away (conversation mode) sees the *previous*
    # generation's __lomaVoiceGen, which looks indistinguishable from
    # "already finished" and returns instantly.
    js = _QUEUE_INIT_JS + f"""
        (function() {{
            window.__lomaVoiceGen = {generation};
            window.__lomaVoiceCompleteGen = -1;
            window.__lomaVoiceQueue = [];
            window.__lomaVoicePlaying = false;
            try {{
                if (window.__lomaVoiceAudio) {{
                    window.__lomaVoiceAudio.pause();
                    window.__lomaVoiceAudio.currentTime = 0;
                    window.__lomaVoiceAudio = null;
                }}
            }} catch (e) {{}}
        }})();
    """
    _client_js(js)
    return generation


def feed_stream_token(token: str, settings: dict | None) -> None:
    """Append one streamed token to the sentence buffer; dispatch synthesis
    for each complete sentence as soon as it forms."""
    global _stream_buffer, _stream_had_output
    if not token or not voice_reply_enabled(settings):
        return
    sentences: list[str] = []
    generation = 0
    with _stream_state_lock:
        if _stream_generation != _play_generation:
            return
        _stream_buffer += token
        sentences, _stream_buffer = _extract_ready_sentences(_stream_buffer)
        generation = _stream_generation
        if sentences:
            _stream_had_output = True
    for sentence in sentences:
        _dispatch_chunk(sentence, settings, generation)


def finish_stream_reply(settings: dict | None) -> None:
    """Flush whatever's left in the buffer (a trailing partial sentence) as
    a final chunk, then mark this generation's chunk count final — once
    every chunk up to that count has actually reached the client, that's
    the real "done speaking" signal (see _chunk_done)."""
    global _stream_buffer, _stream_total_chunks
    with _stream_state_lock:
        remaining = _stream_buffer.strip()
        _stream_buffer = ""
        generation = _stream_generation
    if remaining:
        _dispatch_chunk(remaining, settings, generation)

    all_delivered = False
    with _stream_state_lock:
        if generation == _stream_generation:
            _stream_total_chunks = _chunk_seq
            all_delivered = _chunk_next_send >= _stream_total_chunks
    if all_delivered:
        _push_generation_complete(generation)


def finish_or_fallback_reply() -> None:
    """Call once the assistant turn is fully finalized. If streaming fed any
    speakable text this turn, just flush the trailing partial sentence;
    otherwise fall back to speaking the whole final message at once."""
    from services.session import state

    settings = state.current_settings or {}
    if not voice_reply_enabled(settings):
        return
    with _stream_state_lock:
        used = _stream_generation == _play_generation and _stream_had_output
    if used:
        finish_stream_reply(settings)
    else:
        maybe_play_assistant_reply()


def _dispatch_chunk(text: str, settings: dict | None, generation: int) -> None:
    global _chunk_seq
    with _stream_state_lock:
        if generation != _stream_generation:
            return
        seq = _chunk_seq
        _chunk_seq += 1
    threading.Thread(
        target=_speak_chunk_worker,
        args=(text, dict(settings or {}), generation, seq),
        daemon=True,
        name="loma-voice-chunk",
    ).start()


def _speak_chunk_worker(text: str, settings: dict, generation: int, seq: int) -> None:
    with _play_lock:
        if generation != _play_generation:
            return
    spoken = _clean_chunk_for_speech(text)[:2000]
    if not spoken or len(spoken) < 2:
        _chunk_done(generation, seq, None)
        return
    from pipeline.i18n import get_locale

    voice = resolve_voice_id(settings, spoken)
    rate = (settings.get("tts_rate") or "+0%").strip()
    if rate not in TTS_RATE_OPTIONS:
        rate = "+0%"
    os.makedirs(VOICE_DIR, exist_ok=True)
    path = os.path.join(VOICE_DIR, f"chunk_{generation}_{seq}.mp3")
    ok, err = _synthesize_mp3_to(spoken, voice, rate, path, get_locale(settings))
    if not ok and err:
        _notify_synthesis_failed(generation, err)
    with _play_lock:
        if generation != _play_generation:
            return
    _chunk_done(generation, seq, path if ok else None)


def current_generation() -> int:
    """The playback generation of the most recently started turn — used by
    conversation mode to know which turn's audio to wait on."""
    with _play_lock:
        return _play_generation


_WAIT_PLAYBACK_DONE_JS_TMPL = """
    return await new Promise((resolve) => {
        const gen = %(gen)d;
        const deadline = Date.now() + %(timeout_ms)d;
        function check() {
            if (gen !== window.__lomaVoiceGen) { resolve(true); return; }
            const queueEmpty = !window.__lomaVoiceQueue || window.__lomaVoiceQueue.length === 0;
            // __lomaVoiceCompleteGen means the server has confirmed no more
            // chunks will ever be dispatched for this generation — without
            // it, "not playing and queue empty" is also true in the gap
            // between two chunks while the next one is still synthesizing,
            // which would report "done" mid-reply.
            const complete = window.__lomaVoiceCompleteGen === gen;
            if (complete && !window.__lomaVoicePlaying && queueEmpty) { resolve(true); return; }
            if (Date.now() > deadline) { resolve(false); return; }
            setTimeout(check, 150);
        }
        check();
    });
"""


async def wait_for_stream_playback(generation: int, timeout: float = 45.0) -> None:
    """Block until the browser finishes playing all queued audio for this
    generation, or timeout — lets conversation mode re-arm the mic without
    talking over LOMA's reply."""
    from nicegui import ui

    js = _WAIT_PLAYBACK_DONE_JS_TMPL % {"gen": generation, "timeout_ms": int(timeout * 1000)}
    try:
        await ui.run_javascript(js, timeout=timeout + 5.0)
    except Exception:
        pass


async def wait_for_turn_to_finish(generation: int, max_wait: float = 90.0) -> None:
    """Used by conversation mode: block until the assistant turn identified
    by `generation` has both finished generating and, if voice reply is on,
    finished being spoken aloud."""
    import asyncio as _asyncio
    import time as _time

    from services.session import state

    deadline = _time.time() + max_wait
    while _time.time() < deadline:
        msgs = state.messages or []
        if not msgs or msgs[-1].get("role") != "assistant" or not msgs[-1].get("processing"):
            break
        await _asyncio.sleep(0.15)

    settings = state.current_settings or {}
    if voice_reply_enabled(settings):
        remaining = max(1.0, deadline - _time.time())
        await wait_for_stream_playback(generation, timeout=remaining)


def _chunk_done(generation: int, seq: int, path: str | None) -> None:
    ready: list[str] = []
    all_delivered = False
    global _chunk_next_send
    with _stream_state_lock:
        if generation != _stream_generation:
            return
        _chunk_ready[seq] = path
        while _chunk_next_send in _chunk_ready:
            p = _chunk_ready.pop(_chunk_next_send)
            if p:
                ready.append(p)
            _chunk_next_send += 1
        if _stream_total_chunks is not None and _chunk_next_send >= _stream_total_chunks:
            all_delivered = True
    for p in ready:
        _push_chunk_to_clients(generation, f"/loma-voice/chunk/{os.path.basename(p)}")
    if all_delivered:
        _push_generation_complete(generation)
