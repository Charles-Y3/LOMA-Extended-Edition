# -*- coding: utf-8 -*-
import json
import os

from nicegui import ui

import config
from pipeline.i18n import normalize_locale
from services.persistence import migrate, register, stamp
from services.session import state
from pipeline.i18n import t as _tr  # noqa: E402

# Persistence spine (docs/PIPELINE_REFACTOR.md §0.5 #1). v1 folds in the Document
# Intelligence → Knowledge Vault rename: an existing Extended settings.json lists the
# extension under its old id in installed/disabled_extensions, so remap it (matching the
# extension-folder rename) or the extension's enabled state is silently lost.
_SETTINGS_STORE = "settings"


def _settings_v1(data: dict) -> dict:
    for key in ("installed_extensions", "disabled_extensions"):
        ids = data.get(key)
        if isinstance(ids, list):
            data[key] = [
                "knowledge_vault" if str(i) == "document_intelligence" else i for i in ids
            ]
    for old, new in (
        ("highlight_use_di", "highlight_use_kv"),
        ("highlight_di_mode", "highlight_kv_mode"),
        ("highlight_di_scope", "highlight_kv_scope"),
        ("highlight_di_library_id", "highlight_kv_library_id"),
    ):
        if new not in data and old in data:
            data[new] = data[old]
    return data


register(_SETTINGS_STORE, 1, _settings_v1)


def save_settings(data: dict, *, quiet: bool = False) -> None:
    if "language" in data:
        data["language"] = normalize_locale(data.get("language"))
    stamp(_SETTINGS_STORE, data)
    path = state.SETTINGS_FILE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4)
            f.flush()
            os.fsync(f.fileno())
        config.sync_roles(data.get("assignments", {}))
        if not quiet:
            ui.notify(_tr("notify.settings_saved"), color="positive", pos="bottom-right", icon="save")
    except Exception as e:
        if not quiet:
            ui.notify(_tr("notify.save_failed", error=e), color="negative")


_DEFAULT_ENABLED_EXTENSIONS = frozenset({
    "chat_archive_manager",
    "document_editor",
    "knowledge_vault",
    "token_tracker",
    "web_viewer",
    "history_events",
    "research",
    "news_brief",
})


def _default_disabled_extensions() -> list[str]:
    """Fresh installs enable only the core extension set; all other catalog entries start
    disabled until the user turns them on. Existing installs are untouched — this only feeds
    the `defaults` dict below, which is overridden by an already-saved settings.json.

    Reads extension_catalog.json directly rather than services.plugins.catalog's merged/
    cached view: this runs during load_settings(), which happens before
    extension_registry.discover() populates the registry."""
    try:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "extension_catalog.json")
        with open(os.path.normpath(path), encoding="utf-8") as f:
            rows = json.load(f).get("extensions") or []
        all_ids = {str(row.get("id")) for row in rows if row.get("id")}
        return sorted(all_ids - _DEFAULT_ENABLED_EXTENSIONS)
    except Exception:
        from pipeline.registry.catalog import BUILTIN_EXTENSION_IDS

        return sorted(BUILTIN_EXTENSION_IDS - _DEFAULT_ENABLED_EXTENSIONS)


