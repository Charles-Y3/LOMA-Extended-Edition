# Extended Edition — spine port plan

**STATUS: COMPLETE** (Extended v1.1.0). Spine + edition-neutral locale fixes ported and
verified (150 tests + `verify_imports` green). Deferred, not part of this port: the
`document_intelligence`→`knowledge_vault` rename (ledger §A) and the Extended settings.py
migration wiring that rides with it; the resize-stall-timeout cross-edition follow-up.

Porting the shared spine + edition-neutral Core fixes (Sept 13–14) into Extended.
Core is canonical (v1.1.0). Extended starts at v1.0.0. See `SYNC_LEDGER.md` §F2.

Extended has its own venv (`venv/Scripts/python.exe`) — every step is verified with it.
Line-ending (CRLF/LF) noise made earlier diffs look huge; true divergence is small.

## Scope

Two bodies of Core work land here:
- **A. The shared spine** (migrations, provider capabilities, Context Governor +
  `llm_bridge` wiring, grounding resolver, enforcement guard, template).
- **B. Edition-neutral Core fixes** from commit `17d2a6d` (locale-blind detection in
  highlight Q&A, edit-intent, context strategy) — genuine bug fixes Extended lacks.

Formslator is Core-only, so `74744dc` (Formslator Review merge) does **not** port,
except any shared i18n/KV bits evaluated individually.

## Steps (each: apply → `verify_imports` → targeted test → commit)

### Spine — wholesale copy (Extended base == Core pre-spine, so Core's current file drops in)
1. `services/persistence/` (new), `services/context_governor.py` (new) — copy from Core.
2. `services/providers/base.py`, `services/providers/lmstudio_provider.py`,
   `services/llm_bridge.py`, `scripts/verify_imports.py`, `services/SERVICE_TEMPLATE.md`
   — copy Core's current versions (identical base + spine).

### Spine — merge
3. `services/providers/ollama_provider.py` — take Core's version (capabilities +
   `loaded_context_length()` + governor-compatible). **Decision:** drop Extended's
   `_chat_with_resize_guard` / `_RESIZE_STALL_TIMEOUT` in favour of edition consistency;
   the governor + `llm_bridge` overflow-retry cover its purpose. The resize-stall
   timeout (guards an Ollama hang during a num_ctx reload) is logged as a **cross-edition
   follow-up** for Core first, then re-port — not kept as an Extended-only divergence.
4. `services/session/settings.py` — **left unchanged.** Core's settings migration is the
   `document_intelligence`→`knowledge_vault` rename, which is WRONG for Extended (it still
   uses the old `document_intelligence` id — that rename is the deferred ledger §A change).
   The migration framework module is present + tested; its first Extended consumer is the
   KV rename, so the settings.py wiring lands with that work, not here.
5. `services/grounding.py` (new) — copy Core's, then **add the web branch**: Extended is
   online, so `needs_source_grounding()` also returns True for a live-fact query with no
   attached sources (gated by the web-grounding setting), and `source_priority()` inserts
   `web` before `model_knowledge`, wired to `services/grounded_chat.py`.

### Edition-neutral fixes (commit 17d2a6d)
6. Copy Core's current `pipeline/context/strategy.py`, `services/session/preview_revision.py`,
   `services/session/prompt_memory.py` (Extended == Core pre-fix → clean).
7. Merge into Extended's own versions: `pipeline/direct/highlight_excerpt.py` (~19-line
   delta), `pipeline/query_intent_i18n.py` (add 17d2a6d's new locale concepts).

### Finalize
8. Run `verify_imports` (incl. the new spine-boundary check) on Extended; fix any
   Extended-only direct-backend imports or adjust the allowlist.
9. Full Extended test suite; bump `APP_VERSION`; update `SYNC_LEDGER.md` (mark ported).

## Not ported
- Formslator (Core-only). Extended's KV is still the old `document_intelligence` name —
  that rename is a **separate deferred change** (ledger §A), not part of this port.
