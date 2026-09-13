# -*- coding: utf-8 -*-
"""Shared context ingest — digests, strategy, assembly."""
from pipeline.context.assemble import AssembledContext, assemble_workspace_context, full_text_for_map_reduce
from pipeline.context.digest_store import build_source_digests, digest_index_markdown
from pipeline.context.types import ContextStrategy, SourceDigest

__all__ = [
    "AssembledContext",
    "ContextStrategy",
    "SourceDigest",
    "assemble_workspace_context",
    "build_source_digests",
    "digest_index_markdown",
    "full_text_for_map_reduce",
]