def load_settings() -> dict:
    from config.model_catalog import MODEL_CATALOG

    vision_default = ""
    if MODEL_CATALOG.get("vision_llm"):
        vision_default = MODEL_CATALOG["vision_llm"][0]["name"]
    image_default = ""
    if MODEL_CATALOG.get("image_generation"):
        image_default = MODEL_CATALOG["image_generation"][0]["name"]

    empty_assignments = {role: "" for role in config.ROLES.keys()}
    defaults = {
        "assignments": dict(empty_assignments),
        "theme": config.THEME_DEFAULT,
        "language": config.LANGUAGE_DEFAULT,
        "active_profile": "none",
        "execution_mode": "direct",
        "chat_execution_mode": "direct",
        "installed_extensions": [],
        "disabled_extensions": _default_disabled_extensions(),
        "default_vision_model": vision_default,
        "default_video_vision_model": vision_default,
        "default_image_model": image_default,
        "transcription_backend": "auto",
        "default_whisper_model": "base",
        "default_live_dictation_engine": "sensevoice",
        "default_voice_language": "auto",
        "traditional_chinese": True,
        "default_output_format": "chat",
        "upload_retention_days": 7,
        "web_grounding_enabled": False,
        "voice_reply_enabled": False,
        "tts_voice_id": "en",
        "tts_rate": "+0%",
        "chat_font_px": 13,
        "console_detail": "simple",
        "setup": {
            "completed": False,
            "provider": "none",
            "provider_url": "",
            "skipped_steps": [],
            "installed_assets": {},
            "language_picked": False,
        },
        "inference_defaults": {
            "enable_thinking": False,
            "num_ctx": 4096,
            "max_tokens": 4096,
        },
        "model_inference": {},
    }
    path = state.SETTINGS_FILE
    if os.path.exists(path) and os.path.getsize(path) > 0:
        try:
            with open(path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            # Run the central migration chain (DI → KV rename) before reading the blob.
            loaded = migrate(_SETTINGS_STORE, loaded)
            if "assignments" in loaded:
                defaults["assignments"].update(loaded["assignments"])
            from services.model_assignments import normalize_role_assignments

            defaults["assignments"] = normalize_role_assignments(defaults["assignments"])
            defaults["active_profile"] = loaded.get("active_profile", defaults["active_profile"])
            defaults["theme"] = loaded.get("theme", defaults["theme"])
            defaults["language"] = normalize_locale(
                loaded.get("language", defaults["language"])
            )
            from pipeline.execution_modes.constants import normalize_execution_mode

            raw_mode = loaded.get("execution_mode") or loaded.get("chat_execution_mode")
            defaults["execution_mode"] = normalize_execution_mode(
                str(raw_mode or defaults["execution_mode"])
            )
            defaults["chat_execution_mode"] = defaults["execution_mode"]
            defaults["installed_extensions"] = list(loaded.get("installed_extensions") or [])
            defaults["disabled_extensions"] = list(loaded.get("disabled_extensions") or [])
            defaults["default_vision_model"] = loaded.get(
                "default_vision_model", defaults["default_vision_model"]
            )
            defaults["default_video_vision_model"] = loaded.get(
                "default_video_vision_model",
                loaded.get("default_vision_model", defaults["default_video_vision_model"]),
            )
            defaults["default_image_model"] = loaded.get(
                "default_image_model", defaults["default_image_model"]
            )
            defaults["transcription_backend"] = loaded.get(
                "transcription_backend", defaults["transcription_backend"]
            )
            defaults["default_voice_language"] = loaded.get(
                "default_voice_language", defaults["default_voice_language"]
            )
            defaults["traditional_chinese"] = bool(
                loaded.get("traditional_chinese", defaults["traditional_chinese"])
            )
            from services.media_transcription import normalize_whisper_model

            defaults["default_whisper_model"] = normalize_whisper_model(
                loaded.get("default_whisper_model", defaults["default_whisper_model"])
            )
            live_engine = str(
                loaded.get("default_live_dictation_engine", defaults["default_live_dictation_engine"])
            ).strip().lower()
            if live_engine not in ("sensevoice", "none"):
                live_engine = defaults["default_live_dictation_engine"]
            defaults["default_live_dictation_engine"] = live_engine
            defaults["default_output_format"] = loaded.get(
                "default_output_format", defaults["default_output_format"]
            )
            try:
                defaults["upload_retention_days"] = max(
                    0, int(loaded.get("upload_retention_days", defaults["upload_retention_days"]))
                )
            except (TypeError, ValueError):
                pass
            defaults["web_grounding_enabled"] = bool(loaded.get("web_grounding_enabled", False))
            defaults["voice_reply_enabled"] = bool(loaded.get("voice_reply_enabled", False))
            defaults["tts_voice_id"] = loaded.get("tts_voice_id", defaults["tts_voice_id"])
            defaults["tts_rate"] = loaded.get("tts_rate", defaults["tts_rate"])
            console_detail = str(loaded.get("console_detail") or "simple").strip().lower()
            defaults["console_detail"] = console_detail if console_detail in ("simple", "detailed") else "simple"
            try:
                defaults["chat_font_px"] = max(
                    11, min(20, int(loaded.get("chat_font_px", defaults["chat_font_px"])))
                )
            except (TypeError, ValueError):
                pass
            if "setup" in loaded:
                defaults["setup"].update(loaded["setup"])
            from services.inference.defaults import normalize_inference_defaults

            defaults["inference_defaults"] = normalize_inference_defaults(
                loaded.get("inference_defaults", defaults.get("inference_defaults"))
            )
            raw_model_inf = loaded.get("model_inference", {})
            if isinstance(raw_model_inf, dict):
                defaults["model_inference"] = {
                    str(k): normalize_inference_defaults(v)
                    for k, v in raw_model_inf.items()
                    if isinstance(v, dict)
                }
            else:
                defaults["model_inference"] = {}
            from pipeline.base import profile_pack as profile_manager

            defaults["active_profile"] = profile_manager.resolve_active_profile_id(
                defaults.get("active_profile")
            )
            config.sync_roles(defaults["assignments"])
            from services.model_assignments import migrate_discontinued_models

            if migrate_discontinued_models(defaults):
                save_settings(defaults, quiet=True)
            from services.providers.registry import resolve_inference_backend

            resolve_inference_backend()
            # Everything above is an explicit, validated allowlist of known settings
            # keys — anything else actually on disk (simple extension-owned prefs like
            # image_model_prefs or web_viewer_history) was silently dropped here on
            # every load, even though save_settings() had written it correctly. Carry
            # forward any key not already handled above so those survive a restart.
            for key, value in loaded.items():
                if key not in defaults:
                    defaults[key] = value
            return defaults
        except Exception as e:
            print(f"Error reading settings: {e}")
    defaults["assignments"] = config.get_recommended_models().copy()
    from pipeline.base import profile_pack as profile_manager

    defaults["active_profile"] = profile_manager.resolve_active_profile_id(
        defaults.get("active_profile")
    )
    save_settings(defaults, quiet=True)
    from services.providers.registry import resolve_inference_backend

    resolve_inference_backend()
    return defaults
