# -*- coding: utf-8 -*-
import asyncio
import os
import re
import sys
import time

# A frozen (PyInstaller) app re-executes THIS script for every child process that
# multiprocessing spawns (torch/tokenizers/semaphore tracker on macOS's default "spawn"
# method). Without freeze_support() each child ran the whole app again — extra web servers
# on random ports, duplicate model loads, orphan processes left behind on exit.
import multiprocessing

multiprocessing.freeze_support()

# multiprocessing's resource tracker launches `<exe> -c "<code>"`; a frozen exe ignores
# -c and would otherwise boot a second copy of the app instead of running the snippet.
if getattr(sys, "frozen", False) and len(sys.argv) >= 3 and sys.argv[1] == "-c":
    exec(sys.argv[2], {"__name__": "__main__"})
    sys.exit(0)

# Frozen macOS builds have no usable CA bundle for the stdlib's ssl (python.org builds ship
# none and the packaged app can't see the system one), so every urllib HTTPS call —
# connectivity probe, update check, web search, news, grounded chat — failed certificate
# verification and the app believed it was offline. Point OpenSSL at certifi's bundle
# before anything creates an SSL context.
try:
    import certifi as _certifi

    os.environ.setdefault("SSL_CERT_FILE", _certifi.where())
    os.environ.setdefault("REQUESTS_CA_BUNDLE", _certifi.where())
except Exception:
    pass

# A .app launched from Finder gets only /usr/bin:/bin:/usr/sbin:/sbin — Homebrew and
# /usr/local tools (ollama CLI, espeak-ng, ...) that shutil.which() looks for are invisible.
if sys.platform == "darwin":
    _extra_paths = [p for p in ("/opt/homebrew/bin", "/usr/local/bin") if os.path.isdir(p)]
    os.environ["PATH"] = os.pathsep.join([os.environ.get("PATH", ""), *_extra_paths])

_startup_t0 = time.perf_counter()

# Packaged --windowed .exe / .app: no console is attached, so sys.stdout/stderr are None.
# Any print() crashes outright, and uvicorn's logging setup calls stream.isatty() while
# configuring its formatter — 'NoneType' object has no attribute 'isatty' — which kills the
# app before the UI ever starts. Give both a harmless writable stream so print() and
# uvicorn's isatty() check both succeed.
if sys.stdout is None or sys.stderr is None:
    _devnull = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = _devnull
    if sys.stderr is None:
        sys.stderr = _devnull

# Packaged --windowed .exe has no console attached, so every subprocess a dependency
# spawns (pydub/ffmpeg for TTS wav->mp3, piper.exe, espeak, etc.) makes Windows pop up
# its own new console window for that child process — flashing on screen for each call
# and closing when it exits. Voice reply synthesizes per-sentence, so this showed up as
# "many windows open and close" while LOMA spoke. None of those call sites (including
# vendored pydub) pass CREATE_NO_WINDOW, so default it here for every subprocess this
# process spawns instead of patching each call site individually.
if sys.platform == "win32":
    import subprocess as _subprocess

    _real_popen_init = _subprocess.Popen.__init__

    def _popen_init_no_window(self, *args, **kwargs):
        if "creationflags" not in kwargs:
            kwargs["creationflags"] = _subprocess.CREATE_NO_WINDOW
        _real_popen_init(self, *args, **kwargs)

    _subprocess.Popen.__init__ = _popen_init_no_window

# A --windowed .exe has no console, so print()/logging output otherwise vanishes into thin
# air (see the devnull redirect above) — a user who hits a silent hang or failure (a stuck
# "Semantic ..." build, a TTS failure, a slow first-run model download) has nowhere to look.
# Write everything logged through the standard `logging` module to a real file in AppData
# so there's always somewhere to check, regardless of how the app was launched.
import logging as _logging

