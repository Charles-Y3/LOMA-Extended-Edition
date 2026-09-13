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

    @property
    @abstractmethod
    def base_url(self) -> str:
        pass
