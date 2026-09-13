# -*- coding: utf-8 -*-
"""Local microphone input — live SenseVoice dictation via raw PCM captured in-browser."""
from __future__ import annotations

from nicegui import background_tasks, helpers, ui

from pipeline.i18n import t
from ui.components.loma_notify import notify
from services.session import handlers

_STOP_JS = """
    try {
        if (window.lomaVoiceRec && window.lomaVoiceRec.active && window.lomaVoiceRec.manualStopFn) {
            window.lomaVoiceRec.manualStopFn();
        }
    } catch (e) {}
"""

_STOP_DICTATION_JS = """
    try {
        if (window.lomaDictation && window.lomaDictation.active && window.lomaDictation.manualStopFn) {
            window.lomaDictation.manualStopFn();
        }
    } catch (e) {}
"""

# Streaming dictation protocol (shared by both mics), SenseVoice-only:
# the client captures raw mic audio via Web Audio (not MediaRecorder), so no
# WebM/Opus container is ever produced — every tick sends ONLY the new audio
# since the last tick as raw 16-bit PCM. The server (services/voice_input.py's
# _LiveSession) holds the running uncommitted tail in memory and re-transcribes
# just that tail each tick, so per-tick cost stays flat no matter how long the
# recording runs. This mirrors the SOTA reference implementation's
# LiveTranscriber (native sounddevice capture, commit-on-pause), adapted to a
# browser mic: earlier this pipeline re-sent the CUMULATIVE WebM blob every
# tick and had the server decode-then-slice it, so decode cost (and upload
# size) grew every tick even though only the tail was ever transcribed —
# ticks fell further behind real time the longer a session ran. Raw PCM
# removes that decode step entirely.
# A session starts with /loma-voice/live-start (returns a session_id), each
# tick posts to /loma-voice/live-tick, and stopping posts once to
# /loma-voice/live-stop for a final accurate pass over whatever's left
# uncommitted. `text` in every response is newly committed speech to APPEND;
# `preview` is the still-changing uncommitted tail.

_PCM_CAPTURE_JS_HELPERS = """
        function floatTo16BitPCM(float32) {
            const out = new Int16Array(float32.length);
            for (let i = 0; i < float32.length; i++) {
                const s = Math.max(-1, Math.min(1, float32[i]));
                out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
            }
            return out;
        }

        function downsampleTo16k(buf, inRate) {
            if (inRate === 16000) return new Float32Array(buf);
            const ratio = inRate / 16000;
            const outLen = Math.max(0, Math.floor(buf.length / ratio));
            const out = new Float32Array(outLen);
            for (let i = 0; i < outLen; i++) out[i] = buf[Math.floor(i * ratio)] || 0;
            return out;
        }

        async function liveStart(lang) {
            const fd = new FormData();
            fd.append('language', lang || '');
            const resp = await fetch('/loma-voice/live-start', { method: 'POST', body: fd });
            const data = await resp.json();
            return data.session_id || '';
        }

        async function liveTick(sessionId, pcm16) {
            const fd = new FormData();
            fd.append('session_id', sessionId);
            fd.append('file', new Blob([pcm16.buffer], { type: 'application/octet-stream' }), 'tick.pcm');
            const resp = await fetch('/loma-voice/live-tick', { method: 'POST', body: fd });
            return await resp.json();
        }

        async function liveStop(sessionId) {
            const fd = new FormData();
            fd.append('session_id', sessionId);
            const resp = await fetch('/loma-voice/live-stop', { method: 'POST', body: fd });
            return await resp.json();
        }
"""

