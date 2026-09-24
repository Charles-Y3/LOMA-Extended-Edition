# -*- coding: utf-8 -*-
"""Full-screen loading overlay until registries and routing model are ready."""
from __future__ import annotations

from nicegui import ui

from pipeline.i18n import t as tr
from services.session import state

_overlay_container = None
_overlay_label = None
_overlay_hint_label = None


def _ui_built() -> bool:
    try:
        from nicegui import context

        return bool(context.client.storage.get("loma_ui_built"))
    except Exception:
        return False


def _should_show_overlay() -> bool:
    from services.inference.readiness import inference_ready
    from services.startup_bootstrap import registries_ready
    from services.startup_warmup import is_routing_model_ready

    # Never hide until the workspace shell exists (prevents blank page on warmup race).
    if not _ui_built():
        return True
    if not registries_ready():
        return True
    if not inference_ready():
        return True
    # Server being reachable isn't enough — wait for the chat model itself to be warm
    # in VRAM, otherwise the first query pays the full cold-load latency post-splash.
    if not is_routing_model_ready():
        return True
    return False


def mount_startup_overlay() -> None:
    global _overlay_container, _overlay_label, _overlay_hint_label
    with ui.column().classes(
        "fixed inset-0 z-[9999] items-center justify-center gap-4 "
        "bg-black/75 backdrop-blur-sm loma-startup-overlay"
    ) as overlay:
        _overlay_container = overlay
        overlay.set_visibility(True)
        ui.spinner(size="lg", color="blue")
        _overlay_label = ui.label(tr("startup.loading")).classes(
            "text-sm text-white/90 tracking-wide text-center px-6"
        )
        _overlay_hint_label = ui.label(tr("startup.loading_hint")).classes(
            "text-[10px] text-white/50 text-center max-w-md px-6"
        )


def refresh_startup_overlay() -> None:
    if _overlay_container is None:
        return
    show = _should_show_overlay()
    # Never re-show the splash after it was dismissed — background E5/model
    # notifications were flipping it back on and looking like a full reload.
    try:
        was_visible = bool(_overlay_container.visible)
    except Exception:
        was_visible = True
    if show and not was_visible and _ui_built():
        show = False
    _overlay_container.set_visibility(show)
    if _overlay_label is None:
        return
    if not show:
        return
    from services.startup_bootstrap import embeddings_warming, registries_ready
    from services.inference.readiness import inference_ready, inference_status

    err = (getattr(state, "routing_warmup_error", "") or "").strip()
    hint = tr("startup.loading_hint")
    if err:
        _overlay_label.text = tr("startup.loading_failed", error=err[:120])
    elif not registries_ready():
        _overlay_label.text = tr("startup.loading_registries")
        hint = tr("startup.loading_hint_registries")
    elif not inference_ready():
        pid = inference_status()
        if pid == "timeout":
            _overlay_label.text = tr("startup.loading_inference_timeout")
        else:
            _overlay_label.text = tr("startup.loading_inference")
        hint = tr("startup.loading_hint_inference")
    else:
        model = getattr(state, "routing_model_name", "") or ""
        _overlay_label.text = (
            tr("startup.loading_model", model=model)
            if model
            else tr("startup.loading")
        )
        hint = tr("startup.loading_hint_model")
    if embeddings_warming():
        hint = f"{hint} {tr('startup.loading_hint_embeddings')}"
    if _overlay_hint_label is not None:
        _overlay_hint_label.text = hint


def begin_startup_poll() -> None:
    """Mount workspace when registries are ready; keep models loaded across page reload."""
    from services.startup_warmup import ensure_models_warm

    ensure_models_warm(background=True)
    warm_tts_voice_background()
    ui.timer(0.08, _poll_startup, once=True)


def warm_tts_voice_background() -> None:
    """Preload the currently-picked Piper voice so the first spoken reply doesn't pay the
    cold-start cost (see services/tts_engines.warm_piper_voice)."""
    import threading

    def _run() -> None:
        try:
            from services.session import state
            from services.tts_engines import warm_piper_voice
            from services.voice_reply import resolve_voice_id

            warm_piper_voice(resolve_voice_id(state.current_settings))
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True, name="loma-tts-warmup").start()


