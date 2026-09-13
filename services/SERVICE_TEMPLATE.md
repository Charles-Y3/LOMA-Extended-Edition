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

## Validation

Run `python scripts/verify_imports.py` — it checks that every capability’s declared services exist in the service registry.
