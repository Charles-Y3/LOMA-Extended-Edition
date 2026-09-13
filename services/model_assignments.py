# -*- coding: utf-8 -*-
"""Role assignments, model deletion, and post-delete reassignment."""
from __future__ import annotations

import logging

import config
from config.model_catalog import (
    entries_for_role,
    is_curated_vision_llm,
    recommend_three_tiers,
)
from services.session import settings as session_settings
from services.session import state
from services.system.profiler import SystemProfile, get_system_profile

logger = logging.getLogger(__name__)

ROLE_ORDER = ["Orchestrator", "Specialist", "General", "Verifier"]

DISCONTINUED_MODELS = frozenset({"granite3.2-vision:2b"})
DISCONTINUED_REPLACEMENT = "sorc/qwen3.5-instruct:2b"


def migrate_discontinued_models(settings: dict) -> bool:
    """Replace removed catalog models in assignments and vision defaults."""
    changed = False
    replacement = DISCONTINUED_REPLACEMENT

    def _is_discontinued(name: str) -> bool:
        val = (name or "").strip()
        if not val:
            return False
        return val in DISCONTINUED_MODELS or any(d in val for d in DISCONTINUED_MODELS)

    assignments = dict(settings.get("assignments") or {})
    for role in ROLE_ORDER:
        current = _normalize_model(assignments.get(role, ""))
        if _is_discontinued(current):
            assignments[role] = replacement
            changed = True
    if changed:
        settings["assignments"] = assignments

    for key in ("default_vision_model", "default_video_vision_model"):
        val = (settings.get(key) or "").strip()
        if _is_discontinued(val):
            settings[key] = replacement
            changed = True

    setup = dict(settings.get("setup") or {})
    assets = dict(setup.get("installed_assets") or {})
    for asset_key, val in list(assets.items()):
        if _is_discontinued(str(val or "")):
            assets[asset_key] = replacement
            changed = True
    if assets != setup.get("installed_assets"):
        setup["installed_assets"] = assets
        settings["setup"] = setup
        changed = True

    if changed:
        config.sync_roles(settings.get("assignments") or {})
    return changed


def normalize_role_assignments(assignments: dict | None) -> dict[str, str]:
    """Map legacy Coordinator/Coder slots to Specialist; keep only current ROLES keys."""
    raw = dict(assignments or {})
    specialist = _normalize_model(raw.get("Specialist", ""))
    if not specialist:
        specialist = _normalize_model(raw.get("Coordinator", "")) or _normalize_model(raw.get("Coder", ""))
    out = {role: "" for role in config.ROLES}
    for role in config.ROLES:
        val = _normalize_model(raw.get(role, ""))
        if val:
            out[role] = val
    if specialist and not out.get("Specialist"):
        out["Specialist"] = specialist
    return out


def _normalize_model(value) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("name") or "").strip()
    return str(value or "").strip()


def _installed_match(model_name: str, installed: list[str]) -> str | None:
    name = (model_name or "").strip()
    if not name:
        return None
    if name in installed:
        return name
    for candidate in installed:
        if name in candidate or candidate in name:
            return candidate
    return None


def has_usable_chat_model() -> bool:
    """True when an installed Ollama model is available for chat."""
    installed = config.get_installed_models()
    if not installed:
        return False
    try:
        from pipeline.base import profile_pack as profile_manager
        from pipeline.base.profile_pack import default_profile
        from services.model_router import resolve_general_model

        profile_id = (state.current_settings or {}).get("active_profile", "")
        if profile_manager.is_no_profile(profile_id):
            prof = default_profile("none")
        else:
            prof = profile_manager.load_profile(profile_id) or default_profile(profile_id)
        model = str(resolve_general_model(prof) or "").strip()
    except Exception:
        model = ""
    if not model:
        return False
    return _installed_match(model, installed) is not None


def models_referencing(settings: dict, model_name: str) -> list[str]:
    refs: list[str] = []
    name = (model_name or "").strip()
    if not name:
        return refs
    for role in ROLE_ORDER:
        val = _normalize_model((settings.get("assignments") or {}).get(role, ""))
        if val == name or name in val or val in name:
            refs.append(role)
    for key in ("default_vision_model", "default_video_vision_model"):
        val = (settings.get(key) or "").strip()
        if val == name or name in val or val in name:
            refs.append(key)
    return refs


