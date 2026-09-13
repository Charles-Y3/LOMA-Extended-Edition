"""Contracts for document_generator and selection_revision outputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentContract:
    id: str
    description: str
    output_rules: tuple[str, ...]
    max_chars: int | None = None


_CONTRACTS: dict[str, DocumentContract] = {
    "document_outline": DocumentContract(
        id="document_outline",
        description="Structured document outline before full drafting.",
        output_rules=(
            "Use a structured outline with short numbered sections and sub-bullets.",
            "Keep each item concise and actionable.",
            "Do not write full prose paragraphs or complete document body text.",
        ),
        max_chars=7000,
    ),
    "document_outline_charts": DocumentContract(
        id="document_outline_charts",
        description="Outline for a data report with ordered figures.",
        output_rules=(
            "Mirror the Report layout section from workspace context (Executive summary, "
            "each Figure N in order, Conclusions).",
            "Under each figure item, note what analysis themes to cover (no image paths).",
            "Do not write full prose or the final document body.",
        ),
        max_chars=7000,
    ),
    "document_markdown": DocumentContract(
        id="document_markdown",
        description="Markdown body for Word document compilation.",
        output_rules=(
            "Output ONLY the Markdown document body.",
            "Use clear section headings (##) and bullet lists where helpful.",
            "Write the document body directly — never mention file formats, saving, or exporting.",
            "Do not wrap output in markdown fences or add meta commentary.",
            "Respect profile tone and glossary when provided in the system instruction.",
            "Do not produce slide markdown, 'Slide N:' headings, or '--- Slide N ---' markers — "
            "this compiles to a Word document, not a deck.",
            "If asked for a cover page, contents page, etc., use a single # title heading for the "
            "cover and a plain bullet list of section names for the contents page — as ordinary "
            "sections in the same continuous document, never as separate slides.",
            "If the prior analysis contains a markdown table (e.g. a 'Category breakdowns' or "
            "'Citable examples' table), copy it into the report verbatim — do not restate, "
            "reformat, or recompute its numbers as prose, and do not merge rows across tables.",
            "If citing a specific record/row by name or ID, only use one already named in the "
            "prior analysis — never invent one or state numbers for it from memory.",
            "A '## Dataset Overview' section (row count, date range, top correlation, outlier "
            "counts) is inserted into the report automatically by the compiler — do not write "
            "your own version of these facts anywhere else in the report (e.g. in the Executive "
            "Summary); reference 'see Dataset Overview' instead of restating the numbers.",
        ),
        max_chars=24000,
    ),
    "document_markdown_charts": DocumentContract(
        id="document_markdown_charts",
        description="Markdown report with ordered embedded charts from graph_generation.",
        output_rules=(
            "Output ONLY the Markdown document body.",
            "Follow the Report layout section in workspace context exactly: same figure order and headings.",
            "For each figure section: use `## Figure N: title`, then the provided `![Figure N: ...](path)` line "
            "on its own line (copy paths verbatim), then 2–4 paragraphs analysing that chart only.",
            "Do not skip, reorder, or invent figures. Do not replace image paths with descriptions.",
            "Executive summary and Conclusions sections must not contain images.",
            "Do not wrap output in markdown fences or add meta commentary.",
        ),
        max_chars=24000,
    ),
    "document_synthesis": DocumentContract(
        id="document_synthesis",
        description="Source synthesis notes for document drafting.",
        output_rules=(
            "Extract and organize facts from provided workspace context only.",
            "Use concise bullet points grouped by theme or section.",
            "Do not invent facts not present in the context.",
            "Do not produce the final document body yet.",
        ),
        max_chars=12000,
    ),
    "excerpt_revision": DocumentContract(
        id="excerpt_revision",
        description="Revised document excerpt for in-place Preview replacement.",
        output_rules=(
            "Return ONLY the revised excerpt text.",
            "Preserve meaning unless the user instruction explicitly changes it.",
            "Do not include markdown fences, labels, or explanation.",
            "Match the formatting style of the original excerpt (lists, headings, tone).",
        ),
        max_chars=9000,
    ),
    "excerpt_translation": DocumentContract(
        id="excerpt_translation",
        description="Translated document excerpt.",
        output_rules=(
            "Return ONLY the translated excerpt.",
            "Preserve list and paragraph structure from the original.",
            "Do not add translation notes or meta commentary.",
        ),
        max_chars=9000,
    ),
    "excerpt_shorten": DocumentContract(
        id="excerpt_shorten",
        description="Shortened document excerpt.",
        output_rules=(
            "Return ONLY the shortened excerpt.",
            "Reduce length while keeping essential meaning.",
            "Do not add commentary or labels.",
        ),
        max_chars=6000,
    ),
}


def get_document_contract(contract_id: str) -> DocumentContract:
    return _CONTRACTS.get(contract_id, _CONTRACTS["document_markdown"])


def get_document_contract_for_role(role_id: str) -> DocumentContract:
    from services.session import state

    has_charts = bool(getattr(state, "chart_artifacts", None))
    role_to_contract = {
        "doc_outliner": "document_outline_charts" if has_charts else "document_outline",
        "doc_writer": "document_markdown_charts" if has_charts else "document_markdown",
        "doc_synthesizer": "document_synthesis",
        "excerpt_editor": "excerpt_revision",
        "excerpt_translator": "excerpt_translation",
        "excerpt_shortener": "excerpt_shorten",
    }
    return get_document_contract(role_to_contract.get(role_id, "document_markdown"))
