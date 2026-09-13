# LOMA pipeline refactor — grounding & context overflow

Structural plan to replace scattered per-feature patches with **two shared services**
that every LLM-using surface (chat pipeline, extensions, services) flows through:

1. **Context Governor** — one place that guarantees no request ever overflows the
   context window, degrading gracefully instead of erroring.
2. **Grounding Resolver** — one place that decides whether an answer needs source
   material and, if so, selects the relevant parts.

**Work Core first, get it right, then port to Extended via the sync ledger.**
No code changes until the ledger + Extended git baseline exist (§4).

---

## 0. Edition context (provenance — keep in sync with EDITIONS.md)

- **Core** — offline only. Grounded info comes from attached sources **or none**.
  No web search. Direct mode only. Has Formslator.
- **Extended** — built *off Core*, with features pulled in from the LOMA1 (full) folder.
  Online allowed (adds a web branch to the Grounding Resolver). Drops Formslator,
  adds news brief / research / webviewer / history events / token usage.
- **Complete** — the existing full **LOMA1** build. Not yet split into its own folder;
  ships later. Both Core and Extended ultimately descend from it. Track it here so the
  eventual three-way sync is not a surprise.

Two separate copies (not one shared core) is the **intended** end state — audiences
differ. The sync ledger (§4) is therefore permanent infrastructure, not a migration
step.

---

## 0.5 Common architecture — one spine for all editions and all extensions

The goal is not just two fixed bugs; it is a **single shared spine** that every edition
(Core, Extended, Complete/LOMA1) and every surface (chat pipeline *and* every extension)
goes through for LLM work. Editions differ only by which features sit on the spine, never
by having their own copy of it. An extension author wires into the spine; they do not
re-implement budgeting, grounding, model selection, cancellation, or error handling.

The spine, in order of a request's life:

```
profile/model → GROUNDING RESOLVER → CONTEXT GOVERNOR → PROVIDER(caps) → ERROR SURFACE
        (cancellable throughout · token usage recorded · versioned persisted state)
```