try:
    from services.plugins.paths import loma_app_data_root as _loma_app_data_root

    _log_dir = os.path.join(_loma_app_data_root(), "logs")
    os.makedirs(_log_dir, exist_ok=True)
    _log_path = os.path.join(_log_dir, "loma.log")
    _log_handler = _logging.FileHandler(_log_path, encoding="utf-8")
    _log_handler.setFormatter(
        _logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
    )
    _root_logger = _logging.getLogger()
    _root_logger.addHandler(_log_handler)
    _root_logger.setLevel(_logging.INFO)
except Exception:
    pass

# Packaged .exe / .app: make relative data/ paths writable (not / or System32).
from services.platform_paths import ensure_data_folder_shortcut, ensure_runtime_cwd

ensure_runtime_cwd()
ensure_data_folder_shortcut()

# Packages the user installed on demand (voice input, image extras, ...) live in the per-user
# data folder, not the app folder — make them importable from the first import onward.
from services.pip_runner import register_user_packages

register_user_packages()


def _resolve_port() -> int:
    env_port = os.environ.get("LOMA_PORT")
    if env_port:
        return int(env_port)
    if getattr(sys, "frozen", False):
        # Packaged app: avoid clashing with whatever else might already be on 8080 on the
        # user's machine (NiceGUI's own packaging guidance). Dev runs keep the fixed default
        # so existing tooling (launch.json, etc.) stays stable.
        from nicegui.native import find_open_port

        return find_open_port()
    return 8080


_port = _resolve_port()

from nicegui import app, ui

from ui.components.loma_notify import install_notify_defaults

install_notify_defaults()

from ui.branding import ASSETS_DIR, LOMA_FAVICON_FILE, favicon_for_nicegui

os.makedirs(ASSETS_DIR, exist_ok=True)
app.add_static_files("/loma-brand", ASSETS_DIR)
_generated_images = os.path.join("data", "generated", "images")
# Not created eagerly: Core Edition has no image-output extension bundled, so an empty
# "images" folder would otherwise show up under the workspace output folder on every
# launch even though nothing ever writes there. Image-writing call sites (image
# composite/edit) already os.makedirs() this on demand right before use.
if os.path.isdir(_generated_images):
    app.add_static_files("/loma-generated-images", os.path.abspath(_generated_images))

from pipeline.debug_session import debug_log

_seen_client_ids: set[str] = set()
# Guards the auto-quit-on-disconnect logic below: nicegui fires on_disconnect for routine
# connection churn too (its own docstring: "also called when a client reconnects"), not
# just a tab actually closing — e.g. this app's own auto-reload-if-UI-didn't-build check
# (see the sessionStorage "lomaAutoReloaded" logic further down) is itself a disconnect+
# reconnect. Arming the quit timer on the *first* disconnect ever, before any real session
# was established, killed the whole app ~8s after launch with zero clients ever having
# used it (confirmed while testing the packaged .exe — no error, just silent exit).
_had_real_client = False


def _on_client_connect() -> None:
    """Track clients; do not wipe chat on reconnect (mic / websocket refresh must keep history)."""
    global _had_real_client
    try:
        from nicegui import context

        cid = str(context.client.id)
    except Exception:
        return
    _seen_client_ids.add(cid)
    _had_real_client = True


app.on_connect(_on_client_connect)


def _on_client_disconnect() -> None:
    """Packaged .exe only: quit the process once the last browser tab closes, instead of
    lingering in Task Manager forever — this app runs native=False (a normal browser tab,
    not a managed native window), so there's no OS-level "close the app" signal otherwise.
    Delayed check (not an immediate exit) because a page refresh is itself a disconnect
    followed by a reconnect, which must not kill the server mid-refresh. Only arms at all
    once a real client has connected at least once (see _had_real_client above) — otherwise
    startup-time connection churn before the user's first real page load can trigger it."""
    from services.platform_paths import is_frozen

    if not is_frozen() or not _had_real_client:
        return

    def _maybe_quit() -> None:
        from nicegui import Client

        still_connected = any(c.has_socket_connection for c in Client.instances.values())
        if not still_connected:
            os._exit(0)

    import threading

    threading.Timer(8.0, _maybe_quit).start()