_DICTATION_CHUNKED_JS = (
    """
    return (async () => {
        const ta = document.querySelector('.loma-doc-editor-field textarea');
        if (!ta) return { ok: false, text: '', full: '', error: 'no-editor' };

        const baseline = ta.value || '';
        const sep = baseline && !baseline.endsWith(' ') && !baseline.endsWith('\\n') ? ' ' : '';
        const prefix = baseline + (baseline ? sep : '');
        let confirmed = '';
        let preview = '';
        let lang = '';
"""
    + _PCM_CAPTURE_JS_HELPERS
    + """
        function currentText() {
            return (confirmed + (confirmed && preview ? ' ' : '') + preview).trim();
        }
        function refreshEditor() {
            ta.value = prefix + currentText();
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.scrollTop = ta.scrollHeight;
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const sessionId = await liveStart(lang);

            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const source = audioCtx.createMediaStreamSource(stream);
            const processor = audioCtx.createScriptProcessor(4096, 1, 1);
            const silent = audioCtx.createGain();
            silent.gain.value = 0;
            source.connect(processor);
            processor.connect(silent);
            silent.connect(audioCtx.destination);

            const analyser = audioCtx.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);
            const levelBuf = new Uint8Array(analyser.fftSize);

            const dict = { active: true, manualStop: false, heardSpeech: false, silenceMs: 0 };
            window.lomaDictation = dict;

            let pending = [];
            let inFlight = false;
            let dirty = false;

            async function flushTick() {
                if (inFlight) { dirty = true; return; }
                inFlight = true;
                try {
                    do {
                        dirty = false;
                        if (!dict.active || pending.length === 0) break;
                        const chunks = pending;
                        pending = [];
                        const total = chunks.reduce((n, c) => n + c.length, 0);
                        const merged = new Float32Array(total);
                        let off = 0;
                        for (const c of chunks) { merged.set(c, off); off += c.length; }
                        const data = await liveTick(sessionId, floatTo16BitPCM(merged));
                        if (data && data.ok) {
                            if (data.language) lang = data.language;
                            if (data.text) confirmed = (confirmed ? confirmed + ' ' : '') + data.text;
                            preview = data.preview || '';
                            refreshEditor();
                        }
                    } while (dirty && dict.active);
                } catch (e) {
                    // ignore — the final pass on stop will recover the text
                } finally {
                    inFlight = false;
                }
            }

            processor.onaudioprocess = (e) => {
                if (!dict.active) return;
                pending.push(downsampleTo16k(e.inputBuffer.getChannelData(0), audioCtx.sampleRate));
            };
            const tickTimer = setInterval(flushTick, 1000);

            const SILENCE_MS = 5000;
            const THRESH = 0.014;
            const started = Date.now();

            let resolved = false;
            return await new Promise((resolve) => {
                async function stopEverything() {
                    if (resolved) return;
                    resolved = true;
                    dict.active = false;
                    clearInterval(tickTimer);
                    try { stream.getTracks().forEach((tr) => tr.stop()); } catch (e) {}
                    try { processor.disconnect(); source.disconnect(); silent.disconnect(); analyser.disconnect(); } catch (e) {}
                    try { await audioCtx.close(); } catch (e) {}

                    const deadline = Date.now() + 120000;
                    while (inFlight && Date.now() < deadline) {
                        await new Promise((r) => setTimeout(r, 80));
                    }
                    await flushTick();
                    while (inFlight && Date.now() < deadline) {
                        await new Promise((r) => setTimeout(r, 80));
                    }

                    try {
                        const data = await liveStop(sessionId);
                        if (data && data.ok && data.text) {
                            confirmed = (confirmed ? confirmed + ' ' : '') + data.text;
                            preview = '';
                            refreshEditor();
                        }
                    } catch (e) {}

                    const out = currentText();
                    resolve({
                        ok: out.length > 0, text: out, full: ta.value,
                        error: out ? '' : 'too-short',
                    });
                }

                dict.manualStopFn = () => { dict.manualStop = true; stopEverything(); };

                const monitor = () => {
                    if (!dict.active) return;
                    analyser.getByteTimeDomainData(levelBuf);
                    let sum = 0;
                    for (let i = 0; i < levelBuf.length; i++) {
                        const v = (levelBuf[i] - 128) / 128;
                        sum += v * v;
                    }
                    const rms = Math.sqrt(sum / levelBuf.length);
                    if (rms >= THRESH) {
                        dict.heardSpeech = true;
                        dict.silenceMs = 0;
                    } else if (dict.heardSpeech && (Date.now() - started) > 700) {
                        dict.silenceMs += 120;
                    }
                    if (dict.heardSpeech && dict.silenceMs >= SILENCE_MS && !dict.manualStop) {
                        stopEverything();
                        return;
                    }
                    setTimeout(monitor, 120);
                };
                setTimeout(monitor, 120);
            });
        } catch (e) {
            return { ok: false, text: '', full: baseline, error: String(e) };
        }
    })();
"""
)

