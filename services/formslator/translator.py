# -*- coding: utf-8 -*-
"""LOMA-backed translator with Nida prompts and context-aware memory."""
from __future__ import annotations

import re
from typing import Callable, Dict, Optional

from services.formslator.language_detect import language_meta
from services.formslator.translate_engine import (
    _segment_paragraph,
    _slice_glossary_for_text,
    clean_repetitions,
    is_translation_valid,
    post_clean_output,
)

LogFn = Callable[[str], None]


class FormslatorTranslator:
    """Mirrors reference QwenTranslator (Nida + context memory) using LOMA llm_bridge."""

    def __init__(
        self,
        model_name: str,
        source_code: str = "zh",
        target_code: str = "en",
        max_new_tokens: int = 2560,
        temperature: float = 0.1,
        top_p: float = 0.9,
        log_fn: Optional[LogFn] = None,
        target_name: str | None = None,
    ):
        self.model_name = model_name
        self.meta = language_meta(source_code, target_code, target_name=target_name)
        self.max_new_tokens = max_new_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.log: LogFn = log_fn or (lambda *_a, **_k: None)

    def _build_prompt(
        self,
        source_text: str,
        local_glossary: Dict[str, str],
        previous_context: str = "",
    ) -> tuple[str, str]:
        target = self.meta["target_name"]
        source = self.meta["source_name"]

        context_block = ""
        if previous_context:
            context_block = (
                "For stylistic and terminological consistency, here is the immediate previous translation:\n"
                f"[{previous_context}]\n"
                "Do NOT re-translate the previous context. Continue translating the new text smoothly from there.\n\n"
            )

        gloss_block = ""
        if local_glossary:
            gloss_lines = "\n".join(f"- {term}: {tr}" for term, tr in local_glossary.items())
            gloss_block = f"Mandatory Glossary (Integrate naturally):\n{gloss_lines}\n\n"

        system = (
            f"You are an expert translator applying Nida's Functional Equivalence, "
            f"translating from {source} to {target}.\n"
            "1. Faithfulness: Translate the underlying intent and nuances faithfully.\n"
            "2. Naturalness: Use idiomatic expressions and structures native to the target language. "
            "Avoid awkward literalisms.\n"
            "3. Equivalent Response: Match the register and emotional weight of the source.\n"
            "Achieve naturalness without omitting any specific data points or technical details.\n"
            "Output ONLY the translation. Do not repeat the source text, provide alternatives, or add commentary.\n"
            f"{context_block}"
            f"{gloss_block}"
        )
        return system, source_text

    def _build_short_prompt(
        self,
        source_text: str,
        local_glossary: Dict[str, str],
        previous_context: str = "",
    ) -> tuple[str, str]:
        target = self.meta["target_name"]
        source = self.meta["source_name"]

        context_block = ""
        if previous_context:
            context_block = f"Previous translation context: [{previous_context}]\n\n"

        gloss_block = ""
        if local_glossary:
            gloss_lines = "\n".join(f"- {term}: {tr}" for term, tr in local_glossary.items())
            gloss_block = f"Mandatory Glossary:\n{gloss_lines}\n\n"

        system = (
            f"You are a professional {target} translator.\n"
            f"TASK: Translate the following short phrase from {source} into natural {target}.\n"
            "RULES:\n"
            "1. Output ONLY the translated text.\n"
            "2. Do NOT repeat the source text.\n"
            "3. Do NOT provide explanations.\n"
            "4. If it is a heading, translate it as a heading.\n"
            f"{context_block}"
            f"{gloss_block}"
        )
        user = f"Source: {source_text}"
        return system, user

    def _llm_generate(self, system: str, user: str, temperature: float) -> str:
        from services.llm_bridge import chat
        from services.formslator.resource_budget import compute_translate_budget

        # Each call here is one short paragraph (_segment_paragraph caps segments at
        # ~600 chars, splitting further on repeated failure), so num_ctx doesn't need
        # to scale with this specific prompt — but it was previously left at whatever
        # Ollama's model-file default is, rather than sized to this machine like every
        # other translation call site in the app.
        num_ctx = compute_translate_budget(self.model_name).get("num_ctx", 4096)
        response = chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            stream=False,
            think=False,
            options={
                "temperature": temperature,
                "top_p": self.top_p,
                "num_predict": self.max_new_tokens,
                "num_ctx": num_ctx,
            },
        )
        if isinstance(response, dict):
            msg = response.get("message") or {}
            return (msg.get("content") or "").strip()
        msg = getattr(response, "message", None)
        if msg is not None:
            content = getattr(msg, "content", None)
            if content:
                return str(content).strip()
        return ""

    def translate(
        self,
        source_text: str,
        glossary: Optional[Dict[str, str]] = None,
        log_fn: Optional[LogFn] = None,
        previous_context: str = "",
    ) -> str:
        log = log_fn or self.log
        effective_glossary = glossary or {}

        cleaned = re.sub(r"[\uE000-\uF8FF]", "", source_text or "").strip()
        if not cleaned:
            return ""

        log(cleaned)

        if effective_glossary and cleaned in effective_glossary:
            res = effective_glossary[cleaned]
            log(f"{res}\n")
            return res

        def process_chunk(text_segment: str, current_max_chars: int, current_context: str) -> Optional[str]:
            local_gloss = _slice_glossary_for_text(text_segment, effective_glossary)

            if len(text_segment) <= 20:
                system, user = self._build_short_prompt(text_segment, local_gloss, current_context)
            else:
                system, user = self._build_prompt(text_segment, local_gloss, current_context)

            for attempt in range(2):
                try:
                    raw_out = self._llm_generate(
                        system, user, temperature=(self.temperature + attempt * 0.1)
                    )
                    cleaned_out = clean_repetitions(post_clean_output(raw_out, source=text_segment))
                    if is_translation_valid(
                        cleaned_out,
                        text_segment,
                        is_cjk_target=self.meta["is_cjk_target"],
                        source_code=self.meta["source_code"],
                        target_code=self.meta["target_code"],
                        log_fn=log,
                    ):
                        return cleaned_out
                except Exception as exc:
                    log(f"LLM error: {exc}")

            if current_max_chars > 60:
                next_max = 200 if len(text_segment) > 200 else 60
                sub_segments = _segment_paragraph(text_segment, max_chars=next_max)

                if len(sub_segments) == 1 and len(sub_segments[0]) == len(text_segment):
                    return None

                results = []
                internal_context = current_context
                for sub in sub_segments:
                    res = process_chunk(sub, next_max, internal_context)
                    if res:
                        results.append(res)
                        internal_context = res[-150:]
                    else:
                        results.append(sub)
                return " ".join(results)

            return None

        initial_segments = _segment_paragraph(cleaned, max_chars=600)
        final_parts: list[str] = []
        rolling_internal_context = previous_context

        for seg in initial_segments:
            result = process_chunk(seg, 600, rolling_internal_context)
            if result:
                final_parts.append(result)
                rolling_internal_context = result[-200:]
            else:
                final_parts.append(seg)

        final_text = clean_repetitions(" ".join(final_parts).strip())
        log(f"{final_text}\n")
        return final_text
