# -*- coding: utf-8 -*-
"""Background preload of chat/vision models — skip when Ollama already has them loaded."""
from __future__ import annotations

import logging
import threading
import time

import config

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_warm_lock = threading.Lock()
_started = False
_ready = False
_model_name = ""
_error = ""


def reset_routing_warmup() -> None:
    """Allow warmup to run again after model reassignment (not routine UI reload)."""
    global _started, _ready, _model_name, _error
    with _lock:
        _started = False
        _ready = False
        _model_name = ""
        _error = ""
    try:
        from services.session import state

        state.routing_warmup_status = "pending"
        state.routing_model_ready = False
        state.routing_warmup_error = ""
        state.routing_model_name = ""
        state.loma_ready = False
    except Exception:
        pass


def _sync_state_ready(name: str, *, status: str = "ready") -> None:
    global _ready, _model_name, _error
    _ready = True
    _model_name = name
    _error = ""
    try:
        from services.session import state

        state.routing_warmup_status = status
        state.routing_model_name = name
        state.routing_model_ready = True
        state.routing_warmup_error = ""
        state.loma_ready = True
    except Exception:
        pass


def ollama_model_loaded(model_name: str) -> bool:
    """True when Ollama ps reports this model already in VRAM."""
    want = str(model_name or "").strip().lower()
    if not want:
        return False
    want_base = want.split(":")[0]
    try:
        import ollama

        for entry in ollama.ps().get("models", []) or []:
            name = str(entry.get("name") or entry.get("model") or "").strip().lower()
            if not name:
                continue
            if name == want or name.split(":")[0] == want_base:
                return True
    except Exception:
        pass
    return False


def routing_model_name() -> str:
    try:
        from services.model_router import resolve_general_model
        from pipeline.base import profile_pack as profile_manager
        from pipeline.base.profile_pack import default_profile
        from services.session import state

        profile_id = (state.current_settings or {}).get("active_profile", "")
        if profile_manager.is_no_profile(profile_id):
            prof = default_profile("none")
        else:
            prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
        name = str(resolve_general_model(prof) or "").strip()
        if name:
            return name
    except Exception:
        pass
    installed = config.get_installed_models()
    if installed:
        return installed[0]
    return ""


def chat_model_name() -> str:
    """Resolve the model express-lane chat will use (matches workflow path)."""
    try:
        from pipeline.base import profile_pack as profile_manager
        from pipeline.base.profile_pack import default_profile
        from services.model_router import resolve_general_model
        from services.session import state

        profile_id = (state.current_settings or {}).get("active_profile", "")
        if profile_manager.is_no_profile(profile_id):
            prof = default_profile("none")
        else:
            prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
        return resolve_general_model(prof)
    except Exception:
        return routing_model_name()


def _warm_chat_model(model_name: str) -> None:
    """Load model into Ollama VRAM only when not already resident."""
    name = str(model_name or "").strip()
    if not name:
        return
    if ollama_model_loaded(name):
        logger.debug("Skip warm — already loaded: %s", name)
        return
    with _warm_lock:
        if ollama_model_loaded(name):
            return
        from services import llm_bridge as chat_client
        from services.inference.ollama_chat import build_chat_request

        # Mirror express-lane kwargs (build_chat_request) so num_ctx, keep_alive, and
        # thinking flags match the first real chat — mismatches force Ollama to reload.
        profile = chat_client._active_profile()
        kwargs, _ = build_chat_request(
            profile,
            model=name,
            messages=[
                {"role": "system", "content": "You are LOMA."},
                {"role": "user", "content": "ok"},
            ],
            stream=False,
            disable_thinking=True,
            extra_options={"num_predict": 32, "temperature": 0.1},
        )
        chat_client.chat(**kwargs)


def prewarm_chat_model(model_name: str) -> None:
    """Background preload (image attach). No-op when model already in VRAM."""
    name = str(model_name or "").strip()
    if not name or ollama_model_loaded(name):
        return

    def _run() -> None:
        try:
            _warm_chat_model(name)
        except Exception as exc:
            logger.debug("Vision prewarm skipped: %s", exc)

    threading.Thread(target=_run, daemon=True, name="loma-vision-prewarm").start()


def is_routing_model_ready() -> bool:
    return _ready


def routing_warmup_error() -> str:
    return _error


def mark_ready_if_models_loaded() -> bool:
    """If assigned chat model is already in Ollama VRAM, mark ready without a chat round-trip."""
    global _started
    name = chat_model_name()
    if not name:
        with _lock:
            _started = True
        _sync_state_ready("")
        return True
    if ollama_model_loaded(name):
        with _lock:
            _started = True
        _sync_state_ready(name)
        logger.info("Routing ready — model already loaded: %s", name)
        return True
    return False


def ensure_models_warm(*, background: bool = True) -> None:
    """
    Warm assigned models without unloading on UI reload.
    Skips entirely when Ollama already holds the chat model.
    """
    if mark_ready_if_models_loaded():
        try:
            from ui.components.startup_overlay import refresh_startup_overlay

            refresh_startup_overlay()
        except Exception:
            pass
        return
    start_routing_warmup(background=background)


def start_routing_warmup(*, background: bool = True) -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def _run() -> None:
        global _ready, _model_name, _error
        if mark_ready_if_models_loaded():
            try:
                from ui.components.startup_overlay import refresh_startup_overlay

                refresh_startup_overlay()
            except Exception:
                pass
            return
        try:
            from services.inference.readiness import inference_ready, start_inference_readiness

            if not inference_ready():
                start_inference_readiness(background=False)
            wait_deadline = time.monotonic() + 95.0
            while not inference_ready() and time.monotonic() < wait_deadline:
                time.sleep(0.2)
        except Exception:
            pass

        name = chat_model_name()
        _model_name = name
        if not name:
            _ready = True
            _error = ""
            logger.info("No chat model installed — skipping warmup")
            _sync_state_ready("")
            try:
                from ui.components.startup_overlay import refresh_startup_overlay

                refresh_startup_overlay()
            except Exception:
                pass
            return
        try:
            from services.session import state

            state.routing_warmup_status = "loading"
            state.routing_model_name = name
        except Exception:
            pass
        try:
            _warm_chat_model(name)
            try:
                from services.model_router import resolve_vision_model

                vision = resolve_vision_model()
                if vision and vision != name:
                    _warm_chat_model(vision)
            except Exception:
                pass
            _ready = True
            logger.info("Chat model warmed: %s", name)
        except Exception as exc:
            _error = str(exc)
            logger.warning("Routing warmup failed for %s: %s", name, exc)
            _ready = True
        finally:
            try:
                from services.session import state

                state.routing_warmup_status = "ready" if _ready else "failed"
                state.routing_model_ready = _ready
                state.routing_warmup_error = _error
                state.loma_ready = _ready
            except Exception:
                pass
            try:
                from ui.components.startup_overlay import refresh_startup_overlay

                refresh_startup_overlay()
            except Exception:
                pass

    if background:
        threading.Thread(target=_run, daemon=True, name="loma-routing-warmup").start()
    else:
        _run()
