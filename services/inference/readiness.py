# -*- coding: utf-8 -*-
"""Wait for configured Ollama/LM Studio on cold boot before treating backend as missing."""
from __future__ import annotations

import logging
import threading
import time

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_started = False
_ready = False
_status = "pending"
_MAX_WAIT_S = 90.0
_POLL_S = 2.0


def inference_ready() -> bool:
    return _ready


def inference_status() -> str:
    return _status


def reset_inference_readiness() -> None:
    global _started, _ready, _status
    with _lock:
        _started = False
        _ready = False
        _status = "pending"


def mark_inference_ready_if_available() -> bool:
    """Fast path after settings reload — Ollama stays up; do not block the UI for 90s."""
    global _started, _ready, _status
    provider_id = _configured_provider_id()
    if not provider_id:
        with _lock:
            _started = True
            _ready = True
            _status = "skipped"
        _notify_overlay()
        return True
    try:
        from services.providers.registry import detect_providers

        import config

        infos = detect_providers(force_refresh=True)
        match = next((p for p in infos if p.provider_id == provider_id), None)
        if match and match.available:
            try:
                config.invalidate_models_cache()
            except Exception:
                pass
            with _lock:
                _started = True
                _ready = True
                _status = "ready"
            _notify_overlay()
            logger.info("Inference ready (fast re-probe): %s", provider_id)
            return True
    except Exception as exc:
        logger.debug("Fast inference probe failed: %s", exc)
    return False


def _notify_overlay() -> None:
    try:
        from services.session.workflow_control import schedule_on_ui
        from ui.components.startup_overlay import refresh_startup_overlay

        schedule_on_ui(refresh_startup_overlay)
    except Exception:
        pass


def _configured_provider_id() -> str:
    try:
        from services.session import state

        setup = (state.current_settings or {}).get("setup") or {}
        pid = str(setup.get("provider") or "ollama").strip().lower()
        if pid in ("", "none"):
            return ""
        return pid
    except Exception:
        return "ollama"


def _wait_for_provider(provider_id: str) -> bool:
    from services.providers.registry import detect_providers

    import config

    deadline = time.monotonic() + _MAX_WAIT_S
    while time.monotonic() < deadline:
        infos = detect_providers(force_refresh=True)
        match = next((p for p in infos if p.provider_id == provider_id), None)
        if match and match.available:
            try:
                config.invalidate_models_cache()
            except Exception:
                pass
            return True
        time.sleep(_POLL_S)
    return False


def _run() -> None:
    global _ready, _status
    provider_id = _configured_provider_id()
    if not provider_id:
        _status = "skipped"
        _ready = True
        _notify_overlay()
        return

    if provider_id == "ollama":
        try:
            from services.providers.ollama_launch import try_start_ollama

            ok, launch_status = try_start_ollama()
            logger.info("Ollama launch probe: ok=%s status=%s", ok, launch_status)
        except Exception as exc:
            logger.warning("Ollama launch attempt failed: %s", exc)

    if _wait_for_provider(provider_id):
        _status = "ready"
        logger.info("Inference backend ready: %s", provider_id)
    else:
        _status = "timeout"
        logger.warning("Inference backend not ready after %.0fs: %s", _MAX_WAIT_S, provider_id)

    _ready = True
    _notify_overlay()


def start_inference_readiness(*, background: bool = True) -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    if background:
        threading.Thread(target=_run, daemon=True, name="loma-inference-readiness").start()
    else:
        _run()