async def _poll_startup() -> None:
    from nicegui import context

    from ui.components.language_splash import language_picked

    if not language_picked():
        ui.timer(0.15, _poll_startup, once=True)
        return

    refresh_startup_overlay()

    from services.startup_bootstrap import registries_ready

    if registries_ready() and not context.client.storage.get("loma_ui_built"):
        from ui.layouts.main_layout import build_ui

        try:
            await build_ui()
            context.client.storage["loma_ui_built"] = True
        except Exception as exc:
            print(f"[LOMA] build_ui failed: {exc}")
            if _overlay_label is not None:
                _overlay_label.text = f"UI failed to load: {exc}"
            _try_setup_wizard()
            ui.timer(1.0, _poll_startup, once=True)
            return
        refresh_startup_overlay()
        if _overlay_container is not None and not _should_show_overlay():
            _overlay_container.set_visibility(False)
        _try_setup_wizard()

    if _should_show_overlay():
        ui.timer(0.15, _poll_startup, once=True)


def _try_setup_wizard() -> None:
    from nicegui import context

    if context.client.storage.get("setup_wizard_started"):
        return
    context.client.storage["setup_wizard_started"] = True

    def _run() -> None:
        if _overlay_container is not None:
            _overlay_container.set_visibility(False)
        try:
            from ui.components.setup_wizard import SetupWizard

            SetupWizard.maybe_run()
            watch["timer"] = ui.timer(3.0, _watch_wizard)
        except Exception as exc:
            print(f"[LOMA] setup wizard failed: {exc}")

    watch: dict = {"timer": None, "reopens": 0, "browser_missing": 0}

    # Watchdog: a wizard can silently end up not on the page the user is looking at — bound
    # to a page that reloaded/reconnected, removed when the workspace was rebuilt, or (seen
    # on slow first launches, Chromium) created and "open" on the server but never drawn by
    # the browser — leaving no wizard and no way to start one short of restarting the app.
    # Until setup completes, keep checking that the CURRENT page really shows a wizard (asking
    # the browser itself, since the server can't see a rendering failure), and rebuild it
    # when it doesn't.
    async def _watch_wizard() -> None:
        def _stop() -> None:
            if watch["timer"] is not None:
                watch["timer"].cancel()

        try:
            from nicegui import Client, context

            from ui.components.setup_wizard import SetupWizard, _get_setup

            # A watchdog belonging to a page that has since been closed/reloaded must not
            # fight the live page's watchdog over who owns the wizard.
            if _get_setup().get("completed") or context.client.id not in Client.instances:
                _stop()
                return
            # A closed/reloaded page lingers in Client.instances for a few seconds with no
            # socket; only a page with a live connection may (re)open the wizard.
            if not context.client.has_socket_connection:
                return
            server_ok = SetupWizard.is_shown_on(context.client)
            if server_ok:
                try:
                    drawn = await ui.run_javascript(
                        "!!document.querySelector('.loma-setup-wizard')", timeout=3.0
                    )
                except Exception:
                    return  # can't tell this tick
                if drawn:
                    watch["browser_missing"] = 0
                    return
                # Two ticks in a row, so a wizard still being drawn isn't rebuilt.
                watch["browser_missing"] += 1
                if watch["browser_missing"] < 2:
                    return
            watch["browser_missing"] = 0
            if watch["reopens"] >= 5:
                print("[LOMA] setup wizard could not be shown on this page")
                _stop()
                return
            watch["reopens"] += 1
            print(f"[LOMA] setup wizard missing on this page; reopening ({watch['reopens']})")
            old = SetupWizard._instance.dialog if SetupWizard._instance else None
            if old is not None and not old.is_deleted:
                old.delete()
            SetupWizard._running = False
            SetupWizard.maybe_run()
        except Exception as exc:
            print(f"[LOMA] setup wizard check failed: {exc}")
            _stop()

    # Only open the wizard after the real splash can dismiss — do not force-hide
    # the overlay early (that let users chat while models were still loading).
    def _when_ready() -> None:
        if _should_show_overlay():
            ui.timer(0.25, _when_ready, once=True)
            return
        _run()

    ui.timer(0.2, _when_ready, once=True)