_RECORD_JS = (
    """
    return (async () => {
        const input = document.querySelector(
            '.loma-chat-voice-target textarea, .loma-chat-voice-target input'
        );
        let confirmed = '';
        let preview = '';
        let lang = '';
"""
    + _PCM_CAPTURE_JS_HELPERS
    + """
        function currentText() {
            return (confirmed + (confirmed && preview ? ' ' : '') + preview).trim();
        }
        function refreshInput() {
            if (!input) return;
            input.value = currentText();
            input.dispatchEvent(new Event('input', { bubbles: true }));
        }

        try {
            const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            const sessionId = await liveStart(lang);

            const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            const source = audioCtx.createMediaStreamSource(stream);
            const processor = audioCtx.createScriptProcessor(4096, 1, 1);
            const silent = audioCtx.createGain();
            silent.gain.value = 0;
            source.connect(processor);
            processor.connect(silent);
            silent.connect(audioCtx.destination);

            const analyser = audioCtx.createAnalyser();
            analyser.fftSize = 512;
            source.connect(analyser);
            const levelBuf = new Uint8Array(analyser.fftSize);

            const rec = { active: true, manualStop: false, heardSpeech: false, silenceMs: 0 };
            window.lomaVoiceRec = rec;

            let pending = [];
            let inFlight = false;
            let dirty = false;

            async function flushTick() {
                if (inFlight) { dirty = true; return; }
                inFlight = true;
                try {
                    do {
                        dirty = false;
                        if (!rec.active || pending.length === 0) break;
                        const chunks = pending;
                        pending = [];
                        const total = chunks.reduce((n, c) => n + c.length, 0);
                        const merged = new Float32Array(total);
                        let off = 0;
                        for (const c of chunks) { merged.set(c, off); off += c.length; }
                        const data = await liveTick(sessionId, floatTo16BitPCM(merged));
                        if (data && data.ok) {
                            if (data.language) lang = data.language;
                            if (data.text) confirmed = (confirmed ? confirmed + ' ' : '') + data.text;
                            preview = data.preview || '';
                            refreshInput();
                        }
                    } while (dirty && rec.active);
                } catch (e) {
                    // ignore — the final pass on stop will recover the text
                } finally {
                    inFlight = false;
                }
            }

            processor.onaudioprocess = (e) => {
                if (!rec.active) return;
                pending.push(downsampleTo16k(e.inputBuffer.getChannelData(0), audioCtx.sampleRate));
            };
            const tickTimer = setInterval(flushTick, 1000);

            const SILENCE_MS = __SILENCE_MS__;
            const NO_SPEECH_TIMEOUT_MS = __NO_SPEECH_TIMEOUT_MS__;
            const THRESH = 0.014;
            const started = Date.now();

            let resolved = false;
            return await new Promise((resolve) => {
                async function stopEverything() {
                    if (resolved) return;
                    resolved = true;
                    rec.active = false;
                    clearInterval(tickTimer);
                    try { stream.getTracks().forEach((tr) => tr.stop()); } catch (e) {}
                    try { processor.disconnect(); source.disconnect(); silent.disconnect(); analyser.disconnect(); } catch (e) {}
                    try { await audioCtx.close(); } catch (e) {}

                    const deadline = Date.now() + 120000;
                    while (inFlight && Date.now() < deadline) {
                        await new Promise((r) => setTimeout(r, 80));
                    }
                    await flushTick();
                    while (inFlight && Date.now() < deadline) {
                        await new Promise((r) => setTimeout(r, 80));
                    }

                    try {
                        const data = await liveStop(sessionId);
                        if (data && data.ok && data.text) {
                            confirmed = (confirmed ? confirmed + ' ' : '') + data.text;
                            preview = '';
                            refreshInput();
                        }
                    } catch (e) {}

                    const out = currentText();
                    resolve({ ok: out.length > 0, text: out, error: out ? '' : 'too-short' });
                }

                rec.manualStopFn = () => { rec.manualStop = true; stopEverything(); };

                const monitor = () => {
                    if (!rec.active) return;
                    analyser.getByteTimeDomainData(levelBuf);
                    let sum = 0;
                    for (let i = 0; i < levelBuf.length; i++) {
                        const v = (levelBuf[i] - 128) / 128;
                        sum += v * v;
                    }
                    const rms = Math.sqrt(sum / levelBuf.length);
                    if (rms >= THRESH) {
                        rec.heardSpeech = true;
                        rec.silenceMs = 0;
                    } else if (rec.heardSpeech && (Date.now() - started) > 700) {
                        rec.silenceMs += 120;
                    }
                    if (rec.heardSpeech && rec.silenceMs >= SILENCE_MS && !rec.manualStop) {
                        stopEverything();
                        return;
                    }
                    if (!rec.heardSpeech && (Date.now() - started) > NO_SPEECH_TIMEOUT_MS && !rec.manualStop) {
                        stopEverything();
                        return;
                    }
                    setTimeout(monitor, 120);
                };
                setTimeout(monitor, 120);
            });
        } catch (e) {
            return { ok: false, text: '', error: String(e) };
        }
    })();
"""
)