def _best_installed_for_role(role: str, installed: list[str], profile: SystemProfile) -> str:
    pool = entries_for_role(role, profile)
    for entry in pool:
        matched = _installed_match(entry["name"], installed)
        if matched:
            return matched
    curated = [m for m in installed if is_curated_vision_llm(m)]
    if curated:
        if role == "Orchestrator":
            curated.sort(key=len, reverse=True)
            return curated[0]
        if role == "General":
            curated.sort(key=len)
            return curated[0]
        return curated[0]
    return installed[0] if installed else ""


def get_smart_assignments(
    installed: list[str] | None = None,
    profile: SystemProfile | None = None,
) -> dict[str, str]:
    if installed is None:
        installed = config.get_installed_models()
    profile = profile or get_system_profile()
    mapping = {role: "" for role in config.ROLES.keys()}
    if not installed:
        return mapping

    tiers = recommend_three_tiers("vision_llm", profile)
    orchestrator = _installed_match((tiers.get("quality") or {}).get("name", ""), installed)
    general = ""
    for entry in entries_for_role("General", profile):
        matched = _installed_match(entry["name"], installed)
        if matched:
            general = matched
            break
    if not general:
        general = _best_installed_for_role("General", installed, profile)
    worker = _installed_match((tiers.get("recommended") or {}).get("name", ""), installed)

    if not orchestrator:
        orchestrator = _best_installed_for_role("Orchestrator", installed, profile)
    if not general:
        general = _best_installed_for_role("General", installed, profile)
    if not worker:
        worker = orchestrator or general or installed[0]

    if len(installed) == 1:
        only = installed[0]
        return {role: only for role in mapping}

    mapping["Orchestrator"] = orchestrator or worker
    mapping["General"] = general or worker
    mapping["Specialist"] = worker
    mapping["Verifier"] = _installed_match((tiers.get("recommended") or {}).get("name", ""), installed) or worker
    return mapping


def assign_starter_bundle(
    general: str,
    *,
    coordinator: str | None = None,
    orchestrator: str | None = None,
    save: bool = True,
) -> None:
    """First-run: tier-1 General plus optional tier-2 workers and tier-3 Orchestrator."""
    general_name = (general or "").strip()
    if not general_name:
        return
    worker = (coordinator or "").strip() or general_name
    orch = (orchestrator or "").strip() or worker

    assignments = normalize_role_assignments(
        {
            "General": general_name,
            "Specialist": worker,
            "Verifier": worker,
            "Orchestrator": orch,
        }
    )
    state.current_settings["assignments"] = assignments
    state.current_settings["default_vision_model"] = general_name
    state.current_settings["default_video_vision_model"] = general_name
    config.sync_roles(assignments)
    setup = dict(state.current_settings.get("setup") or {})
    assets = dict(setup.get("installed_assets") or {})
    assets["vision_llm"] = general_name
    if coordinator:
        assets["vision_llm_tier2"] = worker
    if orchestrator:
        assets["vision_llm_tier3"] = orch
    setup["installed_assets"] = assets
    state.current_settings["setup"] = setup
    if save:
        session_settings.save_settings(state.current_settings, quiet=True)


def assign_primary_vlm(model_name: str, *, save: bool = True) -> None:
    """First-run: one multimodal model for all roles and vision defaults."""
    name = (model_name or "").strip()
    if not name:
        return
    assignments = {role: name for role in config.ROLES}
    state.current_settings["assignments"] = assignments
    state.current_settings["default_vision_model"] = name
    state.current_settings["default_video_vision_model"] = name
    config.sync_roles(assignments)
    setup = dict(state.current_settings.get("setup") or {})
    assets = dict(setup.get("installed_assets") or {})
    assets["vision_llm"] = name
    setup["installed_assets"] = assets
    state.current_settings["setup"] = setup
    if save:
        session_settings.save_settings(state.current_settings, quiet=True)


