# -*- coding: utf-8 -*-
"""Catalog of atomic LOMA services for routing and orchestration."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceDescriptor:
    id: str
    brief: str
    module: str
    version: str = "0.1.0"


_SERVICES: tuple[ServiceDescriptor, ...] = (
    ServiceDescriptor("llm_bridge", "Chat and text generation via configured LLM providers", "services.llm_bridge"),
    ServiceDescriptor("model_router", "Resolve chat, vision, and image models from profile and hardware", "services.model_router"),
    ServiceDescriptor("resource_governor", "Serialize GPU/CPU-heavy work across subsystems", "services.resource_governor"),
    ServiceDescriptor("file_io", "Parse and read uploaded documents and images", "services.file_io"),
    ServiceDescriptor(
        "document_chunker",
        "Split long parsed documents into overlapping passages for retrieval",
        "services.document_chunker",
    ),
    ServiceDescriptor(
        "text_search",
        "Score and rank passages by keywords and phrases from the user query",
        "services.text_search",
    ),
    ServiceDescriptor(
        "context_selector",
        "Infer retrieval mode and character budget for workspace context",
        "services.context_selector",
    ),
    ServiceDescriptor("web_fetch", "Scrape and extract text from web URLs", "services.web_fetch"),
    ServiceDescriptor("renderer", "Render markdown and office previews to HTML", "services.renderer"),
    ServiceDescriptor(
        "artifact_store",
        "Compile and export office artifacts (docx, pptx, etc.)",
        "services.artifact_store",
    ),
    ServiceDescriptor("image_generation", "Local diffusion image generation", "services.image_generation"),
    ServiceDescriptor("sandbox_runner", "Execute generated code in a sandbox", "services.sandbox_runner"),
    ServiceDescriptor(
        "media_transcription",
        "Transcribe audio and video to timestamped markdown for chat and search",
        "services.media_transcription",
    ),
    ServiceDescriptor(
        "graph_generation",
        "Profile large tabular uploads and render chart PNGs for analysis deliverables",
        "services.graph_generation",
    ),
)


_RUNTIME: dict[str, ServiceDescriptor] = {}


class ServiceRegistry:
    @staticmethod
    def list_all() -> list[ServiceDescriptor]:
        return list(_SERVICES) + list(_RUNTIME.values())

    @staticmethod
    def ids() -> list[str]:
        return [s.id for s in ServiceRegistry.list_all()]

    @staticmethod
    def routing_prompt_block() -> str:
        lines = ["Available atomic services (use exact ids):"]
        for s in ServiceRegistry.list_all():
            lines.append(f"- {s.id}: {s.brief}")
        return "\n".join(lines)

    @staticmethod
    def is_valid(service_id: str) -> bool:
        return service_id in ServiceRegistry.ids()

    @staticmethod
    def register_runtime(service_id: str, brief: str, module: str, version: str = "0.1.0") -> None:
        sid = (service_id or "").strip()
        if not sid:
            return
        _RUNTIME[sid] = ServiceDescriptor(sid, brief, module, version)


service_registry = ServiceRegistry()
