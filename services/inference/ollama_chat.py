# -*- coding: utf-8 -*-
"""Build Ollama chat kwargs from profile MODEL settings (including native think mode)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from services.model_router import _name_suggests_thinking, model_supports_thinking

ThinkLevel = Literal["low", "medium", "high"]
ThinkKwarg = bool | ThinkLevel | None

_DEFAULT_KEEP_ALIVE = "30m"

_DEBUG_LOG = Path(__file__).resolve().parents[2] / "debug-d4eaa9.log"


def _agent_debug_log(
    location: str,
    message: str,
    data: dict,
    hypothesis_id: str,
    *,
    run_id: str = "pre-fix",
) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "d4eaa9",
            "timestamp": int(time.time() * 1000),
            "location": location,
            "message": message,
            "data": data,
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion


def resolve_enable_thinking(profile: dict | None, model: str = "") -> bool:
    """Thinking mode from per-model settings, profile override, or default off."""
    from services.inference.defaults import effective_model_cfg

    return bool(effective_model_cfg(profile, model).get("enable_thinking", False))


def resolve_keep_alive(profile: dict | None) -> str:
    """Ollama keep_alive duration — keeps model loaded between requests."""
    model_cfg = (profile or {}).get("MODEL") or {}
    if not isinstance(model_cfg, dict):
        return _DEFAULT_KEEP_ALIVE
    val = model_cfg.get("keep_alive")
    if val is not None and str(val).strip():
        return str(val).strip()
    return _DEFAULT_KEEP_ALIVE


def resolve_think_kwarg(profile: dict | None, model: str) -> ThinkKwarg:
    """
    Map profile toggle + model capabilities to Ollama's think parameter.
    Non-thinking models never receive think (avoids 400 errors).
    Thinking models use think=False when disabled so reasoning is skipped.
    """
    enabled = resolve_enable_thinking(profile, model)
    if not enabled:
        if _name_suggests_thinking(model):
            return False
        return None
    supports = model_supports_thinking(model)
    if not supports:
        return None
    if enabled:
        return "medium"
    return False


def build_model_options(
    profile: dict | None,
    extra_options: dict | None = None,
    *,
    model: str = "",
) -> dict[str, Any]:
    """Temperature and token limits from per-model defaults + profile."""
    from services.inference.defaults import effective_model_cfg

    cfg = effective_model_cfg(profile, model)
    options: dict[str, Any] = {
        "temperature": float(cfg.get("temperature", 0.3)),
        "num_predict": int(cfg.get("max_tokens", 4096)),
        "num_ctx": int(cfg.get("num_ctx", 4096)),
    }
    if extra_options:
        options.update(extra_options)
    return options


def _messages_have_images(messages: list) -> bool:
    for msg in messages or []:
        if not isinstance(msg, dict):
            continue
        imgs = msg.get("images")
        if imgs:
            return True
    return False


def build_chat_request(
    profile: dict | None,
    *,
    model: str,
    messages: list,
    stream: bool = False,
    extra_options: dict | None = None,
    disable_thinking: bool = False,
    response_format: dict | None = None,
) -> tuple[dict[str, Any], dict[str, float]]:
    """Kwargs for ollama.chat plus timing metadata for diagnostics.

    response_format, when given, is a JSON schema passed through as Ollama's
    `format` — this constrains the SHAPE of the response (the model literally
    cannot emit free-form prose), unlike a system-prompt instruction the model
    can simply ignore. Confirmed: an instruction alone ("return nothing if
    declining") got a conversational refusal sentence back instead of an empty
    string from more than one model; the schema-constrained version reliably
    returned an empty field instead, across every model tested."""
    if not str(model or "").strip():
        # Every LLM call in the app funnels through here — catch a missing model
        # (no provider reachable, or none downloaded) here with a clean, translated
        # error instead of letting an empty model string reach the Ollama/LM Studio
        # client, which fails with an opaque pydantic validation error instead.
        from pipeline.i18n import t as tr
        from services.model_router import NoChatModelError

        raise NoChatModelError(tr("chat.no_models_installed"))
    t0 = time.perf_counter()
    opts = build_model_options(profile, extra_options, model=model)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": stream,
        "options": opts,
        "keep_alive": resolve_keep_alive(profile),
    }
    if response_format is not None:
        kwargs["format"] = response_format
    probe_ms = 0.0
    if disable_thinking:
        think: ThinkKwarg = False
    elif _messages_have_images(messages):
        think = False
    else:
        t_probe = time.perf_counter()
        think = resolve_think_kwarg(profile, model)
        probe_ms = (time.perf_counter() - t_probe) * 1000
    if think is not None:
        kwargs["think"] = think
    timings = {
        "build_ms": round((time.perf_counter() - t0) * 1000, 1),
        "think_probe_ms": round(probe_ms, 1),
    }
    _agent_debug_log(
        "ollama_chat.py:build_chat_request",
        "chat kwargs resolved",
        {
            "model": model,
            "think": think,
            "disable_thinking": disable_thinking,
            "profile_thinking": resolve_enable_thinking(profile, model),
            "stream": stream,
            **timings,
        },
        "H1-H3",
    )
    return kwargs, timings


def build_planner_chat_request(
    profile: dict | None,
    *,
    model: str,
    messages: list,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Planner/routing JSON — never use reasoning even when user enables thinking."""
    kwargs, _ = build_chat_request(
        profile,
        model=model,
        messages=messages,
        stream=False,
        disable_thinking=True,
        extra_options={"temperature": temperature, "num_predict": 512},
    )
    return kwargs
