# -*- coding: utf-8 -*-
"""Speech stack installer — transcription model + live dictation engine (wizard + first mic use)."""
from __future__ import annotations

from typing import Callable

from nicegui import ui

from pipeline.i18n import t as tr
from services.bootstrap.connectivity import check_connectivity
from services.media_transcription import (
    WHISPER_TRANSCRIPTION_SIZES,
    _whisper_size_cached,
    ensure_whisper_model,
    resolve_whisper_model,
    whisper_ready,
)
from services.voice_input import (
    _sensevoice_available,
    ensure_sensevoice_model,
    sensevoice_model_cached,
    sensevoice_ready,
    voice_engines_ready,
)


_VOICE_LANG_OPTIONS = {
    "en": "English",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "pt": "Portuguese",
    "it": "Italian",
    "ru": "Russian",
}

_LIVE_ENGINE_CHOICES = frozenset({"sensevoice", "none"})


def engines_for_choice(choice: str) -> tuple[bool, bool]:
    """Return (want_sensevoice, want_whisper_live). Live dictation is SenseVoice-only in
    this edition — Whisper remains available for file/attachment transcription separately,
    never as the live-dictation engine."""
    c = (choice or "").strip().lower()
    if c == "none":
        return False, False
    return True, False


def speech_stack_ready(live_choice: str, *, whisper_model: str | None = None) -> bool:
    """True when transcription (tiny + accurate) and selected live engine extras are ready.
    Whisper baseline is required even when live_choice is "none" — it's the general
    transcription model, not just the live-dictation engine."""
    model = resolve_whisper_model(whisper_model)
    if not whisper_ready(accurate_size=model):
        return False
    want_sv, _ = engines_for_choice(live_choice)
    if want_sv and not sensevoice_ready():
        return False
    return True


def needed_engines_ready(choice: str, *, whisper_model: str | None = None) -> bool:
    return speech_stack_ready(choice, whisper_model=whisper_model)


