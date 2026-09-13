# -*- coding: utf-8 -*-
"""InputRouter: resolve deliverable type and required services (service-first)."""
from __future__ import annotations

import json
import re

from pipeline.output_format import (
    apply_mode_for_output,
    constrain_services_to_deliverable,
    infer_format_from_query,
    required_services_for_deliverable,
    resolve_deliverable_type,
)
from pipeline.registry.service_registry import service_registry
from pipeline.schemas.task_schema import InputMetadata, RoutingDecision
from pipeline.routing_helpers import (
    should_enrich_with_graph_analysis,
    should_route_image_mutation,
    should_route_media_transcription,
)
from services import llm_bridge as chat_client


class InputRouter:
    """Service-first router. It does not inspect file bodies or profile prose."""

    def __init__(self, *, model: str | None = None) -> None:
        import config

        if model:
            self.model = model
        else:
            # Reuse the same resolution startup warm-up uses, so this call always
            # targets an installed, already-warm model instead of a hardcoded
            # default that may not exist (was "phi3:mini" — not installed here,
            # which silently stalled every routing call for ~45s).
            from services.startup_warmup import routing_model_name

            self.model = routing_model_name() or config.ROLES.get("General") or ""

    def route(self, metadata: InputMetadata) -> RoutingDecision:
        query = (metadata.query or "").strip()

        deliverable = resolve_deliverable_type(query, metadata.preferred_output_format)

        required = required_services_for_deliverable(
            deliverable,
            file_count=metadata.file_count,
            link_count=metadata.link_count,
        )

        if not self._should_skip_llm_refine(query, metadata, deliverable):
            required |= self._llm_refine_services(query, metadata, deliverable, set())

        if deliverable == "chat" and "image_generation" in required:
            required.discard("image_generation")

        required = constrain_services_to_deliverable(
            required,
            deliverable,
            file_count=metadata.file_count,
            link_count=metadata.link_count,
        )

        mode = apply_mode_for_output(
            deliverable,
            None,
            user_input=query,
            has_docs=metadata.has_docs,
            has_image=metadata.has_image,
            metadata=metadata,
        )
        # Preview-selection queries are dispatched to the dedicated ask/revise handlers
        # before reaching this router (see handlers.send_message). Any residual selection
        # here is an in-place edit → let the resolver's mutation stand (no generation override).

        required_services = sorted(required)

        if should_route_media_transcription(metadata):
            mode = "generation"
            if "media_transcription" not in required_services:
                required_services = sorted(set(required_services) | {"media_transcription", "file_io"})

        if should_route_image_mutation(metadata):
            mode = "mutation"
            # RoutingDecision.output_type below is bound to `deliverable`, not `mode` —
            # step_executor's mutation branch gates on BOTH output_type == "image" and
            # mode == "mutation", so deliverable must be forced here too, or a plain
            # edit instruction that didn't resolve to "image" on text alone (see
            # should_route_image_mutation's docstring) still falls through to
            # image generation despite mode already being "mutation".
            deliverable = "image"
            required_services = sorted(
                set(required_services) | {"image_generation", "file_io", "llm_bridge"}
            )

        if should_enrich_with_graph_analysis(metadata, deliverable=deliverable):
            required_services = sorted(set(required_services) | {"graph_generation", "file_io"})

        explicit_in_query = infer_format_from_query(query)
        reason = "service-first routing"
        if explicit_in_query and explicit_in_query != metadata.preferred_output_format:
            reason = f"query format ({explicit_in_query}) overrides UI selector"

        return RoutingDecision(
            required_services=required_services,
            output_type=deliverable,
            mode=mode,
            confidence="high",
            reason=reason,
            llm_type="image" if metadata.has_image else "text",
        )

    def _should_skip_llm_refine(
        self,
        query: str,
        metadata: InputMetadata,
        deliverable: str,
    ) -> bool:
        if not (query or "").strip():
            return True
        if deliverable != "chat":
            return False
        if metadata.link_count:
            return False
        if getattr(metadata, "has_docs", False):
            return False
        if metadata.has_valid_preview_selection:
            return False
        return True

    def _llm_refine_services(
        self,
        query: str,
        metadata: InputMetadata,
        deliverable: str,
        base: set[str],
    ) -> set[str]:
        if not query:
            return base

        svc_ids = service_registry.ids()
        prompt = (
            "You are the LOMA Input Router.\n"
            "Given the user query and input metadata, select which atomic services are needed.\n"
            "The deliverable type has already been resolved (query explicit format beats UI selector).\n"
            "Do NOT contradict the resolved deliverable type.\n"
            "If deliverable type is chat, do NOT include image_generation, artifact_store, "
            "renderer, or sandbox_runner.\n"
            "If deliverable type is image, include image_generation (and llm_bridge for prompt refinement).\n"
            "Do NOT analyze attachment contents.\n\n"
            f"Resolved deliverable type: {deliverable}\n"
            f"UI output format selector: {metadata.preferred_output_format}\n\n"
            f"{service_registry.routing_prompt_block()}\n\n"
            "Input metadata (JSON):\n"
            f"{json.dumps(metadata.__dict__, ensure_ascii=False)}\n\n"
            f"User query:\n{query}\n\n"
            "Respond ONLY with valid JSON:\n"
            '{"required_services": ["llm_bridge"], "reason": "..." }\n'
        )

        try:
            from services.inference.ollama_chat import build_model_options
            from services.resource_governor import ResourceGovernor

            with ResourceGovernor.acquire("llm_chat"):
                # Thinking is resolved from settings by llm_bridge.generate() (respects
                # the global "disable thinking" default); num_predict is capped here
                # since this is a small JSON-classification prompt, not open-ended text.
                # num_ctx must come from build_model_options() (the same helper the
                # startup warmup uses) — a request that omits num_ctx falls back to
                # Ollama's own default, which reads as a different load configuration
                # and forces a full model reload even though it's "already warm",
                # reintroducing the exact cold-load delay warmup exists to avoid.
                options = build_model_options(
                    None, {"temperature": 0.0, "num_predict": 512}, model=self.model
                )
                resp = chat_client.generate(
                    model=self.model,
                    prompt=prompt,
                    stream=False,
                    options=options,
                )
            raw = (resp.get("response") or "").strip()
            data = _extract_json_obj(raw)
            services = data.get("required_services")
            if isinstance(services, list):
                for s in services:
                    s = str(s).strip()
                    if s in svc_ids:
                        base.add(s)
            return base
        except Exception:
            return base


def _extract_json_obj(text: str) -> dict:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    blob = (m.group(0) if m else text or "").strip()
    if not blob:
        return {}
    try:
        data = json.loads(blob)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
