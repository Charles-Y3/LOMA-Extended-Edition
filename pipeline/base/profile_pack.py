# core/profile_pack.py
# -*- coding: utf-8 -*-
"""Builds LLM system instructions from an active profile."""
from __future__ import annotations

import json
import re

from pipeline.instruction_priority import apply_instruction_priority
from pipeline.i18n import get_locale
from pipeline.schemas.task_schema import RoutingDecision


# Profile-manager compatibility shim: LOMA has no per-user profile switching,
# so every profile lookup resolves to the single hardcoded default_profile().
PROFILE_NONE = "none"


def is_no_profile(profile_name: str | None = None) -> bool:
    key = (profile_name or "").strip().lower()
    return not key or key == PROFILE_NONE


def resolve_active_profile_id(profile_name: str | None = None) -> str:
    return PROFILE_NONE


def load_profile(profile_name: str | None = None) -> dict | None:
    return None


def profile_select_options() -> dict[str, str]:
    from pipeline.i18n import t as tr

    return {PROFILE_NONE: tr("extension.none_label")}


def default_profile(profile_id: str) -> dict:
    return {
        "PROFILE": {"id": profile_id},
        "BEHAVIOR": {"reasoning_mode": "balanced", "planning_style": "stepwise"},
        "MODEL": {
            "temperature": 0.3,
            "max_tokens": 2048,
            "num_ctx": 4096,
            "enable_thinking": False,
            "keep_alive": "30m",
        },
        "USER": {"preferences": {"tone": "balanced", "verbosity": "medium", "structure_style": "clear"}},
        "OUTPUT": {"format": "markdown", "style_rules": ["maintain clarity"]},
        "RULES": {
            "must_follow": ["follow instructions"],
            "must_avoid": ["hallucinating information"],
        },
    }


def _user_wants_images(user_query: str) -> bool:
    from pipeline.query_intent_i18n import matches

    return matches(user_query, "wants_images")


def _image_marker_instruction(user_query: str, output_type: str) -> str:
    """Tell the LLM how many [IMAGE_PROMPT:] markers to emit."""
    from pipeline.query_intent_i18n import matches

    per_unit = matches(user_query, "image_per_unit")
    base = (
        "\nIMAGE SUPPORT (user requested visuals):\n"
        "Insert concise image markers exactly like: [IMAGE_PROMPT: visual description].\n"
        "Phrase the marker's own description to match what the visual should actually be: "
        "for a process/workflow being described, phrase it as a flowchart (e.g. "
        "\"flowchart of: step 1, step 2, step 3\"); for a list of key statistics, phrase it "
        "as key facts (e.g. \"key facts about X\"); for chronological milestones, phrase it "
        "as a timeline (e.g. \"timeline of X\"); otherwise describe a plain scene/photo. "
        "This single marker format covers all of these — do not invent new marker syntax.\n"
    )
    if output_type == "presentation":
        if per_unit:
            return (
                base
                + "Include one image marker per slide or section where the user asked for a visual.\n"
            )
        return base + "Include an image marker on every slide that should have a visual.\n"
    if per_unit:
        return (
            base
            + "Include one image marker in each chapter or section where the user asked for an image. "
            "Do not collapse multiple requested visuals into a single marker.\n"
        )
    return base + "Include an image marker wherever the user requested a visual.\n"


def build_system_instruction(
    profile: dict,
    profile_id: str,
    classification,
    user_query: str = "",
) -> str:
    user_prefs = profile.get("USER", {}).get("preferences", {})
    output_rules = profile.get("OUTPUT", {})
    profile_rules = profile.get("RULES", {})
    intent = profile.get("INTENT", {})

    system_instruction = (
        f"You are LOMA processing under profile: {profile_id}.\n"
        f"Primary goal: {intent.get('primary_goal', 'Assist the user accurately.')}\n"
        f"Task scope: {intent.get('task_scope', 'Use provided context.')}\n"
        f"Tone: {user_prefs.get('tone', 'balanced')} | "
        f"Verbosity: {user_prefs.get('verbosity', 'medium')}\n"
        f"Structure: {user_prefs.get('structure_style', 'clear')}\n"
        f"Target format: {output_rules.get('format', 'markdown')}\n"
        f"Expected output medium: {getattr(classification, 'output_type', 'chat')}\n\n"
        f"Must execute: {', '.join(profile_rules.get('must_follow', ['follow guidelines']))}\n"
        f"Must avoid: {', '.join(profile_rules.get('must_avoid', ['hallucinating content']))}\n"
        f"Layout: {', '.join(output_rules.get('style_rules', ['maintain clarity']))}\n"
    )

    output_type = getattr(classification, "output_type", "chat")
    mode = getattr(classification, "mode", None)
    if output_type in ("document", "presentation"):
        if mode == "mutation":
            system_instruction += (
                "\nCRITICAL — LAYOUT PRESERVATION:\n"
                "Context is structured Markdown. Preserve every Markdown element exactly.\n"
                "When replacing or translating text, keep identical structural boundaries.\n"
            )
            if output_type == "presentation":
                system_instruction += "Prefix every slide block with EXACTLY: --- Slide X ---\n"
        else:
            system_instruction += (
                "\nCRITICAL — NEW DOCUMENT:\n"
                "Create content from scratch with required Markdown structure.\n"
                "- Documents: headings (#, ##), lists, paragraphs, tables.\n"
                "- Use **bold** and *italic* for emphasis where appropriate.\n"
                "- Presentations: # title, then --- Slide X --- sections with bullets.\n"
                "\nDELIVERY:\n"
                "Write the document body directly and completely, using the Markdown structure "
                "above. Do not mention file formats, saving, or exporting anywhere in your response.\n"
            )
            if classification.output_type == "presentation":
                slide_match = re.search(r"\b(\d+)\s*slides?\b", user_query or "", re.IGNORECASE)
                if slide_match:
                    n = int(slide_match.group(1))
                    system_instruction += (
                        f"\nCreate exactly {n} content slides after the title "
                        f"(use --- Slide 1 --- through --- Slide {n} ---).\n"
                    )
            if _user_wants_images(user_query):
                system_instruction += _image_marker_instruction(
                    user_query, classification.output_type
                )
            else:
                system_instruction += (
                    "\nDo not insert image markers or request illustrations unless the user explicitly asked for visuals.\n"
                )

    if output_type == "image":
        system_instruction += (
            "\nCRITICAL — IMAGE OUTPUT:\n"
            "Return only a concise, vivid Stable Diffusion prompt for the requested image. "
            "Do not include markdown fences, explanations, file paths, or instructions.\n"
        )

    glossary = user_prefs.get("glossary_terms", {})
    if glossary:
        system_instruction += f"\nGlossary:\n{json.dumps(glossary)}\n"

    settings_locale = get_locale()
    return apply_instruction_priority(
        system_instruction,
        user_query=user_query,
        profile=profile,
        settings_locale=settings_locale,
    )


def build_mutation_hint(profile: dict) -> str:
    """Compact instruction block for template mutation planning."""
    intent = profile.get("INTENT", {})
    output_rules = profile.get("OUTPUT", {})
    lines = []
    if intent.get("task_scope"):
        lines.append(f"Task scope: {intent['task_scope']}")
    if intent.get("primary_goal"):
        lines.append(f"Goal: {intent['primary_goal']}")
    style_rules = output_rules.get("style_rules") or []
    if style_rules:
        lines.append("Layout/style: " + "; ".join(str(r) for r in style_rules))
    return "\n".join(lines)
