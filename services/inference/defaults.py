# -*- coding: utf-8 -*-
"""Per-model inference settings merged with profile MODEL overrides."""
from __future__ import annotations

from typing import Any

DEFAULT_NUM_CTX = 4096
DEFAULT_MAX_TOKENS = 4096
DEFAULT_ENABLE_THINKING = False


def normalize_inference_defaults(raw: dict | None) -> dict[str, Any]:
    src = raw if isinstance(raw, dict) else {}
    try:
        num_ctx = int(src.get("num_ctx", DEFAULT_NUM_CTX))
    except (TypeError, ValueError):
        num_ctx = DEFAULT_NUM_CTX
    try:
        max_tokens = int(src.get("max_tokens", DEFAULT_MAX_TOKENS))
    except (TypeError, ValueError):
        max_tokens = DEFAULT_MAX_TOKENS
    return {
        "enable_thinking": bool(src.get("enable_thinking", DEFAULT_ENABLE_THINKING)),
        "num_ctx": max(512, min(num_ctx, 131_072)),
        "max_tokens": max(64, min(max_tokens, 32_768)),
    }


def get_global_inference_defaults() -> dict[str, Any]:
    """Fallback template for models without per-model settings."""
    try:
        from services.session import state

        raw = (state.current_settings or {}).get("inference_defaults")
    except Exception:
        raw = None
    return normalize_inference_defaults(raw)


def get_model_inference_settings(model_name: str) -> dict[str, Any]:
    """Per-model settings, falling back to global template defaults."""
    base = get_global_inference_defaults()
    name = (model_name or "").strip()
    if not name:
        return base
    try:
        from services.session import state

        bucket = (state.current_settings or {}).get("model_inference") or {}
        if isinstance(bucket, dict):
            raw = bucket.get(name)
            if isinstance(raw, dict):
                return normalize_inference_defaults({**base, **raw})
    except Exception:
        pass
    return base


def set_model_inference_settings(model_name: str, raw: dict | None) -> dict[str, Any]:
    """Persist normalized per-model inference settings."""
    from services.session import state

    name = (model_name or "").strip()
    if not name:
        return get_global_inference_defaults()
    normalized = normalize_inference_defaults(raw)
    settings = state.current_settings
    bucket = settings.setdefault("model_inference", {})
    if not isinstance(bucket, dict):
        bucket = {}
        settings["model_inference"] = bucket
    bucket[name] = normalized
    return normalized


def drop_model_inference_settings(model_name: str) -> None:
    from services.session import state

    name = (model_name or "").strip()
    if not name:
        return
    bucket = (state.current_settings or {}).get("model_inference") or {}
    if isinstance(bucket, dict) and name in bucket:
        bucket.pop(name, None)


def effective_model_cfg(profile: dict | None, model_name: str = "") -> dict[str, Any]:
    """Per-model defaults, overridden by saved profile MODEL when a profile is active."""
    g = get_model_inference_settings(model_name)
    model_cfg = (profile or {}).get("MODEL") or {}
    if not isinstance(model_cfg, dict):
        model_cfg = {}
    profile_id = str(((profile or {}).get("PROFILE") or {}).get("id") or "")

    from pipeline.base.profile_pack import is_no_profile

    cfg: dict[str, Any] = {
        "temperature": float(model_cfg.get("temperature", 0.3)),
        "max_tokens": int(g["max_tokens"]),
        "num_ctx": int(g["num_ctx"]),
        "enable_thinking": bool(g["enable_thinking"]),
        "keep_alive": str(model_cfg.get("keep_alive") or "30m"),
        "preferred_llm": str(model_cfg.get("preferred_llm") or ""),
        "fallback_llm": str(model_cfg.get("fallback_llm") or ""),
    }
    if not is_no_profile(profile_id):
        if "max_tokens" in model_cfg:
            cfg["max_tokens"] = int(model_cfg["max_tokens"])
        if "num_ctx" in model_cfg:
            cfg["num_ctx"] = int(model_cfg["num_ctx"])
        if "enable_thinking" in model_cfg:
            cfg["enable_thinking"] = bool(model_cfg["enable_thinking"])
        elif "think_enabled" in model_cfg:
            cfg["enable_thinking"] = bool(model_cfg["think_enabled"])
    return cfg