Seven shared components make up the spine. Two are **prerequisites** (build first — the
governor can't be correct without them); the core two are the governor + resolver; three
fold in alongside them.

| # | Component | Role | When |
|---|---|---|---|
| 1 | **Migration & versioning framework** | version stamp + registry of migrations for every persisted store (settings, profiles, KV pickles, glossaries, archives) | **PREREQUISITE** |
| 2 | **Provider capability descriptor** | `BaseProvider` exposes `can_set_ctx`, `max_loaded_ctx`, `supports_vision/think`, model→ctx-ceiling — callers stop guessing | **PREREQUISITE** |
| 3 | **Context Governor** (§1) | never-overflow ladder, branches on #2 | CORE |
| 4 | **Grounding Resolver** (§2) | sources(/web in Ext) → select → hand to governor | CORE |
| 5 | **User-facing error surface** | backend/exception → actionable localized message + optional retry; governor's safety net is one case of it | fold into #3 |
| 6 | **Shared "LLM task" helper** | one entry extensions call: profile + model-role + #4 + #3 + cancel + #5, so no surface re-fragments the spine | fold into #3/#4 |
| 7 | **Cancellation convention** | `run_cancellable` for blocking calls + a between-step check for multi-pass / batch loops; Stop always unwinds | fold into #3 |

Tracked but **not gating** the refactor:
- **#8 Model/role assignment** — divergent and spread across `model_router` /
  `model_assignments` / `startup_warmup` / `defaults`; those three also touch Ollama
  directly (the #2 leakage surface). Centralize only enough metadata for the governor to
  answer "model → ctx ceiling" now; full consolidation later.
- **Embedding-model lifecycle** — if KV retrieval becomes the one grounding engine, keep
  embedding load/warmup/memory centrally managed (mostly is, via `rag_embeddings`).
- **PyInstaller** — new modules + dynamic imports must be added to the `.spec`
  hiddenimports/datas or the `.exe` breaks silently (recurring pattern). A done-criterion.

Enforcement (§3) is what keeps every future edition and extension *on* this spine.

---

## 1. Context Governor  (`services/context_governor.py`)

### 1.1 Problem being replaced

Today ~5 budget systems coexist (`pipeline/direct/batch_budget.py`,
`services/formslator/resource_budget.py`, KV `cpu_budget.py`, Ludicity
`narrative_budget.py`, plus fixed char thresholds in `context_selector.py`,
`grounding.py`, `review_engine.py`). They are opt-in, use inconsistent
chars/token ratios (2.5–3.5, all English-biased), and have **no safety net** — a bad
estimate reaches the user as a raw backend error. This is the Formslator Review bug:
it uses a fixed char budget, and raising ctx in Model Library only masked it.

### 1.2 Callers declare prompt *parts*, not one string

```
fixed        system prompt + instructions      never cut
evidence     source / vault chunks             selectable (rank & drop)
history      prior turns / prior LOMA output   summarisable / droppable
payload      the document being processed       splittable into passes
expected_out rough output size                  so output isn't silently truncated
```

### 1.3 The ladder (branches by provider capability)

Provider capability comes from `BaseProvider` — Ollama can set `num_ctx` per request;
**LM Studio cannot** (context is fixed at model-load; see
`docs/lmstudio_known_issues.md`). The ladder branches on this.

```
1. MEASURE    count tokens — CJK-aware (Chinese ≈ 1–1.5 tok/char, not 2.5–3.5).
2. CEILING    true limit = min(model max, hardware RAM+VRAM, provider loaded ctx).
3. FITS NOW   → run.

  ── Ollama (can grow ctx) ───────────────────────────────────────────
4. RAISE      widen num_ctx / num_predict within ceiling.   [approach A]
5. SHRINK     if still over: select/rank evidence, summarise/drop history.
6. MULTI-PASS if still over: split payload → process → staged merge. [approach B]

  ── LM Studio (cannot grow ctx) ─────────────────────────────────────
4'. (skip — ctx is fixed)
5'. SHRINK + 6'. MULTI-PASS become the PRIMARY strategy, sized to the
    model's already-loaded window.

7. SAFETY NET if the backend STILL returns a context error: catch, split further,
   retry automatically. On LM Studio, if even one minimal pass can't fit the loaded
   window, surface the existing actionable message (chat.lmstudio_context_exceeded:
   "increase Context Length in {label} and reload") and offer retry — never a raw 400.
```

Approaches A and B from the original discussion are **steps 4 and 6 of one ladder**,
not competing options. Raising ctx is preferred (one pass = more coherence) but costs
reload latency, KV-cache memory, and GPU→RAM spill (a "fitting" call can be 10× slower).

### 1.4 Never just fail — graceful + cancellable + honest about slow hardware

- **Every governor path is cancellable** via the existing
  `services/session/workflow_control.run_cancellable`. The Stop button must unwind a
  multi-pass run at any point, including mid-pass.
- **Progress is surfaced** (pass N of M) through the existing UISink progress channel,
  so a long multi-pass run is legible, not a frozen UI.
- **When hardware forces a slow path** (many passes, or GPU spill), tell the user
  up front: "large input on limited memory — this will take a while; Stop anytime."
- The user should **never** see a raw context-length traceback. Worst case is a
  clear message + retry (LM Studio ceiling case).

### 1.5 Model Library ctx = preference, not hard cap  (decided)

- The stored Model Library number is a **starting point / floor**, never mutated.
- The governor may raise `num_ctx` **above** it at call time when a workload needs it.
- It reverts automatically: the run-scoped floor already lives on a fresh profile
  object per workflow (`batch_budget.bump_run_ctx_floor`), so it resets when the run
  ends and when LOMA closes. Nothing persists above the preference.
- **The one real code fix:** `batch_budget.py` currently caps the budget *down* to a
  profile ctx smaller than hardware (why raising it in Model Library "fixed" Review).
  Remove that cap-down so the preference can be exceeded when needed.
- Optional later: a "never exceed" toggle for users on slow machines who prefer a
  hard ceiling over auto-widening.

### 1.6 Consolidation

`batch_budget`, Formslator `resource_budget`, KV `cpu_budget`, Ludicity
`narrative_budget`, and the fixed char thresholds fold into the governor and are
deleted as callers migrate. Budget logic leaves `services/formslator/` — a Formslator
module should not own the global budget.

---

## 2. Grounding Resolver  (`services/grounding.py`) — Core = sources-only

### 2.1 Problem being replaced

~6 parallel policies: `grounded_chat.py` keyword regex (Extended), Extended's
`pipeline/base/grounding.py` broad trigger with a hard `[:4000]` cut, `context_builder`
+ `context_selector` fixed 12,000-char retrieval threshold, KV's own retrieval,
Formslator vault prefill, and raw `web_fetch` for pasted links.

### 2.2 One question, reused everywhere

```
GroundingRequest(query, task_kind, attached_sources, settings)
  1. NEED?    classify: does the answer depend on the sources?  (classifier, NOT keywords)
  2. SELECT   chunk → rank → pick relevant parts of the sources
  3. HAND OFF selected evidence → Context Governor  (never a hard [:4000] cut)
  4. ANSWER   with citations; if sources don't cover it, say so plainly
```

- **Step 1 is a small fast classifier** (LLM or embedding), not the
  "weather/price/latest/today" regex. This is what makes grounding work beyond narrow
  phrasing, in any language. Per CLAUDE.md §8, any concept lives in
  `pipeline/query_intent_i18n.py` with phrases for all 5 locales.
- **Core has no web branch.** Extended adds "web (if online setting on)" as one more
  source in step 2 — the only edition difference in this module.
- KV's hybrid retrieval becomes the **single** retrieval engine step 2 uses. Migrating
  callers onto it touches KV pickled index data — apply a compat shim (see the prior
  Knowledge Vault rename incident) to avoid a silent grounding failure.

---

## 3. Enforcement (keeps future features on the pipeline)

Add a `scripts/verify_imports.py` check that **fails** if any module:
- calls a provider / `ollama` / `lmstudio` directly instead of via `llm_bridge`, or
- builds a large prompt without going through the governor, or
- decides grounding with an ad-hoc keyword check instead of the resolver.

Update `EXTENSION_TEMPLATE.md` / `SERVICE_TEMPLATE.md`: "declare prompt parts, call the
governor; ground via the resolver." A new extension then cannot spawn a 6th budget
system by accident.

---

## 4. Sync ledger (do FIRST, before any code)

1. `git init` in **Extended** and commit its current state as a baseline — it has no
   history today, so nothing records its drift from Core.
2. Create `SYNC_LEDGER.md` (in each repo, kept identical). Tag each of the ~194
   differing / edition-only files:
   - **Core→Ext** — flows to Extended on next sync
   - **Ext→Core** — flows back to Core
   - **edition-only** — e.g. Formslator = Core-only; posters / research / grounded web
     = Ext-only
3. When Complete (LOMA1) gets its own folder, the ledger becomes three-way.

---

## 5. Order of work (one step per cycle, per CLAUDE.md)

1. Ledger + Extended git baseline — no code changes. **[done 2026-09-13]**
2. Design review sign-off (this doc, incl. §0.5).
3. **Prerequisite #1** — migration & versioning framework: version stamp + one migrations
   registry; fold the existing ad-hoc migrations (`_migrate_legacy_root`,
   `_migrate_renamed_extension_root`, `_migrate_extension_id`, `migrate_discontinued_models`)
   into it. This also de-risks the deferred KV-rename port to Extended (ledger §A).
4. **Prerequisite #2** — provider capability descriptor on `BaseProvider`; reconcile
   Extended's `ollama_provider` resize guard (ledger §D) into it; remove the
   `batch_budget.py` cap-down so Model Library ctx is a preference (§1.5). Settle the
   `chat_runner.py` reconcile (ledger §D) here while in the call path.
5. Build `context_governor.py` in Core (with error surface #5 + cancellation #7), on top
   of #2.
6. Migrate callers worst-first: **Formslator Review** → translate/format engines → KV →
   chat `context_builder` → Data Studio. Delete old budget modules as they empty.
7. Build `grounding.py` in Core; migrate the same callers. Introduce the shared "LLM task"
   helper (#6) as these callers converge.
8. Add the `verify_imports.py` guard + update `EXTENSION_TEMPLATE.md` /
   `SERVICE_TEMPLATE.md` so new surfaces must use the spine.
9. Update the `.spec` hiddenimports/datas for the new modules; verify a clean `.exe`
   (per the PyInstaller done-criterion).
10. Port to Extended via the ledger (resolver gains the web branch; apply Core changes
    *into* Extended's feature versions, don't overwrite).
11. (Later) Complete/LOMA1 folder split → three-way ledger + same spine.

---

## 6. Open items

- Confirm hardware-probe accuracy for the CEILING step on GPU + CPU-only machines
  (reuse `services/system/profiler.py` + KV `cpu_budget`'s CPU-only caps).
- Decide the classifier model for grounding step 1 (reuse the express/fast model).
- Decide whether the "never exceed" ctx toggle ships now or later.
