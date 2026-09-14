# -*- coding: utf-8 -*-
"""Backward-compat import shim for the Document Intelligence → Knowledge Vault rename.

Extended shipped this extension as `document_intelligence` before the rename. Its
on-disk vault indexes pickle `ChunkRecord` (and other) dataclass instances, so the
pickles embed the OLD module path `extensions.document_intelligence.corpus.types...`.
After the rename those imports no longer exist, and `pickle.load` on an existing
user's `lexical.pkl` (or any pickled blob) would raise ModuleNotFoundError — a silent
grounding failure with no re-index prompt.

This installs a meta-path finder that transparently resolves any
`extensions.document_intelligence[.*]` import to the renamed
`extensions.knowledge_vault[.*]` module, so old pickles unpickle against the current
classes with zero re-indexing. Paired with the AppData directory migration in
settings.py (`_migrate_renamed_extension_root`), which moves the data folder itself.

Idempotent; install() is called from this package's __init__.
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from importlib.abc import Loader, MetaPathFinder

_OLD = "extensions.document_intelligence"
_NEW = "extensions.knowledge_vault"


class _AliasLoader(Loader):
    def __init__(self, new_name: str) -> None:
        self._new_name = new_name

    def create_module(self, spec):
        return importlib.import_module(self._new_name)

    def exec_module(self, module) -> None:  # already executed as the real module
        pass


class _DocIntelAliasFinder(MetaPathFinder):
    def find_spec(self, fullname: str, path=None, target=None):
        if fullname == _OLD or fullname.startswith(_OLD + "."):
            new_name = _NEW + fullname[len(_OLD):]
            return importlib.util.spec_from_loader(fullname, _AliasLoader(new_name))
        return None


def install() -> None:
    if not any(isinstance(f, _DocIntelAliasFinder) for f in sys.meta_path):
        sys.meta_path.insert(0, _DocIntelAliasFinder())
