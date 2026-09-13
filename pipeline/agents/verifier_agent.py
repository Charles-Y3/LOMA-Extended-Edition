# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass

from config import ROLES
from pipeline.base.base_agent import BaseAgent
from pipeline.i18n import append_language_rule
from services import llm_bridge as chat_client
from services.resource_governor import ResourceGovernor

SYSTEM_PROMPT = """You are the LOMA Verifier, an objective quality assurance module.
Your mission is to critically cross-examine a worker's output against the original target task criteria.

CRITICAL OUTPUT CONSTRAINTS:
- If the output satisfies all explicit conditions of the assigned task description and acceptance criteria, respond with exactly: VALID
- If the output falls short, has formatting distortions, or omits key information, respond with exactly: INVALID: [followed by a brief, precise description of the failure reason]

Do not provide conversational introductions. Start your response immediately with either 'VALID' or 'INVALID:'.
"""


@dataclass
class VerificationResult:
    valid: bool
    reason: str
    raw: str = ""


class VerifierAgent(BaseAgent):
    role = "Verifier"

    def verify(
        self,
        original_task: str,
        worker_output: str,
        acceptance_criteria: list[str] | None = None,
    ) -> VerificationResult:
        criteria_block = ""
        if acceptance_criteria:
            criteria_block = "\nAcceptance criteria:\n" + "\n".join(f"- {c}" for c in acceptance_criteria)
        check_input = f"Task: {original_task}{criteria_block}\n\nResult: {worker_output}"
        with ResourceGovernor.acquire("llm_chat"):
            response = chat_client.chat(
                model=ROLES["Verifier"],
                messages=[
                    {"role": "system", "content": append_language_rule(SYSTEM_PROMPT)},
                    {"role": "user", "content": check_input},
                ],
            )
        raw = response["message"]["content"]
        return self._parse_verification(raw)

    @staticmethod
    def _parse_verification(raw: str) -> VerificationResult:
        text = (raw or "").strip()
        upper = text.upper()
        if upper.startswith("VALID"):
            return VerificationResult(valid=True, reason="", raw=text)
        if "INVALID" in upper:
            reason = text.split(":", 1)[-1].strip() if ":" in text else text
            return VerificationResult(valid=False, reason=reason or text, raw=text)
        if "VALID" in upper:
            return VerificationResult(valid=True, reason="", raw=text)
        return VerificationResult(valid=False, reason=text or "Unparseable verifier response", raw=text)

    def execute(self, task_description: str, context, memory_text: str = "") -> str:
        result = self.verify(task_description, str(context or ""))
        return "VALID" if result.valid else f"INVALID: {result.reason}"


VerifierWorker = VerifierAgent
