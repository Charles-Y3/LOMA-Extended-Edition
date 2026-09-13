# -*- coding: utf-8 -*-
"""Per-model quality mode (Low/High) and resolution preference for image generation,
saved in Model Library.

Mirrors services/inference/defaults.py's per-model settings pattern (own settings.json
bucket, keyed by model id) — kept separate since these are image-pipeline preferences
(quality mode / resolution preset name), not LLM inference knobs (num_ctx/thinking/max_tokens)."""
from __future__ import annotations

_VALID_QUALITY_MODES = ("low", "high")
_VALID_RESOLUTION_PRESETS = ("square", "portrait", "landscape")


def get_image_model_prefs(model_id: str) -> dict[str, str]:
    """{"quality_mode": ..., "resolution_preset": ...}, empty values when unset (callers
    fall back to the model's own auto-resolved defaults)."""
    name = (model_id or "").strip()
    if not name:
        return {"quality_mode": "", "resolution_preset": ""}
    try:
        from services.session import state

        bucket = (state.current_settings or {}).get("image_model_prefs") or {}
        raw = bucket.get(name) if isinstance(bucket, dict) else None
        if isinstance(raw, dict):
            mode = str(raw.get("quality_mode") or "").strip().lower()
            res = str(raw.get("resolution_preset") or "").strip().lower()
            return {
                "quality_mode": mode if mode in _VALID_QUALITY_MODES else "",
                "resolution_preset": res if res in _VALID_RESOLUTION_PRESETS else "",
            }
    except Exception:
        pass
    return {"quality_mode": "", "resolution_preset": ""}


def set_image_model_prefs(
    model_id: str, *, quality_mode: str | None = None, resolution_preset: str | None = None
) -> dict[str, str]:
    """Persist quality_mode and/or resolution_preset for one model id. Pass "" to clear
    a preference back to auto."""
    name = (model_id or "").strip()
    if not name:
        return {"quality_mode": "", "resolution_preset": ""}

    from services.session import state
    from services.session import settings as session_settings

    current = get_image_model_prefs(name)
    if quality_mode is not None:
        m = quality_mode.strip().lower()
        current["quality_mode"] = m if m in _VALID_QUALITY_MODES else ""
    if resolution_preset is not None:
        r = resolution_preset.strip().lower()
        current["resolution_preset"] = r if r in _VALID_RESOLUTION_PRESETS else ""

    settings = state.current_settings
    bucket = settings.setdefault("image_model_prefs", {})
    if not isinstance(bucket, dict):
        bucket = {}
        settings["image_model_prefs"] = bucket
    bucket[name] = current
    session_settings.save_settings(settings, quiet=True)
    return current


# Model catalog "tier" is a hardware/catalog ranking (fast=Realistic Vision, lowest
# footprint; balanced=DreamShaper 8; quality=SDXL Lightning, highest footprint and
# best overall ceiling) — decoupled from generation quality mode, which is the separate
# per-request Low/High quality_mode. Both axes still matter for the "not satisfied?"
# hint: a "fast"-tier model in Low mode has two levers left to pull (switch to High,
# or switch to a higher-tier model); the "quality"-tier model in High mode has none.
_TIER_RANK = {"fast": 1, "balanced": 2, "quality": 3, "flux": 4}
_MODE_RANK = {"low": 1, "high": 2}


def build_image_regen_hint(model_id: str, quality_mode: str) -> str:
    """Context-aware "not satisfied?" follow-up for a generated image — considers which
    model tier and quality mode actually produced it, instead of always pointing at
    Settings → Models even when that model is already the best one available (or the
    quality mode, not the model, is the actual lever left to pull)."""
    from pipeline.i18n import t as tr

    try:
        from config.model_catalog import catalog_entry

        entry = catalog_entry("image_generation", model_id) or {}
        model_tier = (entry.get("tier") or "").strip().lower()
    except Exception:
        model_tier = ""

    model_rank = _TIER_RANK.get(model_tier, 0)
    # No saved quality_mode resolves to "low" at generation time (see
    # resolve_image_presets() in image_generation.py) — match that here too.
    mode_rank = _MODE_RANK.get((quality_mode or "").strip().lower() or "low", 1)

    if not model_rank:
        # Unknown/custom model (e.g. LOMA_IMAGE_MODEL env override not in the catalog) —
        # no tier data to reason about, fall back to the generic hint.
        return tr("chat.image_regen_hint")

    model_maxed = model_rank >= max(_TIER_RANK.values())
    # A model with no Low/High axis at all (e.g. flux_klein_gguf — see
    # quality_modes_for_model()) has nothing left to "max out" on that dimension, so
    # treat it as already maxed rather than defaulting to mode_rank=1 and wrongly
    # suggesting a High-quality preset that doesn't exist for it.
    try:
        from services.image_generation import quality_modes_for_model

        has_quality_modes = bool(quality_modes_for_model(model_id))
    except Exception:
        has_quality_modes = True
    mode_maxed = (not has_quality_modes) or mode_rank >= max(_MODE_RANK.values())

    if model_maxed and mode_maxed:
        return tr("chat.image_regen_hint_best")
    if model_maxed and not mode_maxed:
        return tr("chat.image_regen_hint_try_quality_preset")
    if not model_maxed and mode_maxed:
        return tr("chat.image_regen_hint_try_model")
    return tr("chat.image_regen_hint_try_both")
