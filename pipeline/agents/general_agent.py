# -*- coding: utf-8 -*-
from config import ROLES
from pipeline.base.base_agent import BaseAgent
from pipeline.i18n import append_language_rule
from services import llm_bridge as chat_client
from services.resource_governor import ResourceGovernor

SYSTEM_PROMPT = """You are the LOMA General Worker, a specialized language and text processing engine.
Your mission is to execute general text-based tasks, file summaries, translations, and context extraction.

OPERATIONAL PARAMETERS:
1. Accuracy & Fidelity: Maintain perfect conceptual alignment with the source document context.
2. Structural Continuity: When translating or reformatting, preserve markdown structures, table layout styles, and maintain 1:1 paragraph correlation with the source materials where applicable.
3. Glossary Alignment: Respect and strictly prioritize specific glossary mapping transformations or target tones requested by the active user profile guidelines.
4. Professional Execution: Output results directly without unnecessary chat introductions (e.g., avoid "Sure, here is your translation").
"""


class GeneralAgent(BaseAgent):
    role = "General"

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

        with ResourceGovernor.acquire("llm_chat"):
            response = chat_client.chat(
                model=ROLES["General"],
                messages=[
                    {"role": "system", "content": sys_content},
                    {"role": "user", "content": f"Context:\n{combined}\n\nTask:\n{task}"},
                ],
            )
        return response["message"]["content"]


GeneralWorker = GeneralAgent
