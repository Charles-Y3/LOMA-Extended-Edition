# -*- coding: utf-8 -*-
"""Background registry discovery and system probe so ui.run starts quickly."""
from __future__ import annotations

import threading

_lock = threading.Lock()
_started = False
_registries_ready = False
_profile_ready = False
_embeddings_warming = False


def registries_ready() -> bool:
    return _registries_ready


def profile_ready() -> bool:
    return _profile_ready


def embeddings_warming() -> bool:
    """True while the E5 intent/RAG embedder is loading (HF 'Loading weights')."""
    return _embeddings_warming


def start_background_init() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True

    def _run() -> None:
        global _registries_ready, _profile_ready, _embeddings_warming
        try:
            print("[LOMA] Discovering extensions…", flush=True)
            from pipeline.registry.extension_registry import extension_registry

            extension_registry.discover()
            try:
                from pipeline.registry.integrity import validate_all_registries

                errors = validate_all_registries(extension_registry)
                for err in errors:
                    print(f"[LOMA] registry validation: {err}", flush=True)
            except Exception as exc:
                print(f"[LOMA] registry validation skipped: {exc}", flush=True)
        except Exception as exc:
            print(f"[LOMA] registry discover failed: {exc}", flush=True)
        finally:
            _registries_ready = True
            print("[LOMA] Extensions ready.", flush=True)
            _notify_overlay()

        try:
            from services.system.profiler import get_system_profile

            get_system_profile()
        except Exception as exc:
            print(f"[LOMA] system profile probe failed: {exc}", flush=True)
        finally:
            _profile_ready = True

        # E5 load can take 15–60s after the tqdm bar hits 100%. Keep it off the
        # critical path, and do NOT poke the splash overlay when it finishes —
        # that was re-showing the loading screen after the UI was already up.
        def _warm_embeddings() -> None:
            global _embeddings_warming
            _embeddings_warming = True
            try:
                print(
                    "[LOMA] Loading intent embedding model (E5) — may take a minute; "
                    "UI can continue…",
                    flush=True,
                )
                from pipeline.intent_embeddings import warm_intent_classifier

                warm_intent_classifier()
                print("[LOMA] Intent embedding model ready.", flush=True)
            except Exception as exc:
                print(f"[LOMA] intent classifier warmup skipped: {exc}", flush=True)
            finally:
                _embeddings_warming = False

        threading.Thread(
            target=_warm_embeddings, daemon=True, name="loma-embed-warmup"
        ).start()

    threading.Thread(target=_run, daemon=True, name="loma-startup-init").start()


def _notify_overlay() -> None:
    try:
        from services.session.workflow_control import schedule_on_ui
        from ui.components.startup_overlay import refresh_startup_overlay

        schedule_on_ui(refresh_startup_overlay)
    except Exception:
        pass
