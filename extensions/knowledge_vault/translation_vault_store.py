# -*- coding: utf-8 -*-
"""Translation Vault: a single persistent store of translation pairs, decoupled
from any one catalogue (see the Translation Vault tab, extensions/knowledge_vault/
tabs/translation_vault.py). A catalogue's re-index syncs its extracted pairs into
this one shared store; deleting a catalogue only strips the sources it
contributed, not the pair itself — a pair disappears only once it has no sources
left (see remove_library_from_vault). Session/workspace documents never reach
this store at all — no sync hook is wired up for the workspace, by design.
Session translations stay usable only inside Knowledge Vault's own tabs (see
translation.py's include_workspace flag and ui/components/viewer_selection.py,
which keep session data out of every other consumer)."""
from __future__ import annotations

import os
import pickle
import threading
import uuid
from dataclasses import dataclass, field

from extensions.knowledge_vault.translation import TranslationPair

_lock = threading.Lock()


@dataclass
class TranslationSource:
    library_id: str = ""
    file_path: str = ""


@dataclass
class VaultPair:
    source_text: str = ""
    target_text: str = ""
    sources: list[TranslationSource] = field(default_factory=list)
    pair_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    reviewed: bool = False


@dataclass
class VaultUpdateResult:
    success: bool
    old_source_text: str = ""
    old_target_text: str = ""


def _vault_path() -> str:
    from extensions.knowledge_vault.settings import data_root

    return os.path.join(data_root(), "translation_vault.pkl")


def _load() -> list[VaultPair]:
    path = _vault_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as f:
            return pickle.load(f)
    except Exception:
        return []


def _save(pairs: list[VaultPair]) -> None:
    with open(_vault_path(), "wb") as f:
        pickle.dump(pairs, f, protocol=pickle.HIGHEST_PROTOCOL)


def _dedup_key(source_text: str, target_text: str) -> frozenset[str]:
    """Same swap-tolerant identity as translation.py's _pair_dedup_key — a pair
    and its mirrored (source/target reversed) counterpart are the same pair."""
    return frozenset({source_text.strip(), target_text.strip()})


def library_display_name(library_id: str) -> str:
    try:
        from extensions.knowledge_vault.corpus.library import load_manifest

        return load_manifest(library_id).name or library_id
    except Exception:
        return library_id


def best_source(sources: list[TranslationSource]) -> TranslationSource | None:
    """Deterministic single "best match" for display/open-file actions. A pair
    can carry sources from several catalogues (the same term legitimately
    extracted from more than one document), but the UI only ever shows/opens
    one — sorted by catalogue display name then file path so the same pair
    always resolves to the same source, tie or not."""
    if not sources:
        return None
    return sorted(
        sources, key=lambda s: (library_display_name(s.library_id), s.file_path)
    )[0]


def list_pairs() -> list[VaultPair]:
    return _load()


def vault_translation_index():
    """Build a TranslationIndex (translation.py's live lookup/matching engine) over
    the Translation Vault's own pairs, converting each VaultPair's `sources` down
    to its single best-matched file_path. This is what the live "translate X"
    query shortcut (Knowledge Vault's own query box, Document Editor's highlight
    popup) should search — NOT any one catalogue's own per-catalogue index —
    so editing or deleting a pair in the Translation Vault tab is immediately
    reflected there. Built fresh on every call (the Vault is small — hundreds,
    not tens of thousands, of pairs — so this is cheap enough to skip caching)."""
    from extensions.knowledge_vault.translation import TranslationIndex, TranslationPair

    index = TranslationIndex()
    converted = []
    for vp in list_pairs():
        src = best_source(vp.sources)
        converted.append(
            TranslationPair(
                source_text=vp.source_text,
                target_text=vp.target_text,
                file_path=src.file_path if src else "",
                source_name=library_display_name(src.library_id) if src else "",
                pair_id=vp.pair_id,
                reviewed=vp.reviewed,
            )
        )
    index.build(converted)
    return index


def sync_library_into_vault(library_id: str, extracted_pairs: list[TranslationPair]) -> None:
    """Replace this catalogue's contribution to the vault with what it just
    extracted on re-index. Existing sources tagged with this library_id are
    dropped first (the catalogue's own files may have changed since the last
    scan), then each freshly-extracted pair is merged into an existing
    identical VaultPair (adding a source) or added as a brand new one. Because
    merging always happens by content key, at most one VaultPair ever exists
    per {source, target} pair — there's no separate "duplicate cluster" case to
    clean up here the way there was for the old per-catalogue stores."""
    with _lock:
        vault = _load()
        for p in vault:
            p.sources = [s for s in p.sources if s.library_id != library_id]
        vault = [p for p in vault if p.sources]

        by_key: dict[frozenset[str], VaultPair] = {
            _dedup_key(p.source_text, p.target_text): p for p in vault
        }
        for ep in extracted_pairs:
            key = _dedup_key(ep.source_text, ep.target_text)
            src = TranslationSource(library_id=library_id, file_path=ep.file_path)
            existing = by_key.get(key)
            if existing is not None:
                if not any(
                    s.library_id == src.library_id and s.file_path == src.file_path
                    for s in existing.sources
                ):
                    existing.sources.append(src)
            else:
                new_pair = VaultPair(
                    source_text=ep.source_text, target_text=ep.target_text, sources=[src]
                )
                vault.append(new_pair)
                by_key[key] = new_pair

        _save(vault)


def remove_library_from_vault(library_id: str) -> None:
    """Catalogue deleted — strip its sources from every pair; a pair left with
    zero sources disappears entirely. Pairs that also came from another
    catalogue survive with that other source intact."""
    with _lock:
        vault = _load()
        for p in vault:
            p.sources = [s for s in p.sources if s.library_id != library_id]
        vault = [p for p in vault if p.sources]
        _save(vault)


def update_vault_pair(pair_id: str, source_text: str, target_text: str) -> VaultUpdateResult:
    with _lock:
        vault = _load()
        for p in vault:
            if p.pair_id == pair_id:
                old_source, old_target = p.source_text, p.target_text
                p.source_text = (source_text or "").strip()
                p.target_text = (target_text or "").strip()
                _save(vault)
                return VaultUpdateResult(True, old_source, old_target)
        return VaultUpdateResult(False)


def mark_vault_pair_reviewed(pair_id: str) -> bool:
    with _lock:
        vault = _load()
        for p in vault:
            if p.pair_id == pair_id:
                p.reviewed = True
                _save(vault)
                return True
        return False


def delete_vault_pairs(pair_ids: set[str]) -> int:
    if not pair_ids:
        return 0
    with _lock:
        vault = _load()
        kept = [p for p in vault if p.pair_id not in pair_ids]
        removed = len(vault) - len(kept)
        if removed:
            _save(kept)
        return removed


def delete_all_vault_pairs() -> int:
    with _lock:
        vault = _load()
        n = len(vault)
        if n:
            _save([])
        return n
