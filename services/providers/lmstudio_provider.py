# -*- coding: utf-8 -*-
"""LM Studio OpenAI-compatible provider adapter."""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Iterator

import config
from services.providers.base import BaseProvider, ProviderCapabilities, ProviderInfo

logger = logging.getLogger(__name__)


def _to_openai_messages(messages: list) -> list[dict]:
    """Convert Ollama-shaped messages (a message may carry an "images" list of file
    paths/base64/data-URIs) into OpenAI's content-array shape LM Studio's
    /v1/chat/completions actually understands. Ollama's "images" field is silently
    ignored by an OpenAI-compatible server otherwise — the model never sees the
    image and still answers, just wrong (see services/vision_input.py for the
    per-item conversion, which also drops non-image payloads like the
    audio-via-images trick in services/media_transcription.py rather than mis-sending
    them as broken images)."""
    from services.vision_input import to_openai_content

    out: list[dict] = []
    for msg in messages or []:
        images = msg.get("images")
        if not images:
            if "images" in msg:
                cleaned = dict(msg)
                cleaned.pop("images", None)
                out.append(cleaned)
            else:
                out.append(msg)
            continue
        converted = {k: v for k, v in msg.items() if k != "images"}
        converted["content"] = to_openai_content(str(msg.get("content") or ""), images)
        out.append(converted)
    return out


def _friendly_chat_error(
    exc: urllib.error.HTTPError, label: str, dropped_num_ctx: float | int | None
) -> Exception:
    """LOMA can't force a context-window size on LM Studio per-request (unlike
    Ollama, the OpenAI-compatible API has no such parameter — the window is fixed by
    however the model was loaded in LM Studio's own UI). When a request that would
    have needed a wider window fails, turn the HTTP error into something actionable
    instead of a raw 400 traceback."""
    from pipeline.i18n import t as tr

    try:
        detail = exc.read().decode("utf-8", errors="replace")
    except Exception:
        detail = str(exc)
    if dropped_num_ctx and any(
        kw in detail.lower() for kw in ("context", "n_ctx", "too long", "maximum length")
    ):
        return RuntimeError(tr("chat.lmstudio_context_exceeded", label=label))
    return exc


