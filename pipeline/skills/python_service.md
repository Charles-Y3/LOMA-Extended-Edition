# Python service skill pack

- Place module under `services/` with a single clear public responsibility.
- Do not import other `services.*` modules except shared types if unavoidable.
- No `ui` or `pipeline` imports from services.
- Expose functions with type hints and docstrings for orchestration use.