def assign_role_model(role: str, model_name: str, *, save: bool = True) -> None:
    name = (model_name or "").strip()
    if not name or role not in config.ROLES:
        return
    installed = config.get_installed_models()
    matched = _installed_match(name, installed)
    if matched:
        name = matched
    assignments = dict(state.current_settings.get("assignments") or {})
    assignments[role] = name
    state.current_settings["assignments"] = assignments
    config.sync_roles({role: name})
    if role in ("General", "Orchestrator") or not state.current_settings.get("default_vision_model"):
        state.current_settings["default_vision_model"] = name
        state.current_settings["default_video_vision_model"] = name
    if save:
        session_settings.save_settings(state.current_settings, quiet=True)


def reassign_after_delete(deleted_name: str) -> None:
    deleted = (deleted_name or "").strip()
    settings = state.current_settings
    installed = [m for m in config.get_installed_models(force_refresh=True) if m != deleted]
    profile = get_system_profile(refresh=True)
    smart = get_smart_assignments(installed, profile)

    assignments = dict(settings.get("assignments") or {})
    for role in ROLE_ORDER:
        current = _normalize_model(assignments.get(role, ""))
        if not current or current == deleted or deleted in current or current in deleted:
            assignments[role] = smart.get(role, "")

    settings["assignments"] = assignments
    config.sync_roles(assignments)

    for key in ("default_vision_model", "default_video_vision_model"):
        val = (settings.get(key) or "").strip()
        if not val or val == deleted or deleted in val or val in deleted:
            vision_pool = [m for m in installed if is_curated_vision_llm(m)]
            settings[key] = vision_pool[0] if vision_pool else assignments.get("General", "")

    session_settings.save_settings(settings, quiet=True)


def delete_ollama_model(model_name: str) -> tuple[bool, str]:
    name = (model_name or "").strip()
    if not name:
        return False, "No model name"
    try:
        from services.providers.registry import get_active_provider

        provider = get_active_provider()
        if provider and hasattr(provider, "delete_model"):
            provider.delete_model(name)
        elif provider is None or provider.provider_id == "ollama":
            import ollama

            ollama.delete(name)
        else:
            # Don't fall through to Ollama's own delete API for a different backend
            # (e.g. LM Studio) — the name almost certainly doesn't exist there, and on
            # the rare chance it does (both backends running, coincidental name match)
            # this would delete the wrong model entirely.
            from pipeline.i18n import t as tr

            return False, tr("config.delete_unsupported", label=provider.label)
        config.invalidate_models_cache()
        config.get_installed_models(force_refresh=True)
        reassign_after_delete(name)
        try:
            from services.model_router import _vision_capability_cache

            _vision_capability_cache.clear()
        except Exception:
            pass
        return True, f"Removed {name}"
    except Exception as exc:
        logger.error("delete_ollama_model failed: %s", exc)
        return False, str(exc)


def delete_image_checkpoint(repo_id: str) -> tuple[bool, str]:
    repo = (repo_id or "").strip()
    if not repo:
        return False, "No checkpoint id"
    try:
        from huggingface_hub import scan_cache_dir

        from config.model_catalog import image_lcm_unet_id

        repo_ids = {repo}
        lcm_unet_id = image_lcm_unet_id(repo)
        if lcm_unet_id:
            repo_ids.add(lcm_unet_id)

        cache_info = scan_cache_dir()
        hashes: list[str] = []
        for repo_info in cache_info.repos:
            if repo_info.repo_id in repo_ids:
                hashes.extend(rev.commit_hash for rev in repo_info.revisions)
        if not hashes:
            return False, f"Checkpoint not in cache: {repo}"
        cache_info.delete_revisions(*hashes).execute()
        current = (state.current_settings.get("default_image_model") or "").strip()
        if current == repo:
            profile = get_system_profile()
            tiers = recommend_three_tiers("image_generation", profile)
            nxt = (tiers.get("recommended") or {}).get("name", "")
            state.current_settings["default_image_model"] = nxt
            session_settings.save_settings(state.current_settings, quiet=True)
        return True, f"Removed {repo} from HuggingFace cache"
    except Exception as exc:
        logger.error("delete_image_checkpoint failed: %s", exc)
        return False, str(exc)


def custom_installed_models(installed: list[str] | None = None) -> list[str]:
    if installed is None:
        installed = config.get_installed_models()
    return [name for name in installed if not is_curated_vision_llm(name)]