app.on_disconnect(_on_client_disconnect)

debug_log("main.py:imports_nicegui", "nicegui imported", {"ms": round((time.perf_counter() - _startup_t0) * 1000, 1)}, "F")

if sys.platform == "win32":
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    except Exception as loop_err:
        print(f"Warning: Failed to set Windows Proactor Event Loop Policy: {loop_err}")

from services.session import settings as session_settings
from services.session import state

debug_log("main.py:pre_init", "imports complete", {"ms": round((time.perf_counter() - _startup_t0) * 1000, 1)}, "F")

for subdir in (
    "data/uploads",
    "data/chats",
    "data/generated",
    "data/temp/voice",
    "data/temp/voice_input",
    "data/formslator/styles",
    "data/formslator/glossary",
    "data/formslator/mapping",
    "data/formslator/uploads",
    "data/formslator/output",
):
    os.makedirs(subdir, exist_ok=True)

from services.formslator.paths import ensure_default_glossary, ensure_default_templates

ensure_default_templates()
ensure_default_glossary()

import config

state.current_settings = session_settings.load_settings()
from services.session.upload_cleanup import purge_stale_uploads

_removed = purge_stale_uploads(state.current_settings)
if _removed:
    from pipeline.i18n import t as tr

    state.orchestra_log.append(tr("console.upload_purge", count=_removed))
from services.providers.registry import resolve_inference_backend

resolve_inference_backend()

from services.startup_bootstrap import start_background_init
from services.inference.readiness import start_inference_readiness

start_background_init()
start_inference_readiness(background=True)


def _prefetch_model_catalog() -> None:
    try:
        config.get_installed_models()
    except Exception:
        pass


import threading

threading.Thread(
    target=_prefetch_model_catalog, daemon=True, name="loma-model-catalog"
).start()

from pipeline.startup_locale import apply_startup_messages

apply_startup_messages()

debug_log("main.py:pre_run", "ready for ui.run", {"total_ms": round((time.perf_counter() - _startup_t0) * 1000, 1)}, "F")


from fastapi import Form, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel


class _OpenPathRequest(BaseModel):
    path: str


@app.get("/loma-voice/latest.mp3")
def loma_voice_latest() -> FileResponse:
    path = os.path.join("data", "temp", "voice", "latest.mp3")
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No voice reply")
    return FileResponse(path, media_type="audio/mpeg")


_LOMA_VOICE_CHUNK_RE = re.compile(r"^chunk_\d+_\d+\.mp3$")


@app.get("/loma-voice/chunk/{name}")
def loma_voice_chunk(name: str) -> FileResponse:
    if not _LOMA_VOICE_CHUNK_RE.match(name or ""):
        raise HTTPException(status_code=404, detail="No voice reply")
    path = os.path.join("data", "temp", "voice", name)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="No voice reply")
    return FileResponse(path, media_type="audio/mpeg")


@app.post("/loma-voice/transcribe")
async def loma_voice_transcribe(
    file: UploadFile = File(...),
    language: str = Form(""),
    mode: str = Form("final"),
    offset: float = Form(0.0),
) -> dict:
    import asyncio

    from services.session import state
    from services.voice_input import _maybe_traditional, transcribe_voice_stream

    raw = await file.read()
    settings = state.current_settings or {}
    from services.media_transcription import resolve_whisper_model

    model = resolve_whisper_model(settings.get("default_whisper_model"))
    lang = (language or "").strip()
    if not lang:
        configured = str(settings.get("default_voice_language") or "auto").strip()
        if configured != "auto":
            lang = configured
    # Whisper is CPU-bound — run off the event loop so the UI websocket stays live.
    result = await asyncio.to_thread(
        transcribe_voice_stream,
        raw,
        filename=file.filename or "voice.webm",
        model_size=str(model),
        language=lang,
        mode=(mode or "final").strip() or "final",
        offset=offset,
    )
    # Whisper/SenseVoice emit Simplified script for "zh" regardless of UI locale;
    # convert to Traditional per the "traditional_chinese" setting (on by default).
    if result.get("language") == "zh":
        result["text"] = _maybe_traditional(result.get("text") or "")
        result["preview"] = _maybe_traditional(result.get("preview") or "")
    return result


