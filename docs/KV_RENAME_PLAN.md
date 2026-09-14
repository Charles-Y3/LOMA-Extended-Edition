# Extended: bring document_editor + knowledge_vault up to Core (rename DI → KV)

**STATUS: COMPLETE** (Extended v1.2.0). Decision: **include** the translation-vault tab
(Extended keeps the Formslator backend services). 150 tests + verify_imports green;
pickle-compat shim, settings DI→KV remap, and i18n keys all verified.

Core's `document_editor` and `knowledge_vault` are canonical/most-current; Extended follows.
This is ledger §A (the deferred, high-risk rename) plus a `document_editor` refresh.

## Findings
- Core `knowledge_vault` is a **structural superset** of Extended `document_intelligence`:
  Extended's DI has **zero** unique files. Core-extra files are only the translation-vault
  ones (`tabs/translation_vault.py`, `translation_vault_store.py`, `corpus/backend_resolver.py`)
  + `translation.py` — these import `services.formslator`.
- Extended **kept the Formslator backend services** (`services/formslator/*`) even though the
  Formslator *extension* is removed — so those imports would resolve. But Extended's current
  DI does **not** surface a translation-vault tab.
- Pickled data risk: the lexical index (`index/lexical.py`) pickles `ChunkRecord` dataclass
  instances → the pickle embeds `extensions.document_intelligence.corpus.types.ChunkRecord`.
  After the rename, existing Extended users' `lexical.pkl` (and any pickled blob) fail to load
  → silent grounding failure (the exact incident in memory). Chunks are also in a SQLite DB
  (source of truth), but the fix must not force a re-index.

## Approach
1. **Compat shim** (do first): register `sys.modules['extensions.document_intelligence*']
   = extensions.knowledge_vault*` at KV package import, so old pickles unpickle against the
   renamed classes. Plus Core's existing data-dir migration (settings.py already moves
   AppData/document_intelligence → knowledge_vault).
2. **Replace** `extensions/document_intelligence/` with Core's `extensions/knowledge_vault/`
   (Core content, new name). Handle translation-vault per the decision below.
3. **External references** (~15 files): `pipeline/registry/catalog.py` (BUILTIN_EXTENSION_IDS),
   `config/extension_catalog.json`, `services/session/settings.py` (default-enabled set +
   wire the now-correct DI→KV settings migration via the persistence framework),
   `pipeline/i18n.py` + `pipeline/extension_i18n.py` (keys), `ui/components/chat_message.py`,
   `ui/layouts/main_layout.py`, `packaging/loma_core.spec`, `build.bat`, `requirements.txt`,
   docs. Merge i18n per-key (Extended has extra keys).
4. **Tests**: bring Core's `test_kv_*` over; remove Extended's `test_doc_intel_*`.
5. **document_editor**: port Core's updates (adds `insert_from_chat.py` + file diffs) —
   edition-neutral, lower risk; verify no Formslator/KV-name coupling.
6. **Verify**: `verify_imports` (extension registry must equal BUILTIN_EXTENSION_IDS with
   knowledge_vault), full suite, in-app.

## DECISION NEEDED — translation-vault tab in Extended's Knowledge Vault
Extended has the Formslator backend services but no Formslator extension. Core's KV shows a
translation-vault tab (Formslator translation-memory). Options:
- **A. Include it** — full Core parity; works since the services exist.
- **B. Exclude it** — Extended KV stays focused; drop the 3 files + the tab registration +
  translation.py's Formslator coupling (matches Extended DI's current behaviour, which has
  no such tab).
