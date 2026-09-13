# Extension UI skill pack

- Subclass `BaseExtension` from `pipeline.base.base_extension`.
- Set `extension_id` and `label`; implement `mount(self, container)`.
- Use NiceGUI inside `mount` only; call `services.*` for logic.
- Keep UI state local to the extension module.