async def record_and_transcribe(*, silence_ms: int = 2500, no_speech_timeout_ms: int = 600000) -> dict:
    """Record from mic (auto-stops on pause) with live preview in the chat input.

    silence_ms: how long a pause after speech has started auto-stops the
    recording (2.5s default for both the one-shot mic button and
    conversation mode). no_speech_timeout_ms: how long to wait for speech to
    even begin before giving up (default effectively "no limit" — bounded
    only by the outer run_javascript timeout below — since one-shot
    dictation has no reason to cut the user off before they've said a
    word)."""
    js = _RECORD_JS.replace("__SILENCE_MS__", str(silence_ms)).replace(
        "__NO_SPEECH_TIMEOUT_MS__", str(no_speech_timeout_ms)
    )
    try:
        result = await ui.run_javascript(js, timeout=600.0)
    except Exception:
        result = {"ok": False, "text": "", "error": "timeout"}
    return result if isinstance(result, dict) else {"ok": False, "text": "", "error": "bad-response"}


async def dictate_with_live_preview() -> dict:
    """Dictate into document editor — live local SenseVoice with live textarea updates."""
    try:
        result = await ui.run_javascript(_DICTATION_CHUNKED_JS, timeout=600.0)
    except Exception:
        result = {"ok": False, "text": "", "full": "", "error": "timeout"}
    return result if isinstance(result, dict) else {"ok": False, "text": "", "full": "", "error": "bad-response"}


async def stop_recording() -> None:
    # Stopping is fire-and-forget; the browser may be busy finishing the final
    # transcription pass, so a late reply must not raise.
    try:
        await ui.run_javascript(_STOP_JS, timeout=5.0)
    except Exception:
        pass


async def stop_dictation() -> None:
    try:
        await ui.run_javascript(_STOP_DICTATION_JS, timeout=5.0)
    except Exception:
        pass


_CONVERSATION_SILENCE_MS = 2500
_CONVERSATION_NO_SPEECH_TIMEOUT_MS = 30000
_CONVERSATION_MAX_IDLE_CYCLES = 4  # ~4 * 30s = 2 min of total silence before auto-exit


async def _conversation_loop(chat_input, mic_btn, conversation) -> None:
    """Hands-free loop: listen, auto-submit, wait for LOMA to finish
    speaking, then re-arm the mic — until turned off (right-click again) or
    auto-exited after a couple of minutes of total silence."""
    from services.session import state
    from services.voice_reply import current_generation, wait_for_turn_to_finish

    idle_cycles = 0
    try:
        while conversation["active"]:
            mic_btn.props("icon=mic color=positive")
            result = await record_and_transcribe(
                silence_ms=_CONVERSATION_SILENCE_MS,
                no_speech_timeout_ms=_CONVERSATION_NO_SPEECH_TIMEOUT_MS,
            )
            if not conversation["active"]:
                break
            if not isinstance(result, dict) or not result.get("ok"):
                err = (result.get("error") or "").strip() if isinstance(result, dict) else ""
                if err and err != "too-short":
                    notify(t("voice.input_failed"), color="warning")
                    break
                idle_cycles += 1
                if idle_cycles >= _CONVERSATION_MAX_IDLE_CYCLES:
                    notify(t("voice.conversation_idle_exit"), color="info")
                    break
                continue  # silence only — keep listening for the next utterance

            idle_cycles = 0
            text = (result.get("text") or "").strip()
            if not text:
                continue
            chat_input.set_value(text)
            handlers.handle_chat_action(chat_input)

            mic_btn.props("icon=graphic_eq color=orange")
            generation = current_generation()
            await wait_for_turn_to_finish(generation)
    finally:
        conversation["active"] = False
        mic_btn.props("icon=mic color=default")
        prev = conversation.get("prev_voice_reply_enabled")
        if prev is not None:
            state.current_settings["voice_reply_enabled"] = prev


