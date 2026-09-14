# -*- coding: utf-8 -*-

"""Shared UI labels for Knowledge Vault."""

from __future__ import annotations



from pipeline.i18n import t as tr





def query_placeholder() -> str:

    return tr("knowledge_vault.query_placeholder")





def mode_options() -> dict[str, str]:

    return {

        "search": tr("knowledge_vault.mode_search"),

        "ask": tr("knowledge_vault.mode_ask"),

        "analyse": tr("knowledge_vault.mode_analyse"),

        "deep": tr("knowledge_vault.mode_deep"),

        "agentic": tr("knowledge_vault.mode_agentic"),

    }





def mode_label() -> str:

    return tr("knowledge_vault.mode_label")





def setting_tooltips() -> dict[str, str]:

    return {

        "answer_model": tr("knowledge_vault.settings.tooltip_answer_model"),

        "chunk_size_tokens": tr("knowledge_vault.settings.tooltip_chunk_size_tokens"),

        "min_chunk_tokens": tr("knowledge_vault.settings.tooltip_min_chunk_tokens"),

        "chunks_per_document": tr("knowledge_vault.settings.tooltip_chunks_per_document"),

        "max_sources": tr("knowledge_vault.settings.tooltip_max_sources"),

        "max_chunks_returned": tr("knowledge_vault.settings.tooltip_max_chunks_returned"),

        "score_cutoff": tr("knowledge_vault.settings.tooltip_score_cutoff"),

        "retrieval_depth": tr("knowledge_vault.settings.tooltip_retrieval_depth"),

        "min_retrieval_tokens": tr("knowledge_vault.settings.tooltip_min_retrieval_tokens"),

        "fast_score_gap_ratio": tr("knowledge_vault.settings.tooltip_fast_score_gap_ratio"),

        "max_agent_iterations": tr("knowledge_vault.settings.tooltip_max_agent_iterations"),

        "agent_confidence_threshold": tr("knowledge_vault.settings.tooltip_agent_confidence_threshold"),

        "results_display_count": tr("knowledge_vault.settings.tooltip_results_display_count"),

        "citation_required": tr("knowledge_vault.settings.tooltip_citation_required"),

        "document_passwords": tr("knowledge_vault.settings.tooltip_document_passwords"),

    }



# Back-compat aliases (prefer functions above in new code)

QUERY_PLACEHOLDER = query_placeholder()

MODE_OPTIONS = mode_options()

