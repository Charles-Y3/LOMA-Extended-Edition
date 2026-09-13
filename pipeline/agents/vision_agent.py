# -*- coding: utf-8 -*-
from pipeline.agents.general_agent import SYSTEM_PROMPT
from pipeline.base.base_agent import BaseAgent
from pipeline.i18n import append_language_rule
from services import llm_bridge as chat_client
from services.model_router import resolve_vision_model
from services.resource_governor import ResourceGovernor


class VisionAgent(BaseAgent):
    role = "Vision"

    def execute(
        self,
        task_description: str,
        context,
        memory_text: str = "",
        images: list | None = None,
        *,
        instruction_md: str = "",
        system_prompt: str = "",
    ) -> str:
        model = resolve_vision_model()
        if not model:
            return "Vision model not available locally."

        image_payload = images or []
        if not image_payload and isinstance(context, list):
            image_payload = [f.get("content") for f in context if f.get("type") == "image" and f.get("content")]

        text_context = context if isinstance(context, str) else ""
        if isinstance(context, list):
            parts = [f.get("content", "") for f in context if isinstance(f, dict) and f.get("type") == "text"]
            text_context = "\n".join(parts)

        if memory_text:
            text_context = f"{memory_text}\n\n{text_context}".strip()

        task = instruction_md.strip() or task_description
        sys_content = append_language_rule(
            system_prompt.strip() or (SYSTEM_PROMPT + "\nYou specialize in visual analysis.")
        )
        user_content = f"Context:\n{text_context}\n\nTask:\n{task}"
        messages = [
            {"role": "system", "content": sys_content},
            {"role": "user", "content": user_content},
        ]
        if image_payload:
            messages[1]["images"] = image_payload

        with ResourceGovernor.acquire("llm_vision"):
            response = chat_client.chat(model=model, messages=messages)
        return response["message"]["content"]


VisionWorker = VisionAgent
