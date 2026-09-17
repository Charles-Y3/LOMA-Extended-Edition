# LOMA Core ⟷ Extended — sync ledger

Two editions are kept as **separate copies** (different audiences). This ledger records
how every divergence is classified so nothing is mixed up or left out. Kept **identical**
in both repos. See `docs/PIPELINE_REFACTOR.md` for the refactor this supports.

Buckets:
- **EDITION-CORE** — Core-only, by design (stays out of Extended).
- **EDITION-EXT** — Extended-only, by design (stays out of Core).
- **C→E** — Core is canonical; port to Extended.
- **E→C** — Extended has a real improvement Core should get.
- **RECONCILE** — both changed / feeds the refactor; decide deliberately, don't blind-copy.
- **NOISE** — build/log/data artifacts; ignore, don't sync.

Baseline: Extended git initialised at commit `1bf6098` (2026-09-13), 576 files.
Measured divergence: 112 shared files differ + ~60 files unique to one side (rename churn
excluded below).

---

## A. The dominant driver: extension rename (C→E)

Core renamed `document_intelligence` → **`knowledge_vault`** (current correct name;
`document_intelligence` is the OLD name). Extended still uses the old name. This single
rename produces most of the "only-in" lists (`extensions/knowledge_vault/*` vs
`extensions/document_intelligence/*`, `tests/test_kv_*` vs `tests/test_doc_intel_*`) and
import churn inside many shared files (e.g. `rag_embeddings.py` docstrings).

**STATUS: DONE (2026-09-15, Extended v1.2.0)** — see `docs/KV_RENAME_PLAN.md`. Renamed the
folder, tests, imports, extension id, catalog, i18n (knowledge_vault.* keys in i18n.py +
i18n_extensions.py + extension_i18n.py, all 5 locales) to `knowledge_vault`; ported Core's
KV content (incl. the translation-vault tab) and the updated `document_editor` (added
`insert_from_chat.py`, removed `revision.py`). **Pickle-compat shim**
(`extensions/knowledge_vault/_compat_document_intelligence.py`) redirects old
`extensions.document_intelligence[.*]` imports to the renamed modules so existing users'
pickled vault indexes load with zero re-index; paired with the AppData dir migration in
`knowledge_vault/settings.py` and the DI→KV `settings.json` remap wired via the persistence
framework in `services/session/settings.py`.

---

## B. EDITION-EXT — Extended-only by design (do NOT port to Core)

Core is offline, chat/document only; Extended adds online + presentation/image output +
extra extensions. These differences are correct and permanent.

**Extra extensions:** `history_events`, `news_brief`, `research`, `token_tracker`,
`web_viewer`.

**Online / web (Core is offline):** `services/grounded_chat.py`,
`pipeline/base/grounding.py`, `pipeline/direct/grounded_runner.py`,
`services/web_viewer_history.py`; web-grounding branches inside `pipeline/workflow.py`,
`pipeline/output_format.py`, `services/web_fetch.py`, `services/session/settings.py`
(`web_grounding_enabled`).

**Image / presentation output:** `services/{chart_generation, diagram_generation,
diagram_icons, infographic_generation, poster_generation, poster_fonts, marker_visual,
image_model_prefs, image_text_detection, image_variations}.py`,
`pipeline/direct/image_intent.py`, `ui/components/style_picker.py`, the presentation
grounding / style-bias in `pipeline/direct/step_executor.py`, `presentation_*` deltas,
`role_pipeline.py` (`presentation_style`/`presentation_grounded` fields),
`profile_pack.py` (visual-marker guidance), `model_router.py` (flux tier + image catalog
cache), `resource_governor.py` (media keepalive grace window for per-slide image loops).

**Verdict:** leave divergent. When the refactor (governor/resolver) lands in these shared
files, apply the Core change *into* Extended's version by hand via this ledger — do not
overwrite Extended's feature code.

---

## C. EDITION-CORE — Core-only by design (do NOT port to Extended)

Formslator is Core-only (Extended swaps it for News Brief):
`extensions/formslator/*`, `services/formslator/*` (incl. `review_engine.py`,
`review_ingest.py`, `workspace_engine.py`, `workspace_llm.py`),
`services/office_mutation/track_changes.py`.
(`extensions/document_editor/insert_from_chat.py` is now shared — ported to Extended
v0.1.0 with its chat-message UI wiring + `chat.insert_*` i18n keys, no longer Core-only.)

---

## D. E→C — Extended improvements Core should take

