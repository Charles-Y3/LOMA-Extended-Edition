# -*- coding: utf-8 -*-
"""Build folder trees for filesystem and indexed corpora."""
from __future__ import annotations

import os
from typing import Sequence

from extensions.document_intelligence.corpus.types import ChunkRecord, fully_excluded_file_keys
from extensions.document_intelligence.extract import VALID_EXTS


def build_filesystem_tree(roots: Sequence[str]) -> dict:
    """Nested dict from absolute folder roots (multiple top-level branches)."""
    tree: dict = {}
    for root in roots:
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            continue
        label = os.path.basename(root.rstrip("/\\")) or root
        node = tree.setdefault(label, {})
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            rel = os.path.relpath(dirpath, root).replace("\\", "/")
            sub = node
            if rel != ".":
                for part in rel.split("/"):
                    sub = sub.setdefault(part, {})
            files = sub.setdefault("__files__", [])
            for name in sorted(filenames):
                ext = os.path.splitext(name)[1].lower()
                if ext in VALID_EXTS:
                    if name not in files:
                        files.append(name)
    return tree


def _tree_parts(ingest_root: str, vault_path: str) -> list[str]:
    vp = vault_path.replace("\\", "/")
    label = os.path.basename(ingest_root.rstrip("/\\")) if ingest_root else ""
    parts = [p for p in vp.split("/") if p]
    if label and (not parts or parts[0].lower() != label.lower()):
        parts = [label] + parts
    elif label and parts and parts[0].lower() == label.lower():
        pass
    elif label:
        parts = [label] + parts
    return parts


def build_index_tree(
    chunks: Sequence[ChunkRecord],
    skipped: Sequence[tuple[str, str, str]] | None = None,
) -> dict:
    """Tree from chunk vault paths under ingest_root labels. `skipped` is an optional
    list of (ingest_root, vault_path, reason) for files that were scanned but produced
    no chunks (locked, encrypted, unsupported conversion, etc.) — they're recorded
    under each folder's "__skipped__" map instead of "__files__" so the UI can render
    them distinctly rather than making them vanish with no explanation."""
    # A file is only flagged "excluded" in the tree once EVERY one of its chunks is
    # low_content (the true whole-file cover-page case) — a file can also have a
    # handful of low_content chunks mixed in with normal ones (e.g. a stray short
    # header/footer fragment under the min-retrieval-token floor) without the rest of
    # its real content being excluded. Shared with LibraryCorpus/WorkspaceSession's
    # "(N excluded)" stats count so the two can't drift apart and disagree again.
    fully_excluded = fully_excluded_file_keys(
        chunks, lambda ch: "/".join(_tree_parts(ch.ingest_root, ch.vault_path))
    )

    tree: dict = {}
    for ch in chunks:
        parts = _tree_parts(ch.ingest_root, ch.vault_path)
        if not parts:
            continue
        node = tree
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                files = node.setdefault("__files__", [])
                if part not in files:
                    files.append(part)
                if "/".join(parts) in fully_excluded:
                    node.setdefault("__file_flags__", {})[part] = True
            else:
                node = node.setdefault(part, {})

    for ingest_root, vault_path, reason in skipped or []:
        parts = _tree_parts(ingest_root, vault_path)
        if not parts:
            continue
        node = tree
        for i, part in enumerate(parts):
            if i == len(parts) - 1:
                node.setdefault("__skipped__", {})[part] = reason
            else:
                node = node.setdefault(part, {})
    return tree


def collect_folder_paths(tree: dict, *, prefix: list[str]) -> list[str]:
    paths: list[str] = []
    for folder in sorted(
        k for k in tree if k not in ("__files__", "__skipped__", "__file_flags__")
    ):
        fpath = "/".join(prefix + [folder])
        paths.append(fpath)
        paths.extend(collect_folder_paths(tree[folder], prefix=prefix + [folder]))
    return paths
