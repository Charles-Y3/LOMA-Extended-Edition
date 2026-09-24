# -*- coding: utf-8 -*-
"""Apply settings and refresh UI after first-run setup completes."""
from __future__ import annotations

import config
from services.model_assignments import normalize_role_assignments
from services.session import settings as session_settings
from services.session import state


def _sync_settings_from_disk() -> None:
    try:
        config.invalidate_models_cache()
        config.get_installed_models(force_refresh=True)
    except Exception:
        pass

    try:
        loaded = session_settings.load_settings()
        state.current_settings.clear()
        state.current_settings.update(loaded)
        assignments = normalize_role_assignments(state.current_settings.get("assignments"))
        state.current_settings["assignments"] = assignments
        config.sync_roles(assignments)
        session_settings.save_settings(state.current_settings, quiet=True)
    except Exception as exc:
        print(f"[LOMA Setup] post-setup settings reload failed: {exc}")

    try:
        from pipeline.startup_locale import apply_startup_messages

        apply_startup_messages()
    except Exception:
        pass


def _run_on_client(callback, *, label: str = "post-setup") -> None:
    from services.session.workflow_control import schedule_on_ui

    def _run() -> None:
        try:
            from nicegui import app

            clients = list(app.clients())
            # app.clients() lists the OLDEST page first, and a page the user already left (the
            # first-run language screen, a reload) lingers there for a few seconds with no
            # connection. Sending the reload to clients[0] then left the live page stuck on
            # "Setup complete — reloading workspace…" with the wizard still open. Only pages
            # that are actually connected can act on it.
            live = [c for c in clients if getattr(c, "has_socket_connection", False)]
            targets = live or clients
            if not targets:
                print(f"[LOMA Setup] {label}: no browser client connected")
                return
            for client in targets:
                with client:
                    callback(client)
        except Exception as exc:
            print(f"[LOMA Setup] {label} failed: {exc}")

    schedule_on_ui(_run)


def reload_after_setup(*, reload_delay_s: float = 0.8) -> None:
    """Reload settings and refresh the browser (must run after setup wizard UI work)."""
    _sync_settings_from_disk()

    def _ui_finish(client) -> None:
        from nicegui import ui

        from pipeline.i18n import t as tr

        try:
            from ui.components.startup_overlay import _overlay_container

            if _overlay_container is not None:
                _overlay_container.set_visibility(False)
        except Exception:
            pass

        try:
            from ui.layouts.workspace_panel import sync_execution_mode_select

            sync_execution_mode_select()
        except Exception:
            pass

        def _reload() -> None:
            try:
                client.run_javascript("window.location.reload()")
            except Exception as exc:
                print(f"[LOMA Setup] reload failed: {exc}")
                ui.notify(tr("setup.complete_manual"), type="info", timeout=8000)

        ui.timer(reload_delay_s, _reload, once=True)

    _run_on_client(_ui_finish, label="post-setup reload")

