# LOMA architecture

Developer reference: registries, pipeline stages, and the rules that keep the codebase
consistent. For downloading and running LOMA, see [../README.md](../README.md).

**See also:** [loma_workflow.md](loma_workflow.md) (full input → output walkthrough) ·
[loma_workflow_flowchart.png](loma_workflow_flowchart.png) ·
[dependency_map.md](dependency_map.md) (module import map)

## Run from source (developer path)

```bash
# Windows
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe main.py

# macOS
brew install ffmpeg   # optional system dep
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python main.py
```

Open <http://localhost:8080>.

All dependencies (including image generation and RAG) install unconditionally from
`requirements.txt` via `run.bat` / `run.sh`. If a venv is missing packages later (e.g.
deleted manually), reinstall from Settings, or manually:

```bash
# Windows: .\venv\Scripts\python.exe -m pip install -r requirements.txt
# macOS:   ./venv/bin/python -m pip install -r requirements.txt
```

After structural changes:

```bash
python scripts/verify_imports.py
```

### Packaged builds

PyInstaller thin builds (core only; heavy ML installs on demand):

| Platform | Workflow | Artifact |
|----------|----------|----------|
| Windows | `.github/workflows/build-windows.yml` | `LOMA-windows.zip` (onedir) |
| macOS | `.github/workflows/build-macos.yml` | `LOMA-macos.zip` (`.app`, **Apple Silicon / arm64 only**) |

Trigger: push a `v*` tag, or run the workflow manually. Spec: `packaging/loma.spec`.

**Apple Silicon only:** the macOS build runs on GitHub's `macos-14` (arm64) runner and is
not a universal2 binary. On an Intel Mac, run from source instead (see above) rather than
downloading the packaged `.app`.

