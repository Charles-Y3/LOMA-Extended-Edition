# -*- coding: utf-8 -*-
"""Ollama LLM provider adapter."""
from __future__ import annotations

import logging
import threading
from typing import Any, Iterator

import ollama

import config
from services.providers.base import BaseProvider, ProviderInfo

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 10.0
_PULL_TIMEOUT = 3600.0
_CHAT_TIMEOUT = 600.0

# A chat call whose requested num_ctx differs from what Ollama currently has
# the model loaded at forces a reload with a bigger KV-cache. On a
# VRAM-constrained GPU that reload can stall for a very long time with zero
# visible progress (this is what previously looked like LOMA just hanging).
# Give it this long before assuming it's stuck and falling back to a call
# sized to fit the context that's ALREADY loaded, which needs no reload.
_RESIZE_STALL_TIMEOUT = 90.0


def _loaded_context_length(client: "ollama.Client", model: str) -> int | None:
    """Currently loaded context window for `model`, per Ollama's own /api/ps,
    or None if it isn't loaded (or can't be determined) — a cheap, local
    call, safe to make before any chat request that specifies num_ctx."""
    try:
        ps = client.ps()
    except Exception:
        return None
    for entry in ps.get("models", []) if isinstance(ps, dict) else []:
        name = str(entry.get("name") or entry.get("model") or "")
        if name == model or name.split(":")[0] == model.split(":")[0]:
            ctx = entry.get("context_length")
            if ctx:
                try:
                    return int(ctx)
                except (TypeError, ValueError):
                    return None
    return None


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

    def _chat_with_resize_guard(self, client: "ollama.Client", attempt: dict[str, Any]) -> Any:
        """Run one chat attempt, guarding against a stalled context-size reload.

        If `attempt` asks for a num_ctx different from what's currently
        loaded, Ollama has to reload the model before it can answer. On a
        VRAM-tight machine that reload can stall indefinitely with no
        feedback. This lets it run for _RESIZE_STALL_TIMEOUT seconds; if it
        hasn't returned by then, it fires off a retry sized to fit the
        context that's ALREADY loaded (no reload needed) and returns
        whichever finishes first. The original call is abandoned on its
        daemon thread rather than killed (Python can't interrupt a network
        call) — same abandon-don't-kill pattern as
        services.session.workflow_control.run_cancellable."""
        options = attempt.get("options") or {}
        requested_ctx = options.get("num_ctx")
        model = str(attempt.get("model") or "")
        if not requested_ctx or not model:
            return client.chat(**attempt)

        loaded_ctx = _loaded_context_length(client, model)
        if not loaded_ctx or loaded_ctx == requested_ctx:
            return client.chat(**attempt)

        try:
            from services.session import state

            state.add_log(
                f"Ollama is switching model context size ({loaded_ctx} -> "
                f"{requested_ctx}) — this can take a while on limited VRAM..."
            )
        except Exception:
            pass

        result_box: dict[str, Any] = {}
        error_box: dict[str, BaseException] = {}

        def _run() -> None:
            try:
                result_box["value"] = client.chat(**attempt)
            except Exception as exc:
                error_box["value"] = exc

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        thread.join(_RESIZE_STALL_TIMEOUT)

        if thread.is_alive():
            try:
                from services.session import state

                state.add_log(
                    "Context resize is taking unusually long (likely low GPU "
                    f"memory); retrying at the already-loaded context size "
                    f"({loaded_ctx}) instead of waiting further..."
                )
            except Exception:
                pass
            fallback = dict(attempt)
            fallback_opts = dict(options)
            fallback_opts["num_ctx"] = loaded_ctx
            num_predict = fallback_opts.get("num_predict")
            if isinstance(num_predict, int) and num_predict > loaded_ctx - 512:
                fallback_opts["num_predict"] = max(256, loaded_ctx - 512)
            fallback["options"] = fallback_opts
            try:
                return client.chat(**fallback)
            except Exception as exc:
                raise RuntimeError(
                    "context_resize_stalled: model context resize did not "
                    "complete in time and the fallback at the smaller, "
                    "already-loaded context also failed"
                ) from exc

        if "value" in result_box:
            return result_box["value"]
        raise error_box.get("value") or RuntimeError("Ollama chat failed with no result")

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
                return self._chat_with_resize_guard(client, attempt)
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
