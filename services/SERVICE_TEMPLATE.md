# LOMA service template

Services are **atomic** building blocks listed in `pipeline/registry/service_registry.py`. Capabilities and extensions **call** services; they do not replace the registry.

## Adding a built-in service

1. Implement `services/<service_id>.py` (or package) with a clear public API.
2. Register in `pipeline/registry/service_registry.py` `_SERVICES` tuple:

```python
ServiceDescriptor(
    "<service_id>",
    "One-line description for the Input Router LLM",
    "services.<module_path>",
),
```

3. If the service is required for a deliverable type, update `pipeline/output_format.py`:
   - `allowed_services_for_deliverable()`
   - `required_services_for_deliverable()` (when applicable)
4. Reference the service id in capability `metadata()["services"]` or `["optional_services"]`.

## Agentic / runtime registration

Generated services use `service_registry.register_runtime(id, brief, module)` (see `pipeline/sdlc/registration.py`). Runtime ids must still pass `service_registry.is_valid()` after registration.

## Shared spine (required)

Every service that talks to an LLM MUST go through the shared spine
(`docs/PIPELINE_REFACTOR.md` §0.5) — never import `ollama`/`openai` or a provider
adapter directly (`scripts/verify_imports.py` fails the build if you do):

- **Inference** → call `services.llm_bridge.chat()` / `generate()`. This routes every
  call through the Context Governor, so you do NOT hand-manage `num_ctx` to avoid
  overflow — the governor sizes the window (CJK-aware), raises it to fit within the
  machine's ceiling, and retries on overflow. A user-set Model Library `num_ctx` is the
  floor; the governor only ever raises from it, transiently.
- **Grounded facts** → decide YES/NO via `services.grounding.needs_source_grounding()`
  and select evidence through the same path (Core is sources-only; there is no web
  search). Don't invent a per-service keyword gate.
- **Persisted state** → version it through `services.persistence` (register a migration,
  `stamp()` on save, `migrate()` on load) so a future rename can't silently break
  on-disk data.
- **Cancellation** → long/multi-step work must honour Stop
  (`services.session.workflow_control.run_cancellable`, and a between-step check in loops).

## Validation

Run `python scripts/verify_imports.py` — it checks that every capability’s declared
services exist in the service registry, and that no module bypasses the spine by
importing an LLM backend directly.