def mount_voice_mic_button(chat_input, theme_tokens: dict) -> None:
    recording = {"active": False}
    conversation = {"active": False}
    chat_input.classes("loma-chat-voice-target")

    mic_btn = ui.button(icon="mic").props("flat round dense id=loma-mic-btn").classes(
        theme_tokens["icon_btn"]
    ).tooltip(f"{t('voice.input_tooltip')} — {t('voice.conversation_tooltip')}")

    async def _run_record() -> None:
        recording["active"] = True
        mic_btn.props("icon=stop color=red")
        notify(t("voice.recording_hint"), color="info", timeout=8000)
        result = await record_and_transcribe()
        recording["active"] = False
        mic_btn.props("icon=mic color=default")

        if not isinstance(result, dict):
            return
        if not result.get("ok"):
            err = (result.get("error") or "").strip()
            if err == "too-short":
                notify(t("voice.too_short"), color="warning")
            elif "faster-whisper" in err.lower() or "transcription" in err.lower() or "whisper" in err.lower() or "sensevoice" in err.lower() or "funasr" in err.lower():
                from services.voice_input import sensevoice_ready

                if not sensevoice_ready():
                    from ui.components.voice_input_installer import open_voice_input_chooser

                    open_voice_input_chooser(on_ready=lambda: None)
                notify(err[:240], color="warning", multi_line=True)
            elif err:
                notify(t("voice.input_failed"), color="warning")
            return

        text = (result.get("text") or "").strip()
        if text:
            chat_input.set_value(text)
            handlers.handle_chat_action(chat_input)
        else:
            notify(t("voice.no_speech"), color="warning")

    async def _toggle_mic() -> None:
        if conversation["active"]:
            # A single left-click also exits conversation mode — one gesture
            # to stop, no need to remember it was right-click that started it.
            conversation["active"] = False
            notify(t("voice.conversation_stopped"), color="info")
            await stop_recording()
            return

        if recording["active"]:
            mic_btn.props("icon=hourglass_top color=orange")
            notify(t("voice.transcribing"), color="info")
            await stop_recording()
            return

        def _start() -> None:
            # NiceGUI's slot stack is keyed by id(asyncio.current_task()) (see
            # nicegui/slot.py), so a fresh task from background_tasks.create() —
            # or plain asyncio.create_task() — always starts with an empty stack;
            # neither propagates the click handler's context. ui.* calls inside
            # _run_record (e.g. the notify() right at its start) need the slot
            # re-entered explicitly via the captured client, same as NiceGUI's own
            # internal usage of this helper (see nicegui/client.py).
            client = ui.context.client
            background_tasks.create(helpers.await_with_context(_run_record(), client), name="mic-record")

        from ui.components.voice_input_installer import ensure_voice_before_record

        ensure_voice_before_record(on_ready=_start)

    async def _toggle_conversation_mode() -> None:
        if conversation["active"]:
            conversation["active"] = False
            notify(t("voice.conversation_stopped"), color="info")
            await stop_recording()
            return
        if recording["active"]:
            return  # a one-shot recording is already in progress

        def _start() -> None:
            from services.session import state
            from services.voice_reply import voice_reply_needs_pick

            conversation["active"] = True
            conversation["prev_voice_reply_enabled"] = state.current_settings.get("voice_reply_enabled")
            state.current_settings["voice_reply_enabled"] = True
            notify(t("voice.conversation_started"), color="info")
            if voice_reply_needs_pick(state.current_settings):
                from pipeline.gap_handler import offer_voice_reply_installer

                offer_voice_reply_installer()
            client = ui.context.client
            background_tasks.create(
                helpers.await_with_context(_conversation_loop(chat_input, mic_btn, conversation), client),
                name="mic-conversation",
            )

        from ui.components.voice_input_installer import ensure_voice_before_record

        ensure_voice_before_record(on_ready=_start)

    mic_btn.on_click(_toggle_mic)
    mic_btn.on("contextmenu.prevent", _toggle_conversation_mode)