**Codesigning:** the `.app` is ad-hoc signed in CI (`codesign --deep --force -s -`) — no
paid Apple Developer ID or notarization, so Gatekeeper still shows the unidentified-developer
prompt (see the README's macOS section for the one-time workaround).

Packaged apps store writable data under:

- Windows: `%APPDATA%\LOMA`
- macOS: `~/Library/Application Support/LOMA`
- Linux: `~/.loma`

---

## Two building blocks + pipeline

Every feature is either a **Service** (atomic) or an **Extension** (user tool with UI).
Chat missions are orchestrated by `pipeline/` (direct or plan) — not by a separate
"capability registry" or matched capability modules.

### Service — atomic unit

- Performs ONE specific action
- Returns raw data (structured dict / bytes / text — not a full user workflow)
- Is registered in the Service Registry (stable id + description)

Examples: `llm_bridge`, `file_io`, `web_fetch`, `renderer`, `artifact_store`,
`image_generation`, `media_transcription`, `graph_generation`, `sandbox_runner`

> **Rule:** Multi-step user missions belong in `pipeline/` (direct or plan), which calls
> services in sequence. Extensions also call services; they do not replace
> `pipeline/workflow.py`.

### Extension — interactive tool

- Orchestrates services for a focused tool (document intelligence, formslator, etc.)
- Adds a user interface so the user can configure inputs (paths, accounts, toggles, …)
- Lives in the Extension panel (dropdown in Input panel)
- Is registered in the Extension Registry + `config/extension_catalog.json`
- Is invoked by the user explicitly — parallel to chat, not auto-routed from `send_message`

Folder convention:

```
extensions/<extension_id>/
    extension.py    # class inheriting BaseExtension with mount(container)
```

Examples: `knowledge_vault`, `formslator`, `profile_manager`,
`chat_archive_manager`, `history_events`

> **Rule:** Extensions call services. They must not contain chat workflow orchestration
> that belongs in `pipeline/workflow.py`.

### Pipeline — chat workflow orchestration

The main LOMA chat path (workspace send / highlight query) runs `pipeline/workflow.py`:

- **Input Router** (service-first): `output_type`, `required_services`, `mode` — no capability match
- **Execution mode** (user setting): direct (default) or plan
- **Direct** (`pipeline/direct/`): query planner → express lane OR role steps → delivery
- **Plan** (`pipeline/agentic/`): orchestrator plan → optional approval → workers → delivery

Entry points: `pipeline/workflow.py`, `pipeline/direct/entry.py`, `pipeline/agentic/runner.py`.

---

## Registry architecture

| Registry | Location | Purpose |
|---|---|---|
| Service Registry | `pipeline/registry/service_registry.py` | Catalog of atomic service IDs for Input Router and agents |
| Extension Registry | `pipeline/registry/extension_registry.py` | Discover and mount Extension panel tools |

Extension catalog (titles, tiers, visibility): `config/extension_catalog.json`

### Registration rules

- **Services:** stable id, brief description. Referenced by `InputRouter.required_services`
  and by pipeline steps (`llm_bridge`, `file_io`, `web_fetch`, `renderer`, …).
- **Extensions:** at startup, scan `extensions/*/extension.py`, import classes subclassing
  `BaseExtension`, register by `extension_id`. UI dropdown + Extension Library use catalog
  metadata (`show_in_dropdown`, `show_in_library`, `tier`).
- Chat routing does NOT match a capability registry. `pipeline/workflow.py` branches on
  `execution_mode` (direct | plan) after service-first `InputRouter.route()`.

### Published builds (.exe / .app)

- Plan-mode synthesis (LOMA writing new services/extensions for itself) is **not
  implemented** in the current version — aspirational, not active code. Only user-installed
  extensions (Extension Library) are written to the user plugin area, registered, and used
  on subsequent requests without rebuilding the binary.
- The built-in Service Registry catalog is the contract the Input Router and agents use.
  Runtime additions extend that catalog in memory and on disk.
- Target layout (product): application bundle + user-writable plugin roots, e.g.
  `<AppData>/LOMA/plugins/services/`, `<AppData>/LOMA/plugins/extensions/`.
  Registries must load from both shipped and user plugin directories.
- User extension installs: `%AppData%/LOMA/plugins/extensions/<extension_id>/`.
  Bundled extensions ship under `extensions/`.

---

## Execution modes

Workspace selector (settings: `execution_mode` / `chat_execution_mode`):

| Mode | Path |
|------|------|
| **direct** (default) | Service-first routing → `pipeline/direct` (express chat, grounded chat, or LLM planner + `step_executor`) |
| **plan** | Full orchestrator → facilitator → workers → delivery compile. Plan approval before execution. No usage quota or tier gating in the current version. |

### Extension Library

Input panel → Extension Library:

- Browse all extensions by category
- Install/remove user plugins under `%AppData%/LOMA/plugins/extensions/`

No tiers, user groups, or payment gating in the current version — all bundled and installed
extensions are available to every user.

---

## Workflow — from input to output

Chat missions: `services/session/handlers.py` → `pipeline/workflow.py` (`run_workflow`).
Extensions are parallel (user-selected); they do not replace this path. There is NO
capability registry or matched-capability step.

Cross-cutting: cancellation (`workflow_control`), stale-output guards, progress
(Intent → Planner → Execution → Synthesis), UISink updates.

```
main.py startup → extension_registry + UI
User send / highlight
  → handlers.send_message → thread → run_workflow
  → build_input_metadata
  → InputRouter.route → RoutingDecision (no capability_id)
  → profile + web_fetch + build_context_bundle (+ optional charts)
  → [image packages missing?] installer exit
  → grounded web chat? → deliver
  → else execution_mode:
        plan   → quota → plan → [approve] → workers → compile → deliver
        direct → planner → express OR step pipeline → deliver
  → chat workspace message and/or data/generated/ in Preview
```

### Application startup (`main.py`)

1. Load config, session state, model roles.
2. `extension_registry.discover("extensions")` — catalog from `extension_catalog.json`.
3. `service_registry` — static service ids for InputRouter and agents.
4. `build_ui()` — workspace, Input, Extension panel, Preview, Console.

### Entry points

**Chat** — workspace send / highlight / plan or delivery approval:

```
send_message (handlers.py)
  → preflight (vision/media gap installers if needed)
  → threading.Thread → start_loma_workflow → run_workflow
Highlight:      viewer_query.py → same workflow entry
Plan pause:     approve_plan_review / revise_agentic_plan
Delivery pause: resume_agentic_after_delivery_approval / revise_agentic_delivery
```

**Extension** — Input dropdown / Extension Library:

```
extension_registry mounts BaseExtension UI
  → extension tabs call services/* on user action
  → no InputRouter, no run_workflow (unless user also sends chat)
```

### Stage 1 — Input

From session state: `instruction` (chat, highlight excerpt, revision) ·
`active_context_files`, `active_web_links` · settings (`execution_mode`, output format,
web grounding, profile) · workspace (messages, preview draft/selection, mutation state).

`build_input_metadata(state)` → `InputMetadata`

### Stage 2 — Route & context

| Step | Module | Output |
|---|---|---|
| 1 | `input_router.InputRouter.route` | `RoutingDecision`: `output_type`, `required_services`, `mode`, `confidence`, `reason`, `llm_type` |
| 2 | `context_builder.build_request` | messages + file/link snapshot |
| 3 | `profile_manager` | active profile YAML or defaults |
| 4 | `web_fetch` (+ `web_context_cache`) | if `web_fetch` in `required_services` |
| 5 | `build_context_bundle` | unified text, images, tables, retrieval |
| 6 | `graph_generation` (optional) | chart artifacts on analytical queries |
| 7 | `gap_handler` | image package installer → early exit |

Router: `resolve_deliverable_type` + `required_services_for_deliverable` + LLM refine +
special routes (`media_transcription`, `image_mutation`, …). Preview selection forces
`mode=generation`.

### Stage 3 — Execution

```
┌─ should_use_grounded_chat? ──YES──► run_grounded_chat ──► deliver
│
└─ NO ──► execution_mode?
            │
            ├─ plan ──► orchestrator plan + subtask instructions
            │              [plan_review pause → user approve/revise]
            │              facilitator workers + compile_gate
            │              [optional delivery_review pause]
            │              ──► _deliver_output
            │
            └─ direct ──► plan_direct_query (LLM planner)
                          │
                          ├─ express lane? ──► run_express_chat (or grounded)
                          │
                          └─ else step_executor.run_direct_pipeline
                                 generation: role_pipeline (writer, slide_author, …)
                                 mutation:   preview slices → merge → compile
                                 ──► _deliver_output
```

Direct entry: `pipeline/direct/entry.py` · Plan entry: `pipeline/agentic/runner.py` ·
Role runners (internal): `pipeline/capability_runtime/`

### Stage 4 — Output delivery (`_deliver_output`)

Staleness: skip if user sent a newer message.

- `chat` → assistant message in workspace; no artifact file
- `document`, `presentation`, `spreadsheet`, `sound`, `image` → draft →
  `save_preview_to_artifact` → `data/generated/` → Preview panel; assistant gets
  summary/story text

### Stage 5 — Result

Chat bubble and/or Preview artifact; progress idle; ready for next input. Plan-mode pauses
leave plan/delivery UI until the user responds.

### Extensions (parallel path — examples)

| Extension | Role |
|---|---|
| `knowledge_vault` | catalogue, index, Ask/Analyze |
| `formslator` | format, translate, glossary, vault prefill |
| `history_events` | scenarios, grading |
| `profile_manager` | profiles |
| `chat_archive_manager` | archives |

Pattern: `extension.py` `mount()` → services on user action; not auto-routed from chat.

---

## Layered system overview

| Layer | Role |
|---|---|
| UI Layer | NiceGUI — render, bind events, display state only |
| Pipeline Layer | workflow, input_router, `direct/`, `agentic/`, orchestrator, registries |
| Extension Layer | User-selected tools with configuration UI (compose services) |
| Service Layer | Atomic operations (raw data in/out) |

Import direction (enforced):

```
ui          → pipeline, services, extensions (mount only)
pipeline    → services, registries (service-first routing; no capability modules)
extensions  → services, pipeline helpers (no chat workflow orchestration in extension)
services    → config, stdlib only (no other services.*)
```

---

## Directory layout

```
LOMA/
├── main.py                 # startup: discover extensions, load settings
├── README.md                # end-user quick start
├── run.bat / run.sh         # end-user launcher scripts (Python check, venv, deps, launch)
├── config/                  # settings.yaml, models.yaml, profiles, extension_catalog.json
├── pipeline/
│   ├── workflow.py         # run_workflow — main chat orchestration
│   ├── input_router.py     # service-first routing → RoutingDecision
│   ├── direct/             # express lane, query planner, step_executor
│   ├── agentic/            # runner, quota, plan resume
│   ├── orchestrator.py, coordinator.py, context_builder.py
│   ├── capability_runtime/ # role_pipeline, chat_runner (internal, not registry)
│   ├── registry/
│   │   ├── service_registry.py
│   │   └── extension_registry.py
│   ├── base/base_extension.py
│   └── schemas/task_schema.py
├── extensions/             # one folder per extension_id
├── services/               # flat atomic modules
├── ui/                     # layouts, components, themes
├── data/                   # uploads/, generated/, chats/
└── docs/                   # loma_workflow.md, loma_workflow_flowchart.png, dependency_map.md, ARCHITECTURE.md
```

---

## Architectural rules (must follow)

### UI layer

UI files ONLY: render components, bind events, display state.
UI must NEVER: run LLM logic, parse files, execute orchestration, own app state.

### Services

- One action, one module responsibility
- No imports from other `services.*`
- Register every service in the Service Registry with id + description

### Extensions

- User-facing configuration and tool UI
- Inherit `BaseExtension`; implement `mount(container)`
- Use services for I/O; do not duplicate `pipeline/workflow.py` orchestration

### Registries

- Service lists for routing — `service_registry` + InputRouter helpers
- Extension dropdown — `extension_registry` + `extension_catalog.json`
- No `capability_registry`; chat execution is direct or plan pipeline code

### Localization

All user-visible strings must use `from pipeline.i18n import t as tr` and `tr("key")`, with
keys added for `en`, `zh_tw`, and `zh_cn` in `pipeline/i18n.py` or
`pipeline/i18n_extensions.py`. No hardcoded display text.

---

## Drop-in and auto-discovery

At startup (`main.py`):

1. `extension_registry.discover("extensions")` (+ user plugin path when configured)
2. `service_registry` loaded (static catalog; runtime synthesis may extend)

**Adding an extension**

1. Create `extensions/my_tool/extension.py` (subclass `BaseExtension`)
2. Add entry to `config/extension_catalog.json`; register `BUILTIN_EXTENSION_IDS` if bundled
3. Restart LOMA — extension appears per catalog visibility flags

**Adding a service**

1. Add `services/my_action.py` (atomic)
2. Register in the Service Registry with id + description
3. Reference id from InputRouter / pipeline steps / agents as needed

---

## UI workspace

- **Top bar:** LOMA title, process indicators (Intent → Planner → Execution → Synthesis), settings
- **Input panel:** Sources (files, links), Profile, Extension dropdown
- **Extension panel:** active extension UI (viewers, archives, email, …)
- **Workspace panel:** chat, snapshot save, new chat
- **Output panel:** Console, Preview (artifacts in `data/generated/`), Sandbox

Extensions are selected from the Input panel; chat missions use `pipeline/workflow.py`
(direct or plan), not a separate capability auto-router.

---

## Agents (plan mode)

When `execution_mode = plan`, `run_agentic_loop` uses:

- **Orchestrator** (`LomaOrchestrator`) — mission plan + subtask instructions
- **Facilitator** (`LomaFacilitator`) — coordinate worker steps against the plan
- **Workers** — role-specific LLM steps per plan (Coder-style agents for gaps)
- **Verifier** — validate outputs before delivery

Plan approval: plan-mode runs pause for user review unless resuming an approved plan.
Direct mode skips this orchestration entirely.

Plan-mode synthesis (LOMA registering new services/extensions for itself) is **not
implemented** in the current version.

---

## Deliverable types (chat pipeline)

Handled by `pipeline/direct` and `pipeline/agentic` (not separate capability folders):

- `chat`, `document`, `presentation`, `spreadsheet`, `sound`, `image` — via InputRouter `output_type`
- generation vs mutation — preview selection and planner mode
- `media_transcription` — audio/video → timestamped text in context or document export
- `image_generation` / `image_mutation` — dedicated services when routed
- `graph_generation` — optional chart enrichment on analytical queries

Office mutation uses pipeline mutation roles + renderer compile (`.docx`, `.pptx`, `.xlsx`).
`file_io` parses text, office, pdf, images, xlsx; delegates A/V to `media_transcription`.

---

## Image generation

Default model: `Lykon/dreamshaper-8`
Fallback env: `LOMA_IMAGE_MODEL=runwayml/stable-diffusion-v1-5`

Requires packages from `requirements.txt` (installed by default). The gap handler in the
workflow may prompt for a reinstall before retry if a venv is missing them.

---

## Update system (modular, GitHub-based)

**Versioning**

- `config/app_version.py` — `APP_VERSION` (whole-app version)
- Every service (`ServiceDescriptor.version`) and every extension
  (`config/extension_catalog.json` `"version"`, surfaced via `BaseExtension.metadata()`)
  carries its own semver string, default `"0.1.0"`

**Update source** (`config/update_source.py`)

- `UPDATE_REPO` — `"owner/repo"` on GitHub. Empty by default; update checks are a no-op
  until this is set once LOMA is published.
- Remote manifest (`UPDATE_MANIFEST_PATH`, default `update_manifest.json` at repo root) is
  JSON with `app_version`, `services{id: version}`, `extensions{id: version}`, and
  `files{"app": [...], "services": {id: [paths]}, "extensions": {id: [paths]}}`. The files
  map is what makes updates modular — each component lists exactly which repo-relative
  files belong to it.

**Flow**

- `services/update/manifest.py` — builds the local manifest from the live registries
- `services/update/checker.py` — fetches the remote manifest, compares versions, returns
  components with a newer remote version. Returns empty (never raises) if `UPDATE_REPO` is
  unset or the network is unreachable.
- `services/update/apply.py` — for a single component, downloads every file in its `files`
  list and only writes them once ALL downloads succeed. No full-app redownload; a failed
  apply leaves nothing partially updated.

**Settings & UI**

- Setting: `update_check_enabled` (bool, default `False`)
- Settings → About tab: toggle + "Check now" (`ui/layouts/topbar.py`). Any update found
  always shows a confirm dialog before downloading/applying — nothing installs silently.

---

## Refactor status

**Current architecture**

- `pipeline/workflow.py` — single chat entry; service-first InputRouter
- `execution_mode`: direct (default) or plan
- `capabilities/` folder removed; logic lives in `pipeline/direct`, `pipeline/agentic`,
  `pipeline/capability_runtime` (internal role runners), and `services/*`
- Two registries: service + extension

**In progress / target**

- Service Registry: dynamic discovery and runtime registration
- Strict no cross-service imports enforced in CI
- User plugin directories for published builds
- Registry refresh without full restart
- Modular update system: per-component versioning + GitHub update check

See [dependency_map.md](dependency_map.md) for the module-level import map and smoke
checklist.
