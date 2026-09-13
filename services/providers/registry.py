# -*- coding: utf-8 -*-
"""Detect and resolve active LLM provider."""
from __future__ import annotations

import concurrent.futures
import logging
import time
from typing import TYPE_CHECKING

from services.providers.base import BaseProvider, ProviderInfo
from services.providers.lmstudio_provider import LMStudioProvider
from services.providers.ollama_provider import OllamaProvider
from services.providers.ollama_provider import _PROBE_TIMEOUT as _OLLAMA_CLIENT_TIMEOUT

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DEFAULT_PROVIDER_ID = "ollama"

_PROVIDERS: dict[str, BaseProvider] = {
    "ollama": OllamaProvider(),
    "lmstudio": LMStudioProvider(),
}

_active_provider_id: str | None = None
_resolved_provider: BaseProvider | None = None
_detect_cache: tuple[float, list[ProviderInfo]] | None = None
_DETECT_CACHE_TTL = 30.0


def _normalize_provider_id(provider_id: str | None) -> str:
    pid = str(provider_id or "").strip()
    if not pid or pid == "none":
        return _DEFAULT_PROVIDER_ID
    if pid not in _PROVIDERS:
        return _DEFAULT_PROVIDER_ID
    return pid


def _pin_provider(provider_id: str) -> BaseProvider:
    global _active_provider_id, _resolved_provider
    pid = _normalize_provider_id(provider_id)
    _active_provider_id = pid
    _resolved_provider = _PROVIDERS[pid]
    return _resolved_provider


# Must be >= the slowest provider client timeout (Ollama's is 10s) — a shorter
# wrapper timeout just abandons the thread early while the real HTTP call keeps
# running in the background for its own full timeout anyway (wasted + confusing
# duplicate "timed out" logs at two different durations).
_PROBE_TIMEOUT_S = _OLLAMA_CLIENT_TIMEOUT + 0.5


def _probe_provider(provider: BaseProvider) -> ProviderInfo:
    try:
        return provider.probe()
    except Exception as exc:
        logger.warning("Provider probe error (%s): %s", provider.provider_id, exc)
        return ProviderInfo(
            provider_id=provider.provider_id,
            label=provider.label,
            base_url=provider.base_url,
            available=False,
        )


def _probe_provider_timed(provider: BaseProvider) -> ProviderInfo:
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(_probe_provider, provider)
    try:
        return fut.result(timeout=_PROBE_TIMEOUT_S)
    except concurrent.futures.TimeoutError:
        logger.warning("Provider probe timed out (%s)", provider.provider_id)
        return ProviderInfo(
            provider_id=provider.provider_id,
            label=provider.label,
            base_url=provider.base_url,
            available=False,
        )
    except Exception as exc:
        logger.warning("Provider probe failed (%s): %s", provider.provider_id, exc)
        return ProviderInfo(
            provider_id=provider.provider_id,
            label=provider.label,
            base_url=provider.base_url,
            available=False,
        )
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def detect_providers(*, force_refresh: bool = False) -> list[ProviderInfo]:
    """Probe Ollama/LM Studio — cached; never call this on the chat hot path."""
    global _detect_cache
    now = time.perf_counter()
    if not force_refresh and _detect_cache and (now - _detect_cache[0]) < _DETECT_CACHE_TTL:
        return list(_detect_cache[1])

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(_PROVIDERS)) as pool:
        results = list(pool.map(_probe_provider_timed, _PROVIDERS.values()))
    _detect_cache = (now, results)
    return list(results)


def get_provider(provider_id: str) -> BaseProvider | None:
    return _PROVIDERS.get(provider_id)


def set_active_provider_id(provider_id: str | None) -> None:
    global _detect_cache
    _detect_cache = None
    _pin_provider(str(provider_id or ""))


def get_active_provider_id() -> str | None:
    if _active_provider_id:
        return _active_provider_id
    try:
        from services.session import state

        setup = (state.current_settings or {}).get("setup") or {}
        pid = setup.get("provider")
        if pid and pid != "none":
            return str(pid)
    except Exception:
        pass
    return None


def resolve_inference_backend() -> BaseProvider:
    """Pin the LLM backend once from settings; never probes other providers."""
    if _resolved_provider is not None:
        return _resolved_provider
    pid = get_active_provider_id()
    if not pid:
        pid = _DEFAULT_PROVIDER_ID
    return _pin_provider(pid)


def get_active_provider() -> BaseProvider:
    if _resolved_provider is not None:
        return _resolved_provider
    return resolve_inference_backend()
