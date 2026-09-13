"""Role definitions for document_generator and selection_revision."""
from __future__ import annotations

from pipeline.roles.chat_roles import ChatRole

_DOC_ROLES: dict[str, ChatRole] = {
    "doc_outliner": ChatRole(
        id="doc_outliner",
        description="Plan document structure and sections before drafting.",
        system_prompt=(
            "Role: Document Outliner.\n"
            "Create a clear section plan for the requested document.\n"
            "Focus on headings, flow, and coverage — not final prose."
        ),
        default_contract_id="document_outline",
    ),
    "doc_synthesizer": ChatRole(
        id="doc_synthesizer",
        description="Synthesize facts from attached workspace context for drafting.",
        system_prompt=(
            "Role: Document Synthesizer.\n"
            "Organize relevant facts from workspace context for the requested document.\n"
            "Stay grounded in provided sources."
        ),
        default_contract_id="document_synthesis",
    ),
    "doc_writer": ChatRole(
        id="doc_writer",
        description="Draft the full Markdown document body.",
        system_prompt=(
            "Role: Document Writer.\n"
            "Write the complete Markdown document body matching the user request.\n"
            "Use professional structure, clear headings, and coherent prose."
        ),
        default_contract_id="document_markdown",
    ),
    "excerpt_editor": ChatRole(
        id="excerpt_editor",
        description="Revise a highlighted excerpt for clarity, tone, or wording.",
        system_prompt=(
            "Role: Excerpt Editor.\n"
            "Revise only the provided excerpt according to the user instruction.\n"
            "Preserve factual content unless the instruction says otherwise."
        ),
        default_contract_id="excerpt_revision",
    ),
    "excerpt_translator": ChatRole(
        id="excerpt_translator",
        description="Translate a highlighted excerpt faithfully.",
        system_prompt=(
            "Role: Excerpt Translator.\n"
            "Translate the excerpt while preserving structure and meaning.\n"
            "Output the translation only."
        ),
        default_contract_id="excerpt_translation",
    ),
    "excerpt_shortener": ChatRole(
        id="excerpt_shortener",
        description="Shorten a highlighted excerpt while keeping key points.",
        system_prompt=(
            "Role: Excerpt Shortener.\n"
            "Make the excerpt more concise without losing essential meaning.\n"
            "Output the shortened text only."
        ),
        default_contract_id="excerpt_shorten",
    ),
}


def classify_document_generator_roles(
    user_query: str,
    *,
    has_context: bool = False,
    has_charts: bool = False,
) -> list[str]:
    """Role chain for greenfield document generation."""
    q = (user_query or "").lower()
    roles: list[str] = []
    if has_context:
        roles.append("doc_synthesizer")
    if has_charts or any(w in q for w in ("outline", "plan", "structure", "sections")):
        roles.append("doc_outliner")
    roles.append("doc_writer")
    return roles


def classify_selection_revision_roles(user_query: str) -> list[str]:
    """Role chain for Preview selection revision."""
    q = (user_query or "").lower()
    if any(w in q for w in ("translate", "translation", "localize", "localise")):
        return ["excerpt_translator"]
    if any(w in q for w in ("shorten", "shorter", "concise", "brief", "trim")):
        return ["excerpt_shortener"]
    return ["excerpt_editor"]


def get_document_role(role_id: str) -> ChatRole:
    return _DOC_ROLES.get(role_id, _DOC_ROLES["doc_writer"])


def get_selection_role(role_id: str) -> ChatRole:
    return _DOC_ROLES.get(role_id, _DOC_ROLES["excerpt_editor"])