| File | What Extended has | Action |
|---|---|---|
| `services/providers/ollama_provider.py` | `_loaded_context_length()` + `_chat_with_resize_guard()` — detects the model's loaded ctx and retries a chat when a resize is needed. **Half-built context-overflow handling Core lacks.** | **RECONCILE into the Context Governor** (§1 of refactor doc), don't copy verbatim — it's the provider-level piece of the governor's ladder. HIGH priority; do as part of governor build. |
| `services/web_fetch.py` | `final_url` = `page.url` after redirect, so citations point at the real article not the redirect. | E→C, low priority (only matters when Core fetches a pasted link). Safe to port. |
| `pipeline/capability_runtime/chat_runner.py` | Accumulates streamed text via a local `total_text` instead of slicing `state.messages`. | **RESOLVED — keep Core.** Core's slicing is intentional: it tracks position in the live assistant message to handle `stream_base` continuation + preview mirroring. Extended's `total_text` is no functional gain and would risk the core streaming path. Stays edition-divergent. |
| `services/resources/policies.py` | `MEDIA_KEEPALIVE_SECONDS` constant. | Tied to Extended's media keepalive (EDITION-EXT). Only port if the reentrant-media-lock improvement is taken. Low priority. |

---

## E. RECONCILE — i18n & config (both sides add keys)

`pipeline/i18n.py`, `pipeline/i18n_extensions.py`, `pipeline/extension_i18n.py`,
`pipeline/query_intent_i18n.py`, `config/extension_catalog.json`,
`config/model_catalog.py`, `pipeline/registry/catalog.py`.

**Action:** merge keys, never overwrite — each edition has strings the other needs
(Formslator keys in Core; web/image/extra-extension keys in Extended). All 5 locales per
CLAUDE.md §7–8. Do per-key, not whole-file.

---

## F. Remaining shared diffs — classify as touched

These differ mostly from the rename churn or edition features above; resolve opportunistically
when a file is next edited, tagging it here:

`main.py`, `build.bat`, `requirements.txt`, `settings.json`, `CLAUDE.md`, `EDITIONS.md`,
`README.md`, `README.txt`, `docs/*`, `packaging/*`, `config/__init__.py`,
`config/app_version.py`, `pipeline/{input_router, workflow, output_format, gap_handler,
intent_embeddings, image_mutation_dispatch}.py`, `pipeline/direct/{entry, express_runner,
highlight_excerpt, planner_worker, query_planner, task_roles}.py`,
`pipeline/roles/chat_roles.py`, `pipeline/deliverables/presentation_*`,
`services/{artifact_build, context_selector, media_query, model_assignments,
presentation_markdown, rag_embeddings, session/handlers, session/revision,
session/settings, plugins/*}.py`, `services/image_*`, `ui/**`.

**NOISE (never sync):** `debug-*.log`, `embedding-model/tokenizer.json`,
`package-lock.json`, `scratch_test_no_kv.docx.txt`, `.claude/*`, `OneDrive/`, `assets/`,
`data/`, `build/`, `dist/`, `*.pyc`.

---

## F2. New shared-spine modules to port Core → Extended (at the Extended port)

**STATUS: PORTED (2026-09-15, Extended v1.1.0)** — see `docs/EXTENDED_PORT_PLAN.md`.
Spine live in Extended (150 tests + verify_imports green). Also ported: the
edition-neutral locale-detection fixes from Core `17d2a6d` (highlight Q&A / edit-intent /
context strategy — `verb_edit_selection` concept added to Extended's query_intent_i18n).
Still deferred: the `document_intelligence`→`knowledge_vault` rename (§A) and its
settings.py migration wiring; and a **cross-edition follow-up** to add the
resize-stall-timeout guard (from Extended's old `ollama_provider`) to Core first, then
re-port — it was dropped from Extended here for edition consistency with Core's governor.

Built in Core this refactor; ported to Extended (adapting the noted edition differences):
- `services/persistence/` (migration framework) — port as-is. Register Extended's own
  store migrations (esp. the deferred `document_intelligence`→`knowledge_vault` rename, §A).
- `services/providers/` `ProviderCapabilities` + `loaded_context_length()` — port as-is;
  reconcile with Extended's existing `_chat_with_resize_guard` (§D) into the governor path.
- `services/context_governor.py` + its `llm_bridge` wiring — port as-is (budget modules
  are identical across editions, §G).
- `services/grounding.py` — port, then **add the web-search branch** (Extended is online):
  a live-fact NEED classifier + `services/grounded_chat.py` search, inserted before
  `model_knowledge` in `source_priority()`. This is the one real edition difference.
- `scripts/verify_imports.py` spine-boundary check — port as-is.
- `services/SERVICE_TEMPLATE.md` spine section — port as-is.

## G. Provider-level note for the refactor

`pipeline/direct/batch_budget.py` and `services/formslator/resource_budget.py` are
**identical** across editions — so the Context Governor built in Core ports to Extended
with no budget-logic merge. Only the provider resize guard (§D) and the edition feature
branches (§B) need hand-merging.
