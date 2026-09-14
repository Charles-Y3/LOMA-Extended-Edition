# -*- coding: utf-8 -*-
"""Ollama LLM provider adapter."""
from __future__ import annotations

import logging
from typing import Any, Iterator

import ollama

import config
from services.providers.base import BaseProvider, ProviderCapabilities, ProviderInfo

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 10.0
_PULL_TIMEOUT = 3600.0
_CHAT_TIMEOUT = 600.0


def _is_sequence_batch_error(exc: BaseException) -> bool:
    parts = [str(exc)]
    for attr in ("error", "body", "message"):
        val = getattr(exc, attr, None)
        if val is not None:
            parts.append(str(val))
    msg = " ".join(parts).lower()
    return (
        "samebatch" in msg
        or "numkeep" in msg
        or "failed to create new sequence" in msg
    )


def _chat_retry_variants(kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    """Build fallback kwargs for llama.cpp SameBatch / numKeep failures."""
    variants: list[dict[str, Any]] = [dict(kwargs)]
    stripped = dict(kwargs)
    if stripped.pop("think", None) is not None:
        variants.append(stripped)
    if _kwargs_have_images(kwargs):
        return variants
    opts = dict(stripped.get("options") or {})
    if opts.get("num_batch") != 1:
        retry = dict(stripped)
        retry_opts = dict(opts)
        retry_opts["num_batch"] = 1
        retry["options"] = retry_opts
        if retry not in variants:
            variants.append(retry)
    return variants


def _kwargs_have_images(kwargs: dict[str, Any]) -> bool:
    for msg in kwargs.get("messages") or []:
        if msg.get("images"):
            return True
    return bool(kwargs.get("images"))


class OllamaProvider(BaseProvider):
    provider_id = "ollama"
    label = "Ollama"

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url or config.OLLAMA_BASE_URL
        self._client = ollama.Client(host=self._base_url, timeout=_PROBE_TIMEOUT)

    @property
    def base_url(self) -> str:
        return self._base_url

    def capabilities(self) -> ProviderCapabilities:
        # Ollama honors num_ctx/keep_alive/think per request and exposes a model's
        # trained context length via show() — full capability.
        return ProviderCapabilities(
            can_set_ctx=True,
            reports_loaded_ctx=True,
            supports_keep_alive=True,
            supports_think_param=True,
        )

    def loaded_context_length(self, model: str) -> int | None:
        """Best-effort: the model's trained context length from Ollama's show().
        Ollama keys it per-architecture (e.g. "qwen2.context_length"), so scan for
        any *.context_length / context_length entry. None on any failure — the
        governor then falls back to its hardware estimate."""
        if not model:
            return None
        try:
            info = self._client.show(model)
        except Exception as exc:
            logger.debug("Ollama show(%s) failed: %s", model, exc)
            return None
        model_info = {}
        if isinstance(info, dict):
            model_info = info.get("modelinfo") or info.get("model_info") or {}
        else:
            model_info = getattr(info, "modelinfo", None) or getattr(info, "model_info", None) or {}
        if isinstance(model_info, dict):
            for key, val in model_info.items():
                if str(key).endswith("context_length"):
                    try:
                        n = int(val)
                        if n > 0:
                            return n
                    except (TypeError, ValueError):
                        continue
        return None

    def probe(self) -> ProviderInfo:
        from pipeline.i18n import t as tr

        available = False
        count = 0
        error_hint = ""
        try:
            response = self._client.list()
            models = response.get("models", [])
            count = len(models)
            available = True
        except Exception as exc:
            if "refused" in str(exc).lower() or "connect" in str(exc).lower():
                error_hint = tr("setup.provider.not_started", url=self._base_url, label=self.label)
            else:
                error_hint = tr("setup.provider.probe_error", url=self._base_url, detail=exc)
            logger.debug("Ollama probe failed: %s", exc)
        return ProviderInfo(
            provider_id=self.provider_id,
            label=self.label,
            base_url=self._base_url,
            available=available,
            model_count=count,
            install_url="https://ollama.com/download",
            install_hint=tr("setup.provider.ollama_install_hint"),
            error_hint=error_hint,
        )

    def list_models(self) -> list[str]:
        try:
            response = self._client.list()
            models = response.get("models", [])
            return [
                str(m.get("name") or m.get("model"))
                for m in models
                if m.get("name") or m.get("model")
            ]
        except Exception as exc:
            logger.error("Ollama list_models failed: %s", exc)
            return []

    def pull(self, model_name: str, *, stream: bool = True) -> Iterator[dict[str, Any]]:
        client = ollama.Client(host=self._base_url, timeout=_PULL_TIMEOUT)
        if stream:
            yield from client.pull(model_name, stream=True)
            return
        client.pull(model_name, stream=False)
        yield {"status": "success"}

    def delete_model(self, model_name: str) -> None:
        client = ollama.Client(host=self._base_url, timeout=120.0)
        client.delete(model_name)

    def chat(self, **kwargs: Any) -> Any:
        from services.vision_input import (
            downscale_images_in_messages,
            is_vision_server_crash,
            vision_crash_user_message,
            vision_fallback_max_edge,
        )

        client = ollama.Client(host=self._base_url, timeout=_CHAT_TIMEOUT)
        variants = _chat_retry_variants(kwargs)
        last_exc: BaseException | None = None
        for idx, attempt in enumerate(variants):
            try:
                return client.chat(**attempt)
            except Exception as exc:
                last_exc = exc
                if _is_sequence_batch_error(exc) and idx < len(variants) - 1:
                    logger.warning(
                        "Ollama chat sequence error; retrying with safer options (%s/%s)",
                        idx + 2,
                        len(variants),
                    )
                    try:
                        from services.session import state

                        state.add_log(
                            "Ollama KV-cache limit hit; retrying without reasoning mode…"
                        )
                    except Exception:
                        pass
                    continue
                if is_vision_server_crash(exc) and _kwargs_have_images(attempt):
                    smaller = dict(attempt)
                    smaller["messages"] = downscale_images_in_messages(
                        attempt.get("messages") or [],
                        max_edge=vision_fallback_max_edge(),
                    )
                    try:
                        return client.chat(**smaller)
                    except Exception as retry_exc:
                        if is_vision_server_crash(retry_exc):
                            raise RuntimeError(vision_crash_user_message()) from retry_exc
                        raise
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("Ollama chat failed with no exception")

    def generate(self, **kwargs: Any) -> Any:
        client = ollama.Client(host=self._base_url, timeout=_CHAT_TIMEOUT)
        return client.generate(**kwargs)

    def unload_all(self) -> list[str]:
        unloaded: list[str] = []
        try:
            ps = self._client.ps()
            for entry in ps.get("models", []):
                name = str(entry.get("name") or entry.get("model") or "").strip()
                if not name:
                    continue
                try:
                    self._client.generate(model=name, prompt="", keep_alive=0)
                    unloaded.append(name)
                except Exception as exc:
                    logger.warning("Failed to unload Ollama model %s: %s", name, exc)
        except Exception as exc:
            logger.warning("Ollama ps/unload failed: %s", exc)
        return unloaded