@app.post("/loma-voice/live-start")
async def loma_voice_live_start(language: str = Form("")) -> dict:
    from services.voice_input import live_start

    return {"session_id": live_start(language)}


@app.post("/loma-voice/live-tick")
async def loma_voice_live_tick(
    session_id: str = Form(...),
    file: UploadFile = File(...),
) -> dict:
    from services.voice_input import live_tick

    pcm = await file.read()
    # SenseVoice inference is CPU-bound — run off the event loop so the UI websocket stays live.
    return await asyncio.to_thread(live_tick, session_id, pcm)


@app.post("/loma-voice/live-stop")
async def loma_voice_live_stop(session_id: str = Form(...)) -> dict:
    from services.voice_input import live_stop

    return await asyncio.to_thread(live_stop, session_id)


@app.post("/loma/open-path")
async def loma_open_path(body: _OpenPathRequest) -> dict:
    path = (body.path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="Missing path")
    abspath = os.path.abspath(path)
    if not os.path.exists(abspath):
        raise HTTPException(status_code=404, detail="Path not found")
    try:
        if os.path.isfile(abspath):
            if sys.platform == "win32":
                os.startfile(abspath)
            elif sys.platform == "darwin":
                import subprocess

                subprocess.run(["open", abspath], check=False)
            else:
                import subprocess

                subprocess.run(["xdg-open", abspath], check=False)
        else:
            from services.platform_paths import open_path_in_os

            open_path_in_os(abspath)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}


if os.environ.get("LOMA_SELFTEST") == "1":
    # CI-only (build.yml's smoke test sets this): lets the workflow prove on a real macOS
    # runner that the frozen app can actually make verified HTTPS requests.
    @app.get("/loma-selftest")
    def loma_selftest() -> dict:
        from services.bootstrap.connectivity import check_connectivity

        conn = check_connectivity()
        # The Stable-Diffusion stack imports these lazily; a frozen build that missed them fails
        # every image generation, so surface it here (the build's smoke test checks this).
        image_imports = "ok"
        try:
            from diffusers import DPMSolverMultistepScheduler, LCMScheduler, StableDiffusionPipeline  # noqa: F401
            from transformers import CLIPImageProcessor, CLIPTextModel, CLIPTokenizer  # noqa: F401
        except Exception:
            import traceback

            image_imports = traceback.format_exc()[-700:]
            def _direct_clip():
                try:
                    from transformers.models.clip.image_processing_clip import CLIPImageProcessor as _c  # noqa: F401
                    return "ok"
                except Exception:
                    return traceback.format_exc()[-3500:].replace(chr(10), " | ")

            try:
                import transformers as _tf
                from transformers.utils import import_utils as _iu

                _d = os.path.join(os.path.dirname(_tf.__file__), "models", "clip")
                image_imports += (
                    f" | DIAG clip_dir={sorted(os.listdir(_d)) if os.path.isdir(_d) else 'MISSING'}"
                    f" torchvision={_iu.is_torchvision_available()} vision={_iu.is_vision_available()}"
                    f" torch={_iu.is_torch_available()}"
                    f" models_dir={sorted(os.listdir(os.path.dirname(_d)))[:20]}"
                    f" direct={_direct_clip()}"
                    f" imgutils_in_modules={'transformers.image_utils' in sys.modules}"
                )
            except Exception as _e:
                image_imports += f" | DIAG failed: {_e!r}"
        return {"online": conn.online, "reason": conn.reason, "image_imports": image_imports}


