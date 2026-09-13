# -*- coding: utf-8 -*-

"""Shared UI labels for Document Intelligence."""

from __future__ import annotations



from pipeline.i18n import t as tr





def query_placeholder() -> str:

    return tr("di.query_placeholder")





def mode_options() -> dict[str, str]:

    return {

        "search": tr("di.mode_search"),

        "ask": tr("di.mode_ask"),

        "analyse": tr("di.mode_analyse"),

        "deep": tr("di.mode_deep"),

        "agentic": tr("di.mode_agentic"),

    }





def mode_label() -> str:

    return tr("di.mode_label")





def setting_tooltips() -> dict[str, str]:

    return {

        "answer_model": tr("di.settings.tooltip_answer_model"),

        "chunk_size_tokens": tr("di.settings.tooltip_chunk_size_tokens"),

        "min_chunk_tokens": tr("di.settings.tooltip_min_chunk_tokens"),

        "chunks_per_document": tr("di.settings.tooltip_chunks_per_document"),

        "max_sources": tr("di.settings.tooltip_max_sources"),

        "max_chunks_returned": tr("di.settings.tooltip_max_chunks_returned"),

        "score_cutoff": tr("di.settings.tooltip_score_cutoff"),

        "retrieval_depth": tr("di.settings.tooltip_retrieval_depth"),

        "min_retrieval_tokens": tr("di.settings.tooltip_min_retrieval_tokens"),

        "fast_score_gap_ratio": tr("di.settings.tooltip_fast_score_gap_ratio"),

        "max_agent_iterations": tr("di.settings.tooltip_max_agent_iterations"),

        "agent_confidence_threshold": tr("di.settings.tooltip_agent_confidence_threshold"),

        "results_display_count": tr("di.settings.tooltip_results_display_count"),

        "citation_required": tr("di.settings.tooltip_citation_required"),

        "document_passwords": tr("di.settings.tooltip_document_passwords"),

    }



# Back-compat aliases (prefer functions above in new code)

QUERY_PLACEHOLDER = query_placeholder()

MODE_OPTIONS = mode_options()

