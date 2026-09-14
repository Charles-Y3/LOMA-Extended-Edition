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


def _govern(kwargs: dict[str, Any], *, text_key: str) -> dict[str, Any]:
    """Context Governor spine hook (docs/PIPELINE_REFACTOR.md §1): raise num_ctx to
    fit the prompt+output, within the machine's ceiling, on every call. Raise-only and
    fully defensive — on any error the caller's kwargs are returned unchanged."""
    try:
        from services.context_governor import _messages_text, govern_options

        model = str(kwargs.get("model") or "")
        if text_key == "messages":
            text = _messages_text(kwargs.get("messages"))
        else:
            text = str(kwargs.get(text_key) or "")
        out = dict(kwargs)
        out["options"] = govern_options(text=text, options=kwargs.get("options"), model=model)
        return out
    except Exception:
        return kwargs


def _ctx_ceiling(model: str) -> int:
    try:
        from pipeline.direct.batch_budget import resolve_batch_budget

        b = resolve_batch_budget(None, model or "")
        return int(b.get("num_ctx_raw") or b.get("num_ctx") or 0)
    except Exception:
        return 0


def _run_with_ctx_retry(fn, *, model: str, call_kwargs: dict[str, Any]) -> Any:
    """Safety net: if a call overflows context and the backend can resize, retry once
    with a wider window. The user never sees a raw context-length error on Ollama; on a
    fixed-window backend the provider already raises a translated, actionable message."""
    from services.session.workflow_control import run_cancellable

    try:
        return run_cancellable(fn, **call_kwargs)
    except Exception as exc:
        try:
            from services.context_governor import is_context_overflow_error

            if not is_context_overflow_error(exc):
                raise
            opts = dict(call_kwargs.get("options") or {})
            cur = int(opts.get("num_ctx") or 0)
            ceiling = _ctx_ceiling(model)
            bumped = min(max(cur * 2, 8192), ceiling) if ceiling else 0
            if not (bumped and bumped > cur):
                raise
            opts["num_ctx"] = bumped
            retry = dict(call_kwargs)
            retry["options"] = opts
            try:
                from services.session import state

                state.add_log(
                    f"Context overflow — retrying with a wider window (num_ctx {cur:,} → {bumped:,})…"
                )
            except Exception:
                pass
        except Exception:
            raise exc
        return run_cancellable(fn, **retry)


def chat(**kwargs: Any) -> Any:
    stream = bool(kwargs.get("stream"))
    model = str(kwargs.get("model") or "unknown")
    backend = _backend()
    call_kwargs = _govern(_with_keep_alive(_apply_think_setting(kwargs)), text_key="messages")
    if stream:
        return _StreamUsageRecorder(backend.chat(**call_kwargs), model=model, operation="chat")
    # Non-streaming calls block until the whole response arrives — Stop must be able to
    # unwind this immediately (e.g. plan-mode orchestration), not just wait it out.
    result = _run_with_ctx_retry(backend.chat, model=model, call_kwargs=call_kwargs)
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
    call_kwargs = _govern(_with_keep_alive(_apply_think_setting(kwargs)), text_key="prompt")
    if stream:
        return _StreamUsageRecorder(backend.generate(**call_kwargs), model=model, operation="generate")
    result = _run_with_ctx_retry(backend.generate, model=model, call_kwargs=call_kwargs)
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
