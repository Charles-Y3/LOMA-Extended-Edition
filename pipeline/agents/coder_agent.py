# -*- coding: utf-8 -*-
from config import ROLES
from pipeline.base.base_agent import BaseAgent
from pipeline.i18n import append_language_rule
from services import llm_bridge as chat_client
from services.resource_governor import ResourceGovernor

SYSTEM_PROMPT = """You are the LOMA Coder Worker, a specialized software engineering engine.
Your mission is to write, debug, and explain code based on the provided context and task description.
Output clean, runnable code when requested. Explain assumptions briefly when needed.
"""


class CoderAgent(BaseAgent):
    role = "Coder"

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
                model=ROLES["Coder"],
                messages=[
                    {"role": "system", "content": sys_content},
                    {"role": "user", "content": f"Context:\n{combined}\n\nTask:\n{task}"},
                ],
            )
        return response["message"]["content"]


CoderWorker = CoderAgent
