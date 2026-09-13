# -*- coding: utf-8 -*-
"""Provider-agnostic chat/generate API for LOMA inference."""
from __future__ import annotations

from typing import Any

from services.providers.registry import get_active_provider, resolve_inference_backend

# Re-export for startup / settings wiring.
ensure_inference_backend = resolve_inference_backend

_DEFAULT_KEEP_ALIVE = "30m"


def _backend():
    return get_active_provider()


def _with_keep_alive(kwargs: dict[str, Any]) -> dict[str, Any]:
    out = dict(kwargs)
    if out.get("keep_alive") is None:
        out["keep_alive"] = _DEFAULT_KEEP_ALIVE
    return out


def _active_profile() -> dict | None:
    try:
        from pipeline.base import profile_pack as profile_manager
        from pipeline.base.profile_pack import default_profile
        from services.session import state

        profile_id = (state.current_settings or {}).get("active_profile", "")
        if profile_manager.is_no_profile(profile_id):
            return default_profile("none")
        return profile_manager.load_profile(profile_id) or default_profile(profile_id)
    except Exception:
        return None


def _apply_think_setting(kwargs: dict[str, Any]) -> dict[str, Any]:
    """
    Honor inference_defaults.enable_thinking on every chat call.
    Callers that omit `think` leave Ollama's model default (often ON for Qwen 3.5).
    """
    if "think" in kwargs:
        return kwargs
    model = str(kwargs.get("model") or "").strip()
    if not model:
        return kwargs
    from services.inference.ollama_chat import _messages_have_images, resolve_think_kwarg

    profile = _active_profile()
    messages = kwargs.get("messages") or []
    if _messages_have_images(messages):
        think = False
    else:
        think = resolve_think_kwarg(profile, model)
    if think is None:
        return kwargs
    out = dict(kwargs)
    out["think"] = think
    return out


def chat(**kwargs: Any) -> Any:
    stream = bool(kwargs.get("stream"))
    model = str(kwargs.get("model") or "unknown")
    backend = _backend()
    call_kwargs = _with_keep_alive(_apply_think_setting(kwargs))
    if stream:
        return _StreamUsageRecorder(backend.chat(**call_kwargs), model=model, operation="chat")
    # Non-streaming calls block until the whole response arrives — Stop must be able to
    # unwind this immediately (e.g. plan-mode orchestration), not just wait it out.
    from services.session.workflow_control import run_cancellable

    result = run_cancellable(backend.chat, **call_kwargs)
    try:
        from services.session.token_usage import record_from_response

        record_from_response(result, model=model, operation="chat")
    except Exception:
        pass
    return result


def generate(**kwargs: Any) -> Any:
    stream = bool(kwargs.get("stream"))
    model = str(kwargs.get("model") or "unknown")
    backend = _backend()
    call_kwargs = _with_keep_alive(_apply_think_setting(kwargs))
    if stream:
        return _StreamUsageRecorder(backend.generate(**call_kwargs), model=model, operation="generate")
    from services.session.workflow_control import run_cancellable

    result = run_cancellable(backend.generate, **call_kwargs)
    try:
        from services.session.token_usage import record_from_response

        record_from_response(result, model=model, operation="generate")
    except Exception:
        pass
    return result


class _StreamUsageRecorder:
    """Wrap streaming iterators and record token usage from the final chunk."""

    def __init__(self, stream: Any, *, model: str, operation: str) -> None:
        self._stream = stream
        self._model = model
        self._operation = operation
        self._last: dict | None = None

    def __iter__(self):
        for chunk in self._stream:
            from services.session.token_usage import _coerce_response_dict

            data = _coerce_response_dict(chunk)
            if data:
                self._last = data
            yield chunk
        self._record_last()

    def _record_last(self) -> None:
        if not self._last:
            return
        try:
            from services.session.token_usage import record_from_response

            record_from_response(self._last, model=self._model, operation=self._operation)
        except Exception:
            pass


def list_models() -> list[str]:
    return _backend().list_models()


def pull_model(model_name: str, *, stream: bool = True):
    return _backend().pull(model_name, stream=stream)


def unload_all_llm() -> list[str]:
    return _backend().unload_all()


# Ollama chat helpers (re-exported for pipeline/capabilities)
from services.inference.ollama_chat import (  # noqa: E402
    build_chat_request,
    resolve_enable_thinking,
)
