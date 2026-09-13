# -*- coding: utf-8 -*-
from config import ROLES
from pipeline.base.base_agent import BaseAgent
from pipeline.i18n import append_language_rule
from services import llm_bridge as chat_client
from services.resource_governor import ResourceGovernor

SYSTEM_PROMPT = """You are the LOMA Specialist Worker, a tier-2 language engine for structured tasks.
Your mission is to execute translation, summarization, analysis, extraction, and multi-step text work.

OPERATIONAL PARAMETERS:
1. Accuracy & Fidelity: Maintain conceptual alignment with source context and prior step outputs.
2. Structural Continuity: Preserve markdown, tables, and layout when transforming content.
3. Profile Alignment: Follow glossary and tone rules from the active user profile.
4. Professional Execution: Output results directly without chat filler.
"""


class SpecialistAgent(BaseAgent):
    role = "Specialist"

    def execute(
        self,
        task_description: str,
        context,
        memory_text: str = "",
        *,
        instruction_md: str = "",
        system_prompt: str = "",
    ) -> str:
        if isinstance(context, list):
            text_parts = [f.get("content", "") for f in context if isinstance(f, dict) and f.get("type") == "text"]
            ctx = "\n".join(text_parts)
        else:
            ctx = str(context or "")

        combined = f"{memory_text}\n\n{ctx}".strip() if memory_text else ctx
        task = instruction_md.strip() or task_description
        sys_content = append_language_rule(system_prompt.strip() or SYSTEM_PROMPT)
        model = ROLES.get("Specialist") or ROLES.get("General")
        if not model:
            from services.startup_warmup import routing_model_name

            model = routing_model_name()

        with ResourceGovernor.acquire("llm_chat"):
            response = chat_client.chat(
                model=model,
                messages=[
                    {"role": "system", "content": sys_content},
                    {"role": "user", "content": f"Context:\n{combined}\n\nTask:\n{task}"},
                ],
            )
        return response["message"]["content"]


SpecialistWorker = SpecialistAgent
