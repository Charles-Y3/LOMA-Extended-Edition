# -*- coding: utf-8 -*-
"""Deliverable phase contracts for agentic worker steps."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliverableContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, DeliverableContract] = {
    "research_notes": DeliverableContract(
        id="research_notes",
        description="Research and extraction notes (not the final deliverable).",
        output_rules=(
            "Return concise bullet points grouped by theme (max ~25 bullets total).",
            "Use only facts from provided context.",
            "Do not produce slide markdown, --- Slide N --- markers, or JSON deck specs.",
            "End with a short 'Slide themes' list: one line per slide (title – 5–12 word gist).",
            "Do not write essays or long paragraphs.",
        ),
        max_chars=6000,
    ),
    "document_outline": DeliverableContract(
        id="document_outline",
        description="Structured document outline.",
        output_rules=(
            "Use numbered sections and sub-bullets.",
            "Keep items concise; no full prose body.",
        ),
        max_chars=7000,
    ),
    "document_markdown": DeliverableContract(
        id="document_markdown",
        description="Markdown body for .docx compilation — from-scratch authoring only.",
        output_rules=(
            "Output ONLY the Markdown document body.",
            "Use ## headings and bullet lists where helpful.",
            "Do not wrap output in markdown fences.",
            "Write the document body directly — never mention file formats, saving, or exporting.",
            "Do not produce slide markdown, 'Slide N:' headings, or '--- Slide N ---' markers — "
            "this compiles to a Word document, not a deck.",
            "If asked for a cover page, contents page, etc., use a single # title heading for the "
            "cover and a plain bullet list of section names for the contents page — as ordinary "
            "sections in the same continuous document, never as separate slides.",
        ),
        max_chars=24000,
    ),
    # Data-analysis substance (numbers, tables, dataset overview) lives ONLY here — used
    # whenever role=data_analyst, regardless of chat vs document delivery. Keeping it out of
    # document_markdown means a translator/summarizer/writer step feeding document delivery
    # never sees these rules and so never hallucinates statistics for a non-tabular source.
    "content_analysis": DeliverableContract(
        id="content_analysis",
        description="Data analysis substance — numbers, tables, dataset facts.",
        output_rules=(
            "Write prose analysis grounded in the actual data — specific numbers, "
            "comparisons, and trends, never a bare data dump.",
            "Do not re-list raw per-record rows; summarize with aggregates (mean, range, "
            "min/max, correlation) instead.",
            "When figures/charts are available, reference each by number (Figure 1, Figure 2, "
            "…) and explain what it shows before moving to the next point.",
            "If the prior analysis contains a markdown table (e.g. a 'Category breakdowns' or "
            "'Citable examples' table), copy it into the report verbatim — do not restate, "
            "reformat, or recompute its numbers as prose, and do not merge rows across tables.",
            "If citing a specific record/row by name or ID, only use one already named in the "
            "prior analysis — never invent one or state numbers for it from memory.",
            "A '## Dataset Overview' section (row count, date range, top correlation, outlier "
            "counts) is inserted automatically by the compiler when this feeds a document — do "
            "not write your own version of these facts anywhere else (e.g. in the Executive "
            "Summary); reference 'see Dataset Overview' instead of restating the numbers.",
        ),
        max_chars=24000,
    ),
    "presentation_outline": DeliverableContract(
        id="presentation_outline",
        description="Legacy slide outline (prefer presentation_deck_spec).",
        output_rules=(
            "List each slide as ## Slide title with 2-4 bullet ideas.",
            "Do not use --- Slide N --- markers yet.",
        ),
        max_chars=7000,
    ),
    "presentation_deck_spec": DeliverableContract(
        id="presentation_deck_spec",
        description="JSON deck blueprint with per-slide layout, design mood, visuals, and notes.",
        output_rules=(
            "Output ONLY valid JSON (no markdown fences).",
            'Top-level keys: deck_title (string), design (object), slides (array).',
            'design may include: mood (warm|professional|nature|creative|minimal|bold|calm), '
            "palette (warm_coral|ocean_teal|forest_green|royal_purple|slate_modern|sunset_amber|rose_blush|midnight_blue).",
            "slides[] items: index (int), layout (title|content|section|closing), title, optional subtitle, bullets[], "
            "notes (string), visual (object).",
            "Slide 1: layout title, deck title, optional subtitle (max 120 chars), bullets must be empty.",
            'Slide 2: layout section, title "Agenda", bullets[] = 3–6 short section names (one per content slide).',
            "Slides 3..N-1: layout content, title + 3–5 bullets (max 140 chars each, full sentences — never cut off mid-thought).",
            'Final slide: layout closing, title "Conclusion" or "Key Takeaways", 3–5 bullets.',
            "Minimum 4 slides (title + agenda + one content + conclusion).",
            "Match the user's requested slide count when specified.",
            "notes: one to two sentences of speaker talking points for that slide, expanding on what to say aloud — "
            "this is NOT shown on the slide itself. Every slide except the title slide should have notes.",
            'visual: {"type": "photo|illustration|diagram|chart|icon|none", "description": "..."}. '
            "Set type to a concrete visual whenever a picture would genuinely help that slide's message "
            "(most content slides should have one); use \"none\" only when a visual adds nothing. "
            "description is a short, concrete scene description suitable for an image generator — required "
            "whenever type is not \"none\".",
            "Do not include --- Slide N --- markers or prose paragraphs.",
        ),
        max_chars=12000,
    ),
    "presentation_slide_copy": DeliverableContract(
        id="presentation_slide_copy",
        description="Refine slide wording from deck spec (not final markdown).",
        output_rules=(
            "Output ONLY valid JSON with key slides: array of {index, title, subtitle?, bullets[]}.",
            "Keep the same slide count and layout rules as the deck spec.",
            "Short, speakable lines; no paragraphs; no --- Slide N --- markers.",
            "Do not change design/palette unless user asked.",
        ),
        max_chars=12000,
    ),
    "presentation_markdown": DeliverableContract(
        id="presentation_markdown",
        description="Final slide markdown for agentic .pptx compilation.",
        output_rules=(
            "Output ONLY compile-ready slide markdown.",
            "First line may be: <!-- loma-theme: palette=...; mood=... --> (optional).",
            "Each slide MUST start with --- Slide N --- on its own line.",
            "Slide 1: # deck title only; optional single subtitle line (not a bullet list).",
            'Slide 2: ## Agenda (or Outline) then 3–6 "- " bullets.',
            "Slides 3 through second-to-last: ## topic title then 3–5 bullets.",
            "Final slide: ## Conclusion (or Key Takeaways) then 3–5 bullets.",
            "No code fences, no Title:/Content: script format, no export instructions.",
            "Creative but bounded: strong titles, concise bullets, modern tone.",
            "Every bullet MUST be a complete sentence or phrase — never cut off mid-word or mid-thought.",
            "If the input (deck JSON or prior markdown) includes a `[IMAGE: ...]` and/or `[NOTES: ...]` line for a "
            "slide, carry BOTH through unchanged for that same slide: place `[IMAGE: ...]` right after the "
            "slide's bullets, and `[NOTES: ...]` as the last line of that slide's block, before the next "
            "--- Slide N --- marker. Never drop, summarize, or invent these — copy them verbatim.",
        ),
        max_chars=24000,
    ),
    "image_prompt": DeliverableContract(
        id="image_prompt",
        description="Stable Diffusion prompt for image generation.",
        output_rules=(
            "Return ONLY a vivid image generation prompt.",
            "No markdown fences, labels, or explanation.",
            "One paragraph or comma-separated descriptors.",
        ),
        max_chars=2000,
    ),
    "generic_stub": DeliverableContract(
        id="generic_stub",
        description="Generic deliverable stub for unsupported types.",
        output_rules=(
            "Return the requested content directly.",
            "No markdown fences unless the user asked for code.",
        ),
        max_chars=16000,
    ),
}


def get_deliverable_contract(contract_id: str) -> DeliverableContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["generic_stub"])


def build_contract_text(contract: DeliverableContract) -> str:
    lines = [contract.description, "Rules:"]
    lines.extend(f"- {rule}" for rule in contract.output_rules)
    if contract.max_chars:
        lines.append(f"- Maximum length: {contract.max_chars} characters.")
    return "\n".join(lines)
