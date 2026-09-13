# LOMA Dependency Map (baseline)

Generated during architecture overhaul Phase 0. Internal reference.

## Entry points

| Entry | Path | Flow |
|-------|------|------|
| App | `main.py` | NiceGUI, registries, `build_ui()` |
| Chat send | `services/session/handlers.py` → `send_message` | Thread → `start_loma_workflow` |
| Highlight | `ui/components/viewer_query.py` | → `start_loma_workflow` |
| Workflow | `pipeline/workflow.py` → `run_workflow` | Route (services) → direct or plan |

## Layer imports (intended direction)

```
ui → pipeline, services, extensions
pipeline → services, config
services → config, stdlib, services.types (shared datatypes only)
pipeline/context_retrieval.py composes document_chunker + text_search + context_selector helpers
extensions → services, ui
```

## Modules → target location

### pipeline/ (orchestration)
- `core/pipeline.py` → `pipeline/workflow.py`
- `core/classifier.py` → (removed) service-first `pipeline/input_router.py`
- `core/orchestrator.py` → `pipeline/orchestrator.py`
- `core/coordinator.py` → `pipeline/coordinator.py`
- `core/state_machine.py` → `pipeline/state_machine.py`
- `core/output_format.py` → `pipeline/output_format.py`
- `core/i18n.py` → `pipeline/i18n.py`
- `core/agents/*` → `pipeline/agents/*`
- `core/base/*` → `pipeline/base/*`
- `core/registry/*` → `pipeline/registry/*`
- `core/schemas/*` → `pipeline/schemas/*`

### services/ (atomic, flat)
| Target | Notes |
|--------|--------|
| `file_io.py` | Parses uploads; was legacy file_parser logic |
| `web_fetch.py` | Web scrape; was legacy web_parser logic |
| `image_generation.py` | Diffusers image service |
| `llm_bridge.py` | LLM chat + streaming |
| `renderer.py` | Artifact compile (docx, pptx, etc.) |

Legacy `capabilities/` folder removed. Chat deliverables run through `pipeline/direct` or `pipeline/agentic`.

## Circular dependency risks
- `pipeline/workflow` ↔ `ui` (NiceGUIStateSink) — inject UISink only
- `capabilities/document_writer` → ui logging — use sink/logger
- `session/state.get_ui_module()` — callback from main.py

## Phase 6 status (completed)

- Entry points import `pipeline.*` and flat `services.*` directly.
- Chat routing: `InputRouter` (service-first) → `run_workflow` → direct or plan pipeline.
- No capability registry; `capabilities/` directory removed.

## Smoke test checklist
- [ ] `python main.py` starts on :8080
- [ ] Send chat message (direct mode, simple Q&A → express lane)
- [ ] Upload .txt or .pdf
- [ ] Request docx report (direct planner + step executor, or plan mode with plan approval)
- [ ] High-complexity multi-step request (plan mode)
- [ ] Image generation request (image service + renderer compile)
