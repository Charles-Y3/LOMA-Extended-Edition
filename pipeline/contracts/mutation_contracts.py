# -*- coding: utf-8 -*-
"""Contracts for mutation capabilities (fragment JSON planning)."""

from __future__ import annotations



from dataclasses import dataclass





@dataclass(frozen=True)

class MutationContract:

    id: str

    description: str

    output_rules: tuple[str, ...]

    max_chars: int | None = None





_CONTRACTS: dict[str, MutationContract] = {

    "mutation_fragment_map": MutationContract(

        id="mutation_fragment_map",

        description="JSON map of fragment id → replacement plain text.",

        output_rules=(

            "Return ONLY a JSON object: keys are exact fragment ids; values are replacement strings.",

            "Use plain text only — no markdown, bold, or underline markup.",

            "Preserve emphasis and style notes from fragment metadata when provided.",

            "If a fragment needs no change, return the original text unchanged for that id.",

        ),

        max_chars=32000,

    ),

    "mutation_selective_translate": MutationContract(

        id="mutation_selective_translate",

        description="Translate only specified languages; leave other text unchanged.",

        output_rules=(

            "Return ONLY a JSON object: keys are exact fragment ids; values are replacement strings.",

            "Translate ONLY words/phrases in the source language scope described in the user instruction.",

            "Use the target language named in the user instruction (do not assume English).",

            "Do NOT translate text that is already in the target language.",

            "Do NOT translate text in other languages unless explicitly requested.",

            "Within a fragment, change only the spans that match the instruction; keep all other characters byte-identical.",

            "If a fragment has no matching language, return the original text unchanged for that id.",

            "Plain text only — no markdown or markup.",

        ),

        max_chars=32000,

    ),

    "mutation_full_translate": MutationContract(

        id="mutation_full_translate",

        description="Translate entire fragments to the target language.",

        output_rules=(

            "Return ONLY a JSON object: keys are exact fragment ids; values are fully translated strings.",

            "Translate the full text of each fragment to the target language in the instruction.",

            "Never summarize, condense, or omit sentences — literal full translation only.",

            "Preserve numbers, proper nouns, and formatting intent unless the user says otherwise.",

            "Plain text only.",

        ),

        max_chars=32000,

    ),

    "mutation_summarize": MutationContract(

        id="mutation_summarize",

        description="Summarize fragment text per instruction.",

        output_rules=(

            "Return ONLY a JSON object: keys are exact fragment ids; values are summarized text.",

            "Follow requested format (bullets, length, language).",

            "If a fragment should not be summarized, return it unchanged.",

        ),

        max_chars=32000,

    ),

    "mutation_rewrite": MutationContract(

        id="mutation_rewrite",

        description="Rewrite tone or clarity without changing facts.",

        output_rules=(

            "Return ONLY a JSON object: keys are exact fragment ids; values are rewritten plain text.",

            "Preserve factual content; adjust tone/clarity per instruction.",

        ),

        max_chars=32000,

    ),

}





def get_mutation_contract(contract_id: str) -> MutationContract:

    return _CONTRACTS.get(contract_id, _CONTRACTS["mutation_fragment_map"])





def get_mutation_contract_for_role(role_id: str) -> MutationContract:

    mapping = {

        "mutation_selective_translator": "mutation_selective_translate",

        "mutation_full_translator": "mutation_full_translate",

        "mutation_translator": "mutation_full_translate",

        "mutation_summarizer": "mutation_summarize",

        "mutation_rewriter": "mutation_rewrite",

        "mutation_editor": "mutation_fragment_map",

    }

    return get_mutation_contract(mapping.get(role_id, "mutation_fragment_map"))