@ui.page("/")
def index() -> None:
    from ui.branding import favicon_for_nicegui
    from ui.components.language_splash import mount_language_splash
    from ui.components.startup_overlay import begin_startup_poll, mount_startup_overlay

    ui.add_head_html(
        f'<link rel="icon" type="image/png" href="{favicon_for_nicegui()}">'
        f'<link rel="shortcut icon" type="image/png" href="{favicon_for_nicegui()}">'
        # Self-heal a stalled first paint (e.g. a slow cold-boot websocket handshake): if
        # neither the language gate nor the startup overlay has mounted after 6s, reload
        # once. Plain JS only — must not depend on the same websocket that might be stuck.
        '<script>(function(){'
        "if(sessionStorage.getItem('lomaAutoReloaded'))return;"
        'setTimeout(function(){'
        "if(!document.querySelector('.loma-language-gate, .loma-startup-overlay')){"
        "sessionStorage.setItem('lomaAutoReloaded','1');location.reload();}"
        "},6000);})();</script>"
    )

    # Immediate visible shell (avoids empty page before websocket paints widgets).
    ui.query("body").classes("bg-[#0a0a0c] loma-app")

    def _boot() -> None:
        from nicegui import context

        from ui.components.language_splash import language_picked

        if not language_picked():
            return
        # Fresh page load must rebuild UI (storage survives reload; handlers would otherwise be dead).
        context.client.storage["loma_ui_built"] = False
        context.client.storage["setup_wizard_started"] = False
        mount_startup_overlay()
        begin_startup_poll()

    mount_language_splash(_boot)


if __name__ == "__main__" or (__name__ == "__mp_main__" and not getattr(sys, "frozen", False)):
    # "__mp_main__" only matters for dev's uvicorn --reload child; in a frozen build a
    # multiprocessing child must never start another server.
    import atexit

    from services.sandbox.interactive import stop_sandbox_run

    atexit.register(stop_sandbox_run)
    _project_root = os.path.dirname(os.path.abspath(__file__))
    from services.platform_paths import venv_python

    _venv_python = venv_python()
    if os.path.isfile(_venv_python) and os.path.normcase(sys.executable) != os.path.normcase(
        _venv_python
    ):
        print(
            "Warning: LOMA is not running from the project venv.\n"
            f"  Current:  {sys.executable}\n"
            f"  Expected: {_venv_python}\n"
            "  Image generation needs the project venv with all dependencies installed."
        )
    # Open browser once (parent process only); reload child must not spawn another tab.
    _open_browser = not os.environ.get("LOMA_BROWSER_OPENED")
    if _open_browser:
        os.environ["LOMA_BROWSER_OPENED"] = "1"
    # _port was already resolved at module top (fixed default in dev, an auto-found free
    # port when frozen/packaged).
    # uvicorn --reload spawns a second process that pre-binds the listening socket before
    # the worker (the one that actually imports everything and can serve) is ready. NiceGUI's
    # browser-launch check only waits for the socket to be open, not for the app to answer —
    # so on a cold boot the tab opens against a socket that accepts TCP but isn't serving yet,
    # and the page never paints. End users never edit source files, so reload only benefits an
    # active dev loop — keep it opt-in and off by default for a packaged/end-user launch.
    _dev_reload = os.environ.get("LOMA_DEV_RELOAD", "").strip().lower() in ("1", "true", "yes")
    if __name__ == "__main__":
        print(f"LOMA: starting web UI on http://127.0.0.1:{_port}")
        if not _open_browser:
            print(f"LOMA: open http://127.0.0.1:{_port} in your browser (reload child — no auto-open).")
    ui.run(
        title="LOMA Extended Edition",
        favicon=favicon_for_nicegui(),
        native=False,
        show=_open_browser,
        dark=True,
        port=_port,
        reload=_dev_reload,
        # Watch source dirs only when LOMA_DEV_RELOAD=1 (sandbox lives in system temp — no
        # reload on script writes).
        uvicorn_reload_dirs="pipeline,ui,capabilities,services,extensions,config",
        uvicorn_reload_excludes=".*, .py[cod], .sw.*, ~*, data, debug-*.log",
    )