class LMStudioProvider(BaseProvider):
    provider_id = "lmstudio"
    label = "LM Studio"
    supports_remote_pull = False

    def __init__(self, base_url: str | None = None) -> None:
        raw = (base_url or config.LMSTUDIO_BASE_URL).rstrip("/")
        if "localhost" in raw.lower():
            raw = raw.replace("localhost", "127.0.0.1").replace("LOCALHOST", "127.0.0.1")
        self._base_url = raw

    @property
    def base_url(self) -> str:
        return self._base_url

    def capabilities(self) -> ProviderCapabilities:
        # OpenAI-compatible API: no per-request context sizing (window is fixed at
        # model-load in LM Studio's UI), and keep_alive/think are not honored. The
        # loaded context size isn't exposed via /v1/models either, so the governor
        # relies on shrink + multi-pass and the friendly context-exceeded error.
        return ProviderCapabilities(
            can_set_ctx=False,
            reports_loaded_ctx=False,
            supports_keep_alive=False,
            supports_think_param=False,
        )

    def _request(self, path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 5) -> Any:
        url = f"{self._base_url}{path}"
        data = None
        headers = {"Content-Type": "application/json"}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}

    def probe(self) -> ProviderInfo:
        from pipeline.i18n import t as tr

        available = False
        count = 0
        error_hint = ""
        try:
            # 1s was too tight — a cold LM Studio instance (server just started, or a
            # model still loading into memory) can take longer than that to answer its
            # first request, which made a genuinely-running server look "not detected".
            payload = self._request("/v1/models", timeout=3)
            models = payload.get("data", [])
            count = len(models)
            available = True
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if "refused" in str(reason).lower():
                error_hint = tr("setup.provider.not_started", url=self._base_url, label=self.label)
            else:
                error_hint = tr("setup.provider.probe_error", url=self._base_url, detail=reason)
            logger.debug("LM Studio probe failed: %s", exc)
        except Exception as exc:
            error_hint = tr("setup.provider.probe_error", url=self._base_url, detail=exc)
            logger.debug("LM Studio probe failed: %s", exc)
        return ProviderInfo(
            provider_id=self.provider_id,
            label=self.label,
            base_url=self._base_url,
            available=available,
            model_count=count,
            install_url="https://lmstudio.ai/",
            install_hint=tr("setup.provider.lmstudio_install_hint"),
            error_hint=error_hint,
        )

    def list_models(self) -> list[str]:
        try:
            payload = self._request("/v1/models")
            return [str(m.get("id")) for m in payload.get("data", []) if m.get("id")]
        except Exception as exc:
            logger.error("LM Studio list_models failed: %s", exc)
            return []

    def pull(self, model_name: str, *, stream: bool = True) -> Iterator[dict[str, Any]]:
        # Must raise, not yield-and-return: a caller iterating this as a progress
        # stream (ui/components/asset_downloader.py) treats a generator that ends
        # without raising as a completed download, then reports "installed" and
        # assigns model_name as a role even though nothing was fetched. LM Studio has
        # no download endpoint — supports_remote_pull=False is the signal callers
        # should check *before* calling this at all (see ChatModelTierPicker).
        from pipeline.i18n import t as tr

        raise NotImplementedError(tr("setup.provider.no_remote_pull", label=self.label))
        yield {}  # pragma: no cover — keeps this a generator for the Iterator contract

    def chat(self, **kwargs: Any) -> Any:
        kwargs.pop("think", None)
        # Ollama-only: no per-request equivalent exists in the OpenAI-compatible API —
        # context size is fixed by however the model was loaded in LM Studio itself.
        kwargs.pop("keep_alive", None)
        options = kwargs.pop("options", {}) or {}
        dropped_num_ctx = options.get("num_ctx")
        stream = kwargs.pop("stream", False)
        model = kwargs.pop("model", "")
        messages = _to_openai_messages(kwargs.pop("messages", []))
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": stream,
        }
        if options.get("temperature") is not None:
            body["temperature"] = options["temperature"]
        if options.get("num_predict") is not None:
            body["max_tokens"] = options["num_predict"]
        if options.get("top_p") is not None:
            body["top_p"] = options["top_p"]

        if not stream:
            try:
                payload = self._request("/v1/chat/completions", method="POST", body=body, timeout=300)
            except urllib.error.HTTPError as exc:
                raise _friendly_chat_error(exc, self.label, dropped_num_ctx) from exc
            choice = (payload.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            out: dict[str, Any] = {"message": {"role": "assistant", "content": message.get("content", "")}}
            usage = payload.get("usage")
            if isinstance(usage, dict):
                out["usage"] = usage
            return out

        return self._stream_chat(body, dropped_num_ctx=dropped_num_ctx)

    def _stream_chat(
        self, body: dict[str, Any], *, dropped_num_ctx: float | int | None = None
    ) -> Iterator[dict[str, Any]]:
        url = f"{self._base_url}/v1/chat/completions"
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    chunk_str = line[5:].strip()
                    if chunk_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(chunk_str)
                    except json.JSONDecodeError:
                        continue
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    content = delta.get("content") or ""
                    out: dict[str, Any] = {}
                    if content:
                        out["message"] = {"role": "assistant", "content": content}
                    usage = chunk.get("usage")
                    if isinstance(usage, dict):
                        out["usage"] = usage
                    if out:
                        yield out
        except urllib.error.HTTPError as exc:
            logger.error("LM Studio stream chat failed: %s", exc)
            raise _friendly_chat_error(exc, self.label, dropped_num_ctx) from exc

    def generate(self, **kwargs: Any) -> Any:
        model = kwargs.get("model", "")
        prompt = kwargs.get("prompt", "")
        stream = kwargs.get("stream", False)
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if stream:
            return self._stream_chat(body)
        payload = self._request("/v1/chat/completions", method="POST", body=body, timeout=300)
        choice = (payload.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return {"response": message.get("content", "")}

    def unload_all(self) -> list[str]:
        logger.info("LM Studio unload: best-effort — unload models via LM Studio UI if VRAM is tight.")
        return []
