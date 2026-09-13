# -*- coding: utf-8 -*-

"""Load config/*.yaml and expose runtime model/settings API."""

from __future__ import annotations



import concurrent.futures

import logging

import threading

import time

from pathlib import Path



import ollama

import yaml



_CONFIG_DIR = Path(__file__).resolve().parent



_settings = yaml.safe_load((_CONFIG_DIR / "settings.yaml").read_text(encoding="utf-8")) or {}

_models = yaml.safe_load((_CONFIG_DIR / "models.yaml").read_text(encoding="utf-8")) or {}

_profiles_meta = yaml.safe_load((_CONFIG_DIR / "profiles.yaml").read_text(encoding="utf-8")) or {}



def _normalize_ollama_host(url: str | None) -> str:
    """Use 127.0.0.1 — on Windows, localhost often resolves via IPv6 and adds ~2s latency."""
    raw = (url or "http://127.0.0.1:11434").strip()
    if "localhost" in raw.lower():
        return raw.replace("localhost", "127.0.0.1").replace("LOCALHOST", "127.0.0.1")
    return raw


OLLAMA_BASE_URL = _normalize_ollama_host(_settings.get("ollama_base_url"))
import os as _os

_ollama_host = _normalize_ollama_host(_settings.get("ollama_base_url")).replace("http://", "").replace(
    "https://", ""
)
_os.environ.setdefault("OLLAMA_HOST", _ollama_host)

LMSTUDIO_BASE_URL = _normalize_ollama_host(
    _settings.get("lmstudio_base_url", "http://127.0.0.1:1234")
)

CONNECTIVITY_PROBE_URL = _settings.get("connectivity_probe_url", "https://1.1.1.1")

GOVERNOR_TIGHT_RAM_GB = float(_settings.get("governor_tight_ram_gb", 8))

# Below this VRAM, the LLM is offloaded before media (image/audio) work so they don't
# oversubscribe the GPU. A card must hold an LLM (~2-4GB) AND a diffusion/TTS model
# (~2-8GB) at once to run them concurrently, so anything under ~12GB is "tight" —
# otherwise Windows/WDDM silently pages VRAM over PCIe and media generation crawls.
GOVERNOR_TIGHT_VRAM_GB = float(_settings.get("governor_tight_vram_gb", 12))

GOVERNOR_MIN_AVAILABLE_RAM_GB = float(_settings.get("governor_min_available_ram_gb", 4))

# How long a media pipeline (image/audio/video) stays loaded after a request finishes,
# in case another media request follows right behind it (e.g. two image generations
# back-to-back) — avoids reloading/re-parsing the model from scratch for that case. A
# chat/LLM request arriving during this window evicts the still-warm pipeline
# immediately rather than waiting it out, so this never delays chat responsiveness.
GOVERNOR_MEDIA_KEEPALIVE_SECONDS = float(_settings.get("governor_media_keepalive_seconds", 90))

INTENTS = list(_settings.get("intents", []))

SETTINGS_FILE = _settings.get("settings_file", "data/settings.json")

PROFILES_DIR = Path(_settings.get("profiles_dir", "profiles"))

THEME_DEFAULT = _settings.get("theme_default", "dark")

LANGUAGE_DEFAULT = _settings.get("language_default", "en")

ACTIVE_PROFILE_DEFAULT = _settings.get("active_profile_default", "simple_assistant")

GMAIL_OAUTH_CLIENT_ID = _settings.get("gmail_oauth_client_id", "")

GMAIL_OAUTH_CLIENT_SECRET = _settings.get("gmail_oauth_client_secret", "")



ROLES: dict[str, str] = dict(_models.get("roles", {}))



_MODELS_CACHE: tuple[float, list[str]] | None = None

_MODELS_CACHE_TTL = 45.0

_LIST_MODELS_TIMEOUT = 10.0

_models_probe_lock = threading.Lock()

_RECOMMENDED_MODELS: dict[str, str] | None = None





def sync_roles(assignments: dict) -> None:

    global ROLES

    ROLES.update(assignments)





def _run_with_timeout(fn, timeout: float):

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:

        future = pool.submit(fn)

        try:

            return future.result(timeout=timeout)

        except concurrent.futures.TimeoutError:

            logging.warning("Model list probe timed out after %.1fs", timeout)

            return None

        except Exception as exc:

            logging.debug("Model list probe failed: %s", exc)

            return None





def _fetch_installed_models() -> list[str] | None:
    """List models from the pinned inference backend only."""

    def _via_active_provider():
        from services.providers.registry import get_active_provider

        return get_active_provider().list_models()

    return _run_with_timeout(_via_active_provider, _LIST_MODELS_TIMEOUT)


def invalidate_models_cache() -> None:
    global _MODELS_CACHE
    _MODELS_CACHE = None


def get_installed_models(*, force_refresh: bool = False) -> list[str]:
    import time as _time

    from pipeline.debug_session import debug_log

    global _MODELS_CACHE

    now = _time.perf_counter()

    if not force_refresh and _MODELS_CACHE and (now - _MODELS_CACHE[0]) < _MODELS_CACHE_TTL:
        debug_log(
            "config:get_installed_models",
            "cache hit",
            {"age_ms": round((now - _MODELS_CACHE[0]) * 1000, 1), "count": len(_MODELS_CACHE[1])},
            "A",
        )
        return list(_MODELS_CACHE[1])

    with _models_probe_lock:
        now = _time.perf_counter()
        if not force_refresh and _MODELS_CACHE and (now - _MODELS_CACHE[0]) < _MODELS_CACHE_TTL:
            return list(_MODELS_CACHE[1])

        _t0 = _time.perf_counter()
        result = _fetch_installed_models()
        if result is not None:
            _MODELS_CACHE = (now, list(result))
            debug_log(
                "config:get_installed_models",
                "probe ok",
                {"ms": round((_time.perf_counter() - _t0) * 1000, 1), "count": len(result)},
                "A",
            )
            return list(result)

        debug_log(
            "config:get_installed_models",
            "probe failed or timed out",
            {"ms": round((_time.perf_counter() - _t0) * 1000, 1)},
            "A",
        )
        if force_refresh:
            _MODELS_CACHE = (now, [])
        return list(_MODELS_CACHE[1]) if _MODELS_CACHE else []





def get_smart_assignments() -> dict[str, str]:
    from services.model_assignments import get_smart_assignments as _smart

    return _smart(get_installed_models())





def get_recommended_models() -> dict[str, str]:

    global _RECOMMENDED_MODELS

    if _RECOMMENDED_MODELS is None:

        _RECOMMENDED_MODELS = get_smart_assignments()

    return dict(_RECOMMENDED_MODELS)





def pull_model(model_name: str) -> bool:

    try:

        logging.info(f"LOMA: Pulling {model_name}...")

        response = ollama.pull(model_name, stream=True)

        for chunk in response:

            if "status" in chunk:

                print(f"Pulling {model_name}: {chunk['status']}")

        return True

    except Exception as e:

        logging.error(f"Failed to download {model_name}: {e}")

        return False





# Lazy defaults — avoid blocking startup on Ollama/LM Studio probes.

RECOMMENDED_MODELS: dict[str, str] = {role: "" for role in ROLES.keys()}