def _install_sensevoice_sync(on_status: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Install funasr + its runtime deps and ensure SenseVoice's model weights are
    available (local -> HF -> ModelScope, see services/voice_input.py). Synchronous — call
    from a background thread. This is the ONE place SenseVoice actually gets installed;
    every entry point (setup wizard, first mic/dictate use, Settings -> Model Library)
    calls this instead of each keeping its own near-identical copy, so a fix here (or a
    behavior change) applies everywhere at once instead of needing three separate ones.

    funasr IS bundled in this build now (packaging/loma_core.spec's collect_all('funasr'))
    — skip the pip install whenever funasr is already importable instead of unconditionally
    re-running pip's resolver. Without this check, a bundled funasr plus an unconditional
    reinstall here produces a SECOND, different-versioned funasr install (plus modelscope's
    entire unrelated dependency tree) sitting alongside the bundled one, with no guarantee
    which one actually gets imported — see packaging/loma_core.spec's collect_all('funasr')
    comment for the full story, including why this pip-install path still exists at all
    (a safety net for older installs upgrading in place without funasr yet on disk)."""
    if sensevoice_ready():
        return True, ""
    if on_status:
        on_status(tr("voice.installing_sensevoice"))
    if not _sensevoice_available():
        from services.pip_runner import pip_install

        ok, output = pip_install(["funasr", "huggingface_hub", "torchaudio"], on_line=on_status)
        if not ok:
            return False, output[-300:] or "pip install failed"
    ok, detail = ensure_sensevoice_model()
    if not ok:
        return False, detail or "sensevoice"
    return True, ""


def install_sensevoice(
    *,
    on_status: Callable[[str], None] | None = None,
    on_done: Callable[[bool, str], None] | None = None,
) -> None:
    """Standalone SenseVoice install/uninstall-aware entry point — same background-thread
    + connectivity-check + schedule_on_ui shape as install_speech_stack/
    install_speech_baseline below, just without the Whisper-baseline half those two also
    handle. Used by Settings -> Model Library, where SenseVoice is downloaded on its own
    rather than bundled with a Whisper pick."""
    import threading

    def _work() -> None:
        from services.session.workflow_control import schedule_on_ui

        def _status(msg: str) -> None:
            if on_status:
                schedule_on_ui(lambda m=msg: on_status(m))

        try:
            conn = check_connectivity()
            if not conn.online:
                schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, "offline"))
                return
            ok, detail = _install_sensevoice_sync(on_status=_status)
            schedule_on_ui(lambda: (on_done or (lambda *_: None))(ok, detail))
        except Exception as exc:
            err_msg = str(exc)
            schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, err_msg))

    threading.Thread(target=_work, daemon=True).start()


def install_speech_stack(
    live_choice: str,
    *,
    whisper_model: str | None = None,
    on_status: Callable[[str], None] | None = None,
    on_percent: Callable[[float], None] | None = None,
    on_done: Callable[[bool, str], None] | None = None,
) -> None:
    """Background install: faster-whisper + tiny + transcription model; optional SenseVoice."""
    import threading

    live = (live_choice or "sensevoice").strip().lower() or "sensevoice"
    if live not in _LIVE_ENGINE_CHOICES:
        live = "sensevoice"
    model = resolve_whisper_model(whisper_model)
    want_sv, _ = engines_for_choice(live)

    def _work() -> None:
        from services.session.workflow_control import schedule_on_ui

        def _status(msg: str) -> None:
            if on_status:
                schedule_on_ui(lambda m=msg: on_status(m))

        def _percent(value: float) -> None:
            if on_percent:
                schedule_on_ui(lambda v=value: on_percent(v))

        try:
            conn = check_connectivity()
            if not conn.online:
                schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, "offline"))
                return

            if not whisper_ready(accurate_size=model):
                _status(tr("voice.installing_whisper"))
                from services.pip_runner import pip_install

                ok, output = pip_install(["faster-whisper"], on_line=_status)
                if not ok:
                    raise RuntimeError(output[-300:] or "pip install failed")
                ok, detail = ensure_whisper_model(accurate_size=model, on_percent=_percent)
                if not ok:
                    raise RuntimeError(detail or "whisper")

            if want_sv:
                ok, detail = _install_sensevoice_sync(on_status=_status)
                if not ok:
                    raise RuntimeError(detail)

            schedule_on_ui(lambda: (on_done or (lambda *_: None))(True, ""))
        except Exception as exc:
            # Capture the message now — "except X as exc" unbinds exc as soon as this
            # block exits, but schedule_on_ui defers the lambda until later on the UI
            # loop, so referencing exc there raised NameError instead of showing the
            # real error.
            err_msg = str(exc)
            schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, err_msg))

    threading.Thread(target=_work, daemon=True).start()


def speech_baseline_ready(whisper_sizes: list[str] | None = None, *, want_sensevoice: bool = False) -> bool:
    """True when every requested Whisper size, and (only if requested) SenseVoice, are
    installed. SenseVoice is optional in this edition — pass want_sensevoice=True to also
    require it. At least one Whisper size must be given; an empty list is never ready."""
    sizes = [s for s in (whisper_sizes or []) if s in WHISPER_TRANSCRIPTION_SIZES]
    if not sizes:
        return False
    if want_sensevoice and not sensevoice_ready():
        return False
    return all(_whisper_size_cached(size) for size in sizes)


def install_speech_baseline(
    whisper_sizes: list[str] | None = None,
    *,
    want_sensevoice: bool = False,
    on_status: Callable[[str], None] | None = None,
    on_percent: Callable[[float], None] | None = None,
    on_done: Callable[[bool, str], None] | None = None,
) -> None:
    """Background install: whichever Whisper sizes (base/small/turbo/large) the user
    picked, plus SenseVoice only if want_sensevoice is True — SenseVoice is optional in
    this edition."""
    import threading

    sizes = [s for s in (whisper_sizes or []) if s in WHISPER_TRANSCRIPTION_SIZES]

    def _work() -> None:
        from services.session.workflow_control import schedule_on_ui

        def _status(msg: str) -> None:
            if on_status:
                schedule_on_ui(lambda m=msg: on_status(m))

        def _percent(value: float) -> None:
            if on_percent:
                schedule_on_ui(lambda v=value: on_percent(v))

        try:
            conn = check_connectivity()
            if not conn.online:
                schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, "offline"))
                return

            if any(not _whisper_size_cached(size) for size in sizes):
                _status(tr("voice.installing_whisper"))
                from services.pip_runner import pip_install

                ok, output = pip_install(["faster-whisper"], on_line=_status)
                if not ok:
                    raise RuntimeError(output[-300:] or "pip install failed")
                for size in sizes:
                    ok, detail = ensure_whisper_model(accurate_size=size, on_percent=_percent)
                    if not ok:
                        raise RuntimeError(detail or f"whisper:{size}")

            if want_sensevoice:
                ok, detail = _install_sensevoice_sync(on_status=_status)
                if not ok:
                    raise RuntimeError(detail)

            schedule_on_ui(lambda: (on_done or (lambda *_: None))(True, ""))
        except Exception as exc:
            # Capture the message now — "except X as exc" unbinds exc as soon as this
            # block exits, but schedule_on_ui defers the lambda until later on the UI
            # loop, so referencing exc there raised NameError instead of showing the
            # real error.
            err_msg = str(exc)
            schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, err_msg))

    threading.Thread(target=_work, daemon=True).start()


def install_voice_engines(
    choice: str,
    *,
    whisper_model: str | None = None,
    on_status: Callable[[str], None] | None = None,
    on_done: Callable[[bool, str], None] | None = None,
) -> None:
    install_speech_stack(
        choice,
        whisper_model=whisper_model,
        on_status=on_status,
        on_done=on_done,
    )


def _default_live_choice(langs: list[str] | None = None) -> str:
    try:
        from services.session import state

        configured = str((state.current_settings or {}).get("default_live_dictation_engine") or "").strip().lower()
        if configured in _LIVE_ENGINE_CHOICES:
            return configured
    except Exception:
        pass
    return "sensevoice"


def open_voice_input_chooser(
    *,
    default_langs: list[str] | None = None,
    default_choice: str | None = None,
    on_ready: Callable[[], None] | None = None,
    allow_skip: bool = True,
) -> None:
    """Dialog: offer to install SenseVoice (the sole live-dictation engine in this
    edition), then on_ready. default_langs/default_choice are accepted for call-site
    compatibility but no longer change which engine is offered."""
    recommended = "sensevoice"

    with ui.dialog() as dialog, ui.card().classes("w-[480px] p-5 gap-3"):
        ui.label(tr("voice.chooser_title")).classes("text-lg font-bold text-primary")
        ui.markdown(tr("voice.chooser_blurb")).classes("text-xs text-gray-500")

        choice = ui.label(tr("voice.choice_sensevoice")).classes("text-sm")

        # Fixed-height scroll box, not a bare label — pip/funasr status lines vary wildly
        # in length (a short "Downloading…" vs. a long path-based error), and letting the
        # label grow with its text made the dialog resize/jump every status update instead
        # of staying put like the very first screenshot showed.
        with ui.column().classes("w-full h-12 overflow-y-auto"):
            status = ui.label("").classes("text-xs text-gray-500")
        status.set_visibility(False)
        progress = ui.linear_progress(value=0, show_value=False).classes("w-full")
        progress.set_visibility(False)

        def _close() -> None:
            dialog.close()

        def _after_ok() -> None:
            _close()
            if on_ready:
                on_ready()

        def _download() -> None:
            sel = recommended
            whisper_model = resolve_whisper_model(None)
            if needed_engines_ready(sel, whisper_model=whisper_model):
                _after_ok()
                return
            conn = check_connectivity()
            if not conn.online:
                status.set_visibility(True)
                status.set_text(tr("voice.offline_retry"))
                ui.notify(tr("setup.offline_no_download"), type="warning")
                return
            progress.set_visibility(True)
            # The download step (funasr/ModelScope) reports no incremental progress back
            # to this dialog — an indeterminate/animated bar at least shows it's alive,
            # instead of sitting frozen at 0% for however many minutes the download takes.
            progress.props("indeterminate")
            status.set_visibility(True)
            status.set_text(tr("voice.installing"))
            continue_btn.disable()

            def _done(ok: bool, msg: str) -> None:
                progress.props(remove="indeterminate")
                progress.set_value(1.0)
                if ok:
                    ui.notify(tr("voice.install_done"), type="positive")
                    _after_ok()
                    return
                continue_btn.enable()
                if msg == "offline":
                    status.set_text(tr("voice.offline_retry"))
                    ui.notify(tr("setup.offline_no_download"), type="warning")
                else:
                    status.set_text(tr("voice.install_failed", error=msg[:200]))
                    ui.notify(tr("voice.install_failed", error=msg[:120]), type="negative")

            install_speech_stack(
                sel,
                whisper_model=whisper_model,
                on_status=status.set_text,
                on_done=_done,
            )

        with ui.row().classes("w-full justify-end gap-2 mt-2"):
            if allow_skip:
                cancel_btn = ui.button(tr("common.cancel"), on_click=_close).props("flat")
            continue_btn = ui.button(tr("voice.download_continue"), color="primary", on_click=_download)

    dialog.open()


def ensure_voice_before_record(
    *,
    on_ready: Callable[[], None],
    default_langs: list[str] | None = None,
) -> None:
    """Live dictation (mic click, document-editor dictation) is SenseVoice-only in this
    edition. If already loaded in memory, dictate immediately. If funasr is installed but
    the model isn't loaded yet, warm it up in the background with a loading/ready notice
    (first load can take several seconds — blocking on it here with no feedback is the
    silent-freeze this replaces). Otherwise prompt to install it — an already-installed
    Whisper baseline does not substitute, since Whisper is not offered as a live-dictation
    engine here."""
    try:
        from services.session import state

        configured = str((state.current_settings or {}).get("default_live_dictation_engine") or "").strip().lower()
    except Exception:
        configured = ""
    if configured == "none":
        ui.notify(tr("voice.live_dictation_disabled"), type="warning")
        return
    if sensevoice_model_cached():
        on_ready()
        return

    from services.voice_input import sensevoice_downloaded

    # SenseVoice is NOT shipped by default — gate on whether the model weights are actually
    # on disk (sensevoice_downloaded), NOT on whether the funasr package imports. funasr is
    # now bundled unconditionally, so the old `_sensevoice_available()` (an import check) is
    # always True and this always fell through to the warm-up path — which silently kicked
    # off a ~1GB model DOWNLOAD with only a small "loading" toast and no consent, no progress
    # bar, and no completion gating (dictating against a half-downloaded model is the likely
    # source of the garbage-transcription-until-reload symptom). When the model isn't on disk
    # we must show the install chooser so the user explicitly consents and the download runs
    # to completion (with progress) before dictation is allowed.
    if not sensevoice_downloaded():
        open_voice_input_chooser(
            default_langs=default_langs,
            default_choice="sensevoice",
            on_ready=on_ready,
            allow_skip=True,
        )
        return

    # On disk but not loaded into memory yet — warm-load only (no download).
    _warm_up_sensevoice_then(on_ready, default_langs=default_langs)


def _warm_up_sensevoice_then(
    on_ready: Callable[[], None],
    *,
    default_langs: list[str] | None = None,
) -> None:
    """The SenseVoice model is already on disk (caller guarantees sensevoice_downloaded())
    but not loaded into memory yet — load it in a background thread (never blocks the UI
    event loop) with a loading toast up front and a ready toast once it lands, then proceed
    to on_ready(). ensure_sensevoice_model() here only LOADS the on-disk model; it must not
    reach the download path (that's the chooser's job, above)."""
    import threading

    ui.notify(tr("voice.sensevoice_loading_first_time"), color="info", timeout=6000)

    def _work() -> None:
        from services.session.workflow_control import schedule_on_ui

        ok, detail = ensure_sensevoice_model()

        def _done() -> None:
            if ok:
                ui.notify(tr("voice.sensevoice_ready"), color="positive")
                on_ready()
            else:
                open_voice_input_chooser(
                    default_langs=default_langs,
                    default_choice="sensevoice",
                    on_ready=on_ready,
                    allow_skip=True,
                )
                if detail:
                    ui.notify(detail[:240], color="warning", multi_line=True)

        schedule_on_ui(_done)

    threading.Thread(target=_work, daemon=True).start()


def install_voice_reply(
    voice_id: str,
    *,
    on_status: Callable[[str], None] | None = None,
    on_percent: Callable[[float], None] | None = None,
    on_done: Callable[[bool, str], None] | None = None,
) -> None:
    """Background: pip-install piper-tts if needed, download `voice_id` (a
    services.tts_engines.PIPER_VOICE_CATALOG entry id), then make it the active reply
    voice. Voice reply is off by default even after this — this only makes the picked
    voice ready so turning the Settings toggle on (or activating conversation mode)
    doesn't itself need to download anything."""
    import threading

    def _work() -> None:
        from services.session.workflow_control import schedule_on_ui

        def _status(msg: str) -> None:
            if on_status:
                schedule_on_ui(lambda m=msg: on_status(m))

        def _percent(value: float) -> None:
            if on_percent:
                schedule_on_ui(lambda v=value: on_percent(v))

        try:
            conn = check_connectivity()
            if not conn.online:
                schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, "offline"))
                return

            from services.tts_engines import download_piper_voice, piper_package_ready

            if not piper_package_ready():
                _status(tr("voice.installing_piper_package"))
                from services.pip_runner import pip_install

                ok, output = pip_install(["piper-tts[zh]"], on_line=_status)
                if not ok:
                    raise RuntimeError(output[-300:] or "pip install failed")

            ok, detail = download_piper_voice(voice_id, on_progress=_status, on_percent=_percent)
            if not ok:
                raise RuntimeError(detail or f"voice:{voice_id}")

            from services.session import settings as session_settings
            from services.session import state

            state.current_settings["tts_voice_id"] = voice_id
            session_settings.save_settings(state.current_settings, quiet=True)
            schedule_on_ui(lambda: (on_done or (lambda *_: None))(True, ""))
        except Exception as exc:
            err_msg = str(exc)
            schedule_on_ui(lambda: (on_done or (lambda *_: None))(False, err_msg))

    threading.Thread(target=_work, daemon=True).start()
