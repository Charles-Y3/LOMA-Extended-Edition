# -*- coding: utf-8 -*-
"""LLM provider protocol for Ollama and LM Studio."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ProviderInfo:
    provider_id: str
    label: str
    base_url: str
    available: bool = False
    model_count: int = 0
    install_url: str = ""
    install_hint: str = ""
    error_hint: str = ""


@dataclass
class ProviderCapabilities:
    """What a backend can actually do, so callers — above all the Context Governor
    (docs/PIPELINE_REFACTOR.md §0.5 #2) — branch on real capability instead of
    guessing per provider. Defaults describe the Ollama reference backend; a provider
    overrides only what differs (e.g. LM Studio can't set context per request).

    - can_set_ctx: num_ctx is settable per request. When False, the governor cannot
      widen the window — it must fit the work to the already-loaded ctx (shrink +
      multi-pass) and rely on the error surface if even that won't fit.
    - reports_loaded_ctx: loaded_context_length() can return a real number (lets the
      governor know the hard ceiling up front rather than discovering it on failure).
    - supports_keep_alive / supports_think_param: the Ollama-only `keep_alive` /
      `think` kwargs are honored rather than silently dropped.
    """

    can_set_ctx: bool = True
    reports_loaded_ctx: bool = False
    supports_keep_alive: bool = True
    supports_think_param: bool = True


class BaseProvider(ABC):
    provider_id: str = ""
    label: str = ""
    # False for a provider whose pull() can't actually fetch a model (e.g. LM Studio,
    # which is bring-your-own-model — its OpenAI-compatible API has no download
    # endpoint). Pickers should offer a choice among already-installed models instead
    # of a catalog download when this is False.
    supports_remote_pull: bool = True

    @abstractmethod
    def probe(self) -> ProviderInfo:
        """Check whether the provider daemon/API is reachable."""

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return installed/available model names."""

    @abstractmethod
    def pull(self, model_name: str, *, stream: bool = True) -> Iterator[dict[str, Any]]:
        """Download a model; yields progress chunks when stream=True."""

    @abstractmethod
    def chat(self, **kwargs: Any) -> Any:
        """Chat completion (stream or single response)."""

    @abstractmethod
    def generate(self, **kwargs: Any) -> Any:
        """Text generation (non-chat)."""

    @abstractmethod
    def unload_all(self) -> list[str]:
        """Release loaded models from VRAM; returns unloaded model names."""

    def capabilities(self) -> ProviderCapabilities:
        """What this backend can do. Default = Ollama-reference (full capability);
        providers override only what differs."""
        return ProviderCapabilities()

    def loaded_context_length(self, model: str) -> int | None:
        """The model's currently-loaded context window in tokens, if the backend
        exposes it; None when unknown. Lets the governor know the hard ceiling before
        sending rather than discovering it from a failed request."""
        return None

    @property
    @abstractmethod
    def base_url(self) -> str:
        pass
