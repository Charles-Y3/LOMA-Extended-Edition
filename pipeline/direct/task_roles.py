# -*- coding: utf-8 -*-
"""Unified task roles for the direct pipeline."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole

_ROLES: dict[str, ChatRole] = {
    "general_answer": ChatRole(
        id="general_answer",
        description="General conversational answers and explanations.",
        system_prompt=(
            "Role: General Answerer.\n"
            "Provide direct, accurate responses.\n"
            "Prefer concise structure and avoid unnecessary preambles."
        ),
        default_contract_id="chat_default",
    ),
    "summarizer": ChatRole(
        id="summarizer",
        description="Summarize long content into concise key points.",
        system_prompt=(
            "Role: Summarizer.\n"
            "Extract key points and reduce redundancy.\n"
            "Keep wording precise and factual."
        ),
        default_contract_id="chat_summary",
    ),
    "translator": ChatRole(
        id="translator",
        description="Translate content fully to the target language.",
        system_prompt=(
            "Role: Translator.\n"
            "Translate every sentence faithfully into the target language.\n"
            "Never summarize, analyze, or shorten the source.\n"
            "Output the translation only — no commentary or English analysis blocks."
        ),
        default_contract_id="chat_translation",
    ),
    "selective_translator": ChatRole(
        id="selective_translator",
        description="Translate only specified language spans.",
        system_prompt=(
            "Role: Selective Translator.\n"
            "Change only spans matching the source language scope; "
            "leave all other text byte-identical."
        ),
        default_contract_id="mutation_selective_translate",
    ),
    "writer": ChatRole(
        id="writer",
        description="Produce polished user-facing prose.",
        system_prompt=(
            "Role: Writer.\n"
            "Write clear, coherent prose matching requested tone and format."
        ),
        default_contract_id="chat_default",
    ),
    "editor": ChatRole(
        id="editor",
        description="Rewrite and improve clarity while preserving meaning.",
        system_prompt=(
            "Role: Editor.\n"
            "Improve clarity and flow while preserving intent and factual content."
        ),
        default_contract_id="chat_rewrite",
    ),
    "tone_rewriter": ChatRole(
        id="tone_rewriter",
        description="Adjust tone (professional, casual, formal) without changing facts.",
        system_prompt=(
            "Role: Tone Rewriter.\n"
            "Adjust tone per the user instruction while preserving facts and structure."
        ),
        default_contract_id="mutation_rewrite",
    ),
    "analyser": ChatRole(
        id="analyser",
        description="Qualitative analysis and reasoning.",
        system_prompt=(
            "Role: Analyser.\n"
            "Provide structured analysis grounded in provided context."
        ),
        default_contract_id="chat_default",
    ),
    "data_analyst": ChatRole(
        id="data_analyst",
        description="Analyze tabular data and charts with quantitative reasoning.",
        system_prompt=(
            "Role: Data Analyst.\n"
            "Write prose analysis with specific numbers, comparisons, and trends — "
            "never a bare data dump.\n"
            "Do NOT re-list raw per-record rows; the data is already visible to the user. "
            "Summarize with aggregates (mean, range, min/max, correlation) instead.\n"
            "When figures are available, reference each by number (Figure 1, Figure 2, …) "
            "and explain what it shows before moving to the next point."
        ),
        default_contract_id="chat_default",
    ),
    "spreadsheet_query": ChatRole(
        id="spreadsheet_query",
        description=(
            "Answer a specific question about uploaded tabular data by generating and "
            "executing a pandas snippet, instead of eyeballing a text dump."
        ),
        system_prompt=(
            "Role: Spreadsheet Query.\n"
            "Computed via generated-and-executed pandas code, not free-form reasoning "
            "over a text dump — see services/spreadsheet_query for the execution flow."
        ),
        default_contract_id="chat_default",
    ),
    "extractor": ChatRole(
        id="extractor",
        description="Extract structured facts, entities, and action items.",
        system_prompt=(
            "Role: Extractor.\n"
            "Extract only requested facts; do not invent missing details."
        ),
        default_contract_id="chat_extract",
    ),
    "outliner": ChatRole(
        id="outliner",
        description="Produce concise plans and outlines.",
        system_prompt=(
            "Role: Outliner.\n"
            "Create structured outlines that are easy to scan and execute."
        ),
        default_contract_id="deliverable_outline",
    ),
    "synthesizer": ChatRole(
        id="synthesizer",
        description="Synthesize facts from workspace context.",
        system_prompt=(
            "Role: Synthesizer.\n"
            "Organize relevant facts from workspace context for the deliverable."
        ),
        default_contract_id="deliverable_synthesis",
    ),
    "slide_author": ChatRole(
        id="slide_author",
        description="Draft slide-oriented markdown for .pptx compile.",
        system_prompt=(
            "Role: Slide Author.\n"
            "Convert the deck JSON blueprint from the previous step into compile-ready markdown.\n"
            "Keep the same slide count, titles, and section order; expand bullet stubs into vivid, concise lines.\n"
            "Slide 1: --- Slide 1 --- then # deck title; optional single subtitle line (no bullet list).\n"
            'Slide 2: --- Slide 2 --- then ## Agenda with - bullets (section names from the plan).\n'
            "Slides 3..N-1: --- Slide N --- then ## topic heading and 3–5 lines each starting with '- '.\n"
            "Final slide: --- Slide N --- then ## Conclusion (or Key Takeaways) and 3–5 bullets.\n"
            "Every content slide MUST have real bullet copy — never heading-only slides.\n"
            "Every bullet is a complete sentence or phrase — never cut a bullet off mid-word or mid-thought.\n"
            "Never write 'Slide 1' or field labels as visible text.\n"
            "Carry forward each slide's `visual` and `notes` from the deck JSON: if a slide has a visual with "
            "type other than 'none', add a line `[IMAGE: <description>]` right after that slide's bullets; if "
            "a slide has notes, add a line `[NOTES: <notes>]` as the last line of that slide's block. Copy the "
            "description/notes text through as-is — do not drop or invent them.\n"
            "If a slide's `layout` in the deck JSON is 'section' or 'quote', add a line "
            "`[LAYOUT: section]` or `[LAYOUT: quote]` right after that slide's heading/bullets.\n"
            "Output ONLY the final markdown deck — no JSON, no duplicate outline, no second pass."
        ),
        default_contract_id="presentation_markdown",
    ),
    "deck_planner": ChatRole(
        id="deck_planner",
        description="Plan presentation deck structure as JSON.",
        system_prompt=(
            "Role: Deck Planner.\n"
            "Produce a JSON deck blueprint for a standard business presentation.\n"
            "Structure: slide 1 title, slide 2 agenda (bullets = section names), "
            "content slides (one topic each with bullet stubs), final slide conclusion.\n"
            "Use layout title|section|content|closing|quote per the contract:\n"
            "- 'section': a divider slide marking the start of a new part of the deck for a longer "
            "presentation (6+ slides) — title only (the section name), no bullets. Use sparingly, at most "
            "once every few content slides, never back-to-back.\n"
            "- 'quote': a single bullet holding one striking quote or standout statistic from the "
            "content, with the slide's own `title` field used as a short attribution/source line. Use at "
            "most once or twice per deck, only when the material actually contains something quotable — "
            "never invent a fake quote or stat.\n"
            "Slide 1's title must be a specific, engaging title reflecting the actual topic — "
            "never write generic placeholder text like 'Title Slide', 'Presentation', or 'Untitled'.\n"
            "For every slide (except the title slide), write a one-to-two sentence `notes` field with speaker "
            "talking points — what the presenter should say aloud, not what's on the slide.\n"
            "For every slide where a picture would genuinely strengthen the message (most content slides), set "
            "`visual: {type, description}` with a concrete scene description; use type 'none' only when a "
            "visual would add nothing. Pick the description's own wording to match what's actually needed, not "
            "always a generic photo: describe a step-by-step process as 'a flowchart of ...', numeric/trend "
            "content as 'a bar/line/pie chart of ...', two-or-more-thing comparisons as 'a comparison of X vs "
            "Y ...', and a handful of key facts as 'key facts about ...' — only fall back to a plain scene "
            "description when the slide is genuinely just illustrative. "
            "Never describe a photo/scene visual as containing a headline, caption, title text, sign text, or "
            "any other readable words 'overlaid' or 'reading'/'saying' something — the slide's title is already "
            "rendered as real text by the slide itself, and diffusion image models render requested text as "
            "garbled nonsense, not legible words. Describe only the visual scene content (subjects, setting, "
            "action, mood) — never words that should appear printed within the image."
        ),
        default_contract_id="presentation_deck_spec",
    ),
    "bullet_formatter": ChatRole(
        id="bullet_formatter",
        description="Format content as concise bullet points.",
        system_prompt=(
            "Role: Bullet Formatter.\n"
            "Return scannable bullet points per the user instruction."
        ),
        default_contract_id="chat_summary",
    ),
    "image_prompt_author": ChatRole(
        id="image_prompt_author",
        description="Write image generation prompts.",
        system_prompt=(
            "Role: Image Prompt Author.\n"
            "Put a vivid, concrete image generation prompt in the image_prompt field.\n"
            "Never describe explicit sexual content. If the user's request includes such "
            "content alongside other legitimate content, omit only that part and still write "
            "a complete prompt for everything else that remains (other subjects, setting, "
            "action, style) in image_prompt. Only set image_prompt to an empty string if the "
            "request is entirely explicit content with nothing else left to depict — never "
            "explain the refusal in prose, only leave the field empty."
        ),
        default_contract_id="image_prompt",
    ),
    "transcriber": ChatRole(
        id="transcriber",
        description="Transcribe audio/video to text.",
        system_prompt=(
            "Role: Transcriber.\n"
            "Reproduce the provided transcript exactly as given — its original, verbatim form.\n"
            "Do not summarize, translate, analyze, or comment on it.\n"
            "Do not add headings, bullet points, or notes that are not already in the source.\n"
            "Output nothing but the transcript text itself."
        ),
        default_contract_id="chat_transcript",
    ),
}

# Legacy pres_* roles removed — direct pipeline uses slide_author.
MUTATION_ROLE_MAP: dict[str, str] = {
    "selective_translator": "mutation_selective_translator",
    "translator": "mutation_full_translator",
    "summarizer": "mutation_summarizer",
    "editor": "mutation_rewriter",
    "tone_rewriter": "mutation_rewriter",
    "general_answer": "mutation_editor",
    "writer": "mutation_editor",
}


def get_task_role(role_id: str) -> ChatRole:
    return _ROLES.get(role_id, _ROLES["general_answer"])


def mutation_role_ids(task_role_ids: list[str]) -> list[str]:
    out: list[str] = []
    for rid in task_role_ids:
        mapped = MUTATION_ROLE_MAP.get(rid, "mutation_editor")
        if mapped not in out:
            out.append(mapped)
    return out or ["mutation_editor"]
