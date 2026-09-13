# core/model_router.py
# -*- coding: utf-8 -*-
"""Resolves Ollama model names dynamically from model capabilities and configuration profiles."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

import config
from config import ROLES, get_installed_models
from config.model_catalog import MODEL_CATALOG, _entry_fits, hardware_tag, recommend_three_tiers
from services.system.profiler import SystemProfile, get_system_profile

_IMAGE_TIER_SORT = {"fast": 0, "balanced": 1, "quality": 2, "flux": 3}

_vision_capability_cache: dict[str, bool] = {}
_thinking_capability_cache: dict[str, bool] = {}


class NoChatModelError(RuntimeError):
    """No usable chat model — no local LLM backend reachable, or none downloaded yet.

    Raised by build_chat_request() (services/inference/ollama_chat.py) as the last
    backstop before an empty model name would otherwise reach the Ollama/LM Studio
    client and fail with an opaque client-library validation error. Every caller —
    the main chat pipeline and any extension that calls through the shared chat
    helpers — gets a translated, user-facing message instead."""


def _name_looks_like_embedding(name: str) -> bool:
    lower = (name or "").lower()
    return "embed" in lower


def chat_capable_models(names: list[str]) -> list[str]:
    """Filter out models that clearly aren't chat-capable (text-embedding models)
    from a list of installed names, before offering them as a General/Vision/
    translation-model choice. Embedding models don't do text generation at all —
    LM Studio's model list mixes them in with chat models with no reliable "type"
    field to distinguish via the API, so this is a name heuristic like the
    vision/audio/thinking ones in this module, not a guarantee."""
    return [n for n in names if not _name_looks_like_embedding(n)]


def resolve_general_model(profile: dict) -> str:
    """
    Looks up the base linguistic model from the user profile.
    Falls back to the first installed Ollama model when assignments are missing.
    """
    installed = get_installed_models()

    def _pick(name: str) -> str:
        from services.model_assignments import _installed_match

        candidate = str(name or "").strip()
        if not candidate:
            return ""
        if installed:
            matched = _installed_match(candidate, installed)
            return matched or ""
        return candidate

    model_cfg = profile.get("MODEL", {})
    preferred = _pick(model_cfg.get("preferred_llm"))
    if preferred:
        return preferred
    general = _pick(ROLES.get("General"))
    if general:
        return general
    try:
        from services.session import state

        assignments = (state.current_settings or {}).get("assignments") or {}
        raw = assignments.get("General", "")
        assigned = raw.get("label") if isinstance(raw, dict) else str(raw or "")
        assigned = _pick(assigned)
        if assigned:
            return assigned
    except Exception:
        pass
    # A lone embedding model (no real chat assignment yet) shouldn't get silently
    # picked as the default chat model — it can't do text generation.
    chat_installed = chat_capable_models(installed)
    if chat_installed:
        return chat_installed[0]
    if installed:
        return installed[0]
    return ""


def needs_vision(llm_type: str, images: list) -> bool:
    """Checks whether the incoming workflow requires a multimodal engine."""
    return llm_type == "image" or len(images) > 0


def _name_suggests_thinking(model_name: str) -> bool:
    lower = (model_name or "").lower()
    return any(
        k in lower
        for k in (
            "qwen3.5",
            "qwen3-vl",
            "qwq",
            "deepseek-r1",
            "thinking",
        )
    )


def model_supports_thinking(model_name: str) -> bool:
    """True when Ollama reports native thinking support for this model, or (for a
    non-Ollama backend, where ollama.show() always fails) the name matches a known
    thinking-model family — same name-heuristic-as-fallback pattern already used by
    check_model_has_vision/check_model_supports_audio below."""
    if not model_name:
        return False
    cached = _thinking_capability_cache.get(model_name)
    if cached is not None:
        return cached
    try:
        import ollama

        info = ollama.show(model_name)
        capabilities = info.get("capabilities", []) or []
        supports = "thinking" in capabilities or any(
            str(capability).lower() == "thinking" for capability in capabilities
        )
    except Exception:
        supports = _name_suggests_thinking(model_name)
    _thinking_capability_cache[model_name] = supports
    return supports


def check_model_has_vision(model_name: str) -> bool:
    """
    Programmatically queries Ollama to inspect a model's true capabilities.
    Returns True if the model explicitly declares 'vision' support or includes a visual projector.
    """
    if not model_name:
        return False
    cached = _vision_capability_cache.get(model_name)
    if cached is not None:
        return cached
    try:
        import ollama
        info = ollama.show(model_name)

        # 1. Check Ollama's native capabilities array
        capabilities = info.get("capabilities", []) or []
        if "vision" in capabilities or any(str(c).lower() == "vision" for c in capabilities):
            return _mark_vision_capable(model_name)

        details = info.get("details", {}) or {}
        families = details.get("families", []) or []
        if any("vision" in str(f).lower() for f in families):
            return _mark_vision_capable(model_name)

        model_info = info.get("model_info", {}) or {}
        if any("projector" in str(k).lower() for k in model_info.keys()):
            return _mark_vision_capable(model_name)

    except Exception:
        pass
    _vision_capability_cache[model_name] = False
    return False


def _mark_vision_capable(model_name: str) -> bool:
    _vision_capability_cache[model_name] = True
    return True


def _name_looks_vision(model_name: str) -> bool:
    lower = model_name.lower()
    return any(
        k in lower
        for k in (
            "vision",
            "vl",
            "llava",
            "moondream",
            "minicpm",
            "gemma4",
            "qwen3.5",
            "qwen3-vl",
            "qwen2.5-vl",
            "qwen-vl",
        )
    )


_audio_capability_cache: dict[str, bool] = {}


def check_model_supports_audio(model_name: str) -> bool:
    """True when Ollama reports audio capability or model name suggests omni/audio."""
    if not model_name:
        return False
    cached = _audio_capability_cache.get(model_name)
    if cached is not None:
        return cached
    lower = model_name.lower()
    if any(k in lower for k in ("gemma3", "gemma4", "qwen2.5-omni", "qwen-omni", "audio", "omni")):
        return _mark_audio_capable(model_name)
    try:
        import ollama

        info = ollama.show(model_name)
        capabilities = info.get("capabilities", []) or []
        if "audio" in capabilities or any(str(c).lower() == "audio" for c in capabilities):
            return _mark_audio_capable(model_name)
    except Exception:
        pass
    _audio_capability_cache[model_name] = False
    return False


def _mark_audio_capable(model_name: str) -> bool:
    _audio_capability_cache[model_name] = True
    return True


def needs_audio(context_files: list) -> bool:
    for f in context_files or []:
        if isinstance(f, dict) and f.get("type") in ("media_audio", "media_video"):
            return True
    return False


def get_vision_capable_models(*, probe: bool = True) -> list[str]:
    """Installed models that can analyze images."""
    capable: list[str] = []
    for name in get_installed_models():
        if (probe and check_model_has_vision(name)) or _name_looks_vision(name):
            if name not in capable:
                capable.append(name)
    return capable


def get_default_vision_model_from_settings(settings: dict | None = None) -> str | None:
    """User-selected vision default from system settings, if installed and capable."""
    if settings is None:
        try:
            from services.session import state

            settings = state.current_settings or {}
        except Exception:
            settings = {}
    preferred = (settings.get("default_vision_model") or "").strip()
    if not preferred:
        return None
    installed = get_installed_models()
    matched = preferred
    if installed:
        from services.model_assignments import _installed_match

        matched = _installed_match(preferred, installed) or ""
        if not matched:
            return None
    if _name_looks_vision(matched):
        return matched
    if check_model_has_vision(matched):
        return matched
    return None


def get_default_video_vision_model_from_settings(settings: dict | None = None) -> str | None:
    """Vision model for video frame understanding (falls back to image vision default)."""
    if settings is None:
        try:
            from services.session import state

            settings = state.current_settings or {}
        except Exception:
            settings = {}
    preferred = (settings.get("default_video_vision_model") or "").strip()
    if preferred:
        installed = get_installed_models()
        if preferred in installed and (
            check_model_has_vision(preferred) or _name_looks_vision(preferred)
        ):
            return preferred
    return get_default_vision_model_from_settings(settings)


def find_video_vision_model() -> str | None:
    """Vision model for video understanding (settings → capable installed list)."""
    preferred = get_default_video_vision_model_from_settings()
    if preferred:
        return preferred
    capable = get_vision_capable_models()
    return capable[0] if capable else None


def _local_checkpoint_exists(model_id: str) -> bool:
    path = Path(model_id)
    if not path.exists():
        return False
    if path.is_file():
        return path.suffix.lower() in {".safetensors", ".ckpt", ".bin"}
    return path.is_dir()


def _image_catalog_repo_cached(repo_id: str) -> bool | None:
    """Pipeline-kind-aware cache check for an image_generation catalog entry — None when
    repo_id isn't a catalog entry (or is a "sd15"/plain "sdxl" one), so the caller falls
    back to the generic multi-file-repo heuristic below."""
    from config.model_catalog import (
        catalog_entry,
        image_checkpoint_files,
        image_gguf_spec,
        image_lcm_unet_id,
        image_pipeline_kind,
    )

    if not catalog_entry("image_generation", repo_id):
        return None
    kind = image_pipeline_kind(repo_id)
    if kind == "flux_klein_gguf":
        # "Downloaded" means both GGUF weight files are cached, plus the base repo's
        # small config-only files (model_index.json etc.) — see download_image_model()'s
        # flux_klein_gguf branch for what actually gets fetched from each repo.
        spec = image_gguf_spec(repo_id)
        if not spec:
            return False
        try:
            from huggingface_hub import try_to_load_from_cache

            transformer_cached = (
                try_to_load_from_cache(spec["gguf_repo"], spec["gguf_file"]) is not None
            )
            encoder_cached = (
                try_to_load_from_cache(spec["text_encoder_repo"], spec["text_encoder_file"])
                is not None
            )
            return (
                transformer_cached
                and encoder_cached
                and huggingface_repo_cached_generic(repo_id)
            )
        except Exception:
            return False
    if kind == "sdxl_lightning":
        # Loose single-file checkpoints — no model_index.json to find, check every file
        # this entry needs (its repo also hosts unrelated step/format variants). Both the
        # low (4-step) and high (8-step) checkpoint must be cached for "downloaded" to be
        # true, since switching quality mode later must not require a fresh download.
        filenames = list(image_checkpoint_files(repo_id).values())
        if not filenames:
            return False
        try:
            from huggingface_hub import try_to_load_from_cache

            return all(try_to_load_from_cache(repo_id, f) is not None for f in filenames)
        except Exception:
            return False
    lcm_unet_id = image_lcm_unet_id(repo_id)
    if lcm_unet_id:
        # "Downloaded" should mean ready to actually generate, i.e. both the base pipeline
        # and its distilled-UNet swap are cached — not just one of the two.
        return huggingface_repo_cached_generic(repo_id) and huggingface_repo_cached_generic(
            lcm_unet_id
        )
    return None


def huggingface_repo_cached_generic(repo_id: str) -> bool:
    """Standard multi-file diffusers repo cache check (model_index.json etc.) — the part
    of huggingface_repo_cached() that doesn't need catalog awareness, split out so
    _image_catalog_repo_cached() can reuse it for a sub-repo (e.g. an lcm_unet_id)."""
    if _local_checkpoint_exists(repo_id):
        return True
    if "/" not in repo_id.replace("\\", "/"):
        return False
    try:
        from huggingface_hub import try_to_load_from_cache

        for filename in (
            "model_index.json",
            "scheduler/scheduler_config.json",
            "unet/diffusion_pytorch_model.safetensors",
        ):
            if try_to_load_from_cache(repo_id, filename) is not None:
                return True
        from huggingface_hub.constants import HF_HUB_CACHE

        cache_dir = Path(HF_HUB_CACHE) / ("models--" + repo_id.replace("/", "--"))
        if cache_dir.is_dir() and any(cache_dir.rglob("*.json")):
            return True
    except Exception:
        pass
    return False


def huggingface_repo_cached(repo_id: str) -> bool:
    """True when a Diffusers checkpoint is on disk (local path or HF hub cache)."""
    special = _image_catalog_repo_cached(repo_id)
    if special is not None:
        return special
    return huggingface_repo_cached_generic(repo_id)


def delete_cached_hf_model(repo_id: str) -> tuple[bool, str]:
    """Remove a HuggingFace Hub-cached model (image checkpoint or faster-whisper size)
    using huggingface_hub's own cache API — never manually rmtree the cache folder, since
    the library handles blob reference-counting/symlinks across cached repos correctly."""
    try:
        from huggingface_hub import scan_cache_dir

        cache_info = scan_cache_dir()
        repo = next(
            (
                r
                for r in cache_info.repos
                if r.repo_id == repo_id and r.repo_type == "model"
            ),
            None,
        )
        if repo is None:
            return True, ""
        hashes = [rev.commit_hash for rev in repo.revisions]
        if not hashes:
            return True, ""
        cache_info.delete_revisions(*hashes).execute()
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _image_catalog_entries() -> list[dict]:
    """All enabled image_generation rows in fast → balanced → quality order."""
    entries = [
        e for e in MODEL_CATALOG.get("image_generation", []) if e.get("enabled", True) is not False
    ]
    return sorted(entries, key=lambda e: _IMAGE_TIER_SORT.get(e.get("tier", ""), 99))


def image_catalog_entries() -> list[dict]:
    """Public alias of _image_catalog_entries() — full catalog dicts (label/size/desc),
    for UI callers that need more than the name→label options get_image_generation_select_options()
    returns (e.g. the setup wizard's image-model picker, which shows each entry's desc)."""
    return _image_catalog_entries()


def _image_entry_display_label(entry: dict, profile: SystemProfile, *, deps_ok: bool, missing: str = "") -> str:
    name = entry["name"]
    label = entry.get("label", name)
    size = entry.get("size", "")
    if not deps_ok:
        detail = f"missing {missing}" if missing else "dependencies missing"
        return f"{label} ({detail})"
    hw = hardware_tag(entry, profile)
    cached = huggingface_repo_cached(name)
    # Only annotate when the checkpoint is already downloaded; say nothing otherwise.
    cache_note = " · downloaded" if cached else ""
    if not _entry_fits(entry, profile):
        return f"{label} ({size}) [{hw} — exceeds this machine]{cache_note}"
    return f"{label} ({size}) [{hw}]{cache_note}"


def list_runnable_image_generation_models() -> list[dict]:
    """Catalog entries that fit hardware and have image deps installed."""
    profile = get_system_profile()
    deps_ok, _ = image_generation_deps_available()
    if not deps_ok:
        return []

    out: list[dict] = []
    for entry in _image_catalog_entries():
        if not _entry_fits(entry, profile):
            continue
        name = entry["name"]
        out.append(
            {
                "name": name,
                "label": entry.get("label", name),
                "display": _image_entry_display_label(entry, profile, deps_ok=True),
                "cached": huggingface_repo_cached(name),
                "tier": entry.get("tier", ""),
            }
        )

    env_model = (os.environ.get("LOMA_IMAGE_MODEL") or "").strip()
    if env_model and not any(m["name"] == env_model for m in out):
        out.insert(
            0,
            {
                "name": env_model,
                "label": f"Environment ({env_model})",
                "display": f"Environment ({env_model})",
                "cached": huggingface_repo_cached(env_model),
                "tier": "balanced",
            },
        )
    return out


def _image_plain_label(entry: dict) -> str:
    """Bare catalog name with the ' — Tier' suffix stripped, e.g. 'Stable Diffusion 1.5'."""
    label = entry.get("label", entry.get("name", ""))
    return label.split(" — ")[0].strip()


def installed_image_generation_options(settings: dict | None = None) -> tuple[dict[str, str], str]:
    """Installed-only image-generation options (name -> plain label), matching the
    vision/whisper role dropdowns' pattern — no not-installed fallback. Used by the
    Roles tab's "Image generation" picker, which should only offer models actually
    usable right now. Unlike get_image_generation_select_options() (the Model
    Library's download list, which must show every catalog entry so the user can
    download new ones), a not-yet-downloaded model never appears here."""
    if settings is None:
        try:
            from services.session import state

            settings = state.current_settings or {}
        except Exception:
            settings = {}

    options: dict[str, str] = {}
    for entry in _image_catalog_entries():
        name = entry["name"]
        if huggingface_repo_cached(name):
            options[name] = _image_plain_label(entry)

    env_model = (os.environ.get("LOMA_IMAGE_MODEL") or "").strip()
    if env_model and env_model not in options and huggingface_repo_cached(env_model):
        options = {env_model: f"Environment ({env_model})", **options}

    current = (settings.get("default_image_model") or "").strip()
    if current not in options:
        current = next(iter(options), "")
    return options, current


def any_image_model_installed(settings: dict | None = None) -> bool:
    """True when at least one image-generation checkpoint is actually downloaded —
    unlike image_generation_deps_available() (diffusers/torch importable, says nothing
    about whether any model weights exist on disk). Callers that would otherwise trigger
    generate_image()'s silent first-use auto-download (of DreamShaper 8) should check
    this first and point the user at the setup wizard's image-model step or Settings →
    Model Library instead."""
    return bool(installed_image_generation_options(settings)[0])


def get_image_generation_select_options(
    settings: dict | None = None,
    *,
    plain: bool = False,
) -> tuple[dict[str, str], str]:
    """
    Settings dropdown: always lists every enabled catalog tier (fast / balanced / quality)
    with live hardware and cache status — not only models that fit this machine.

    When `plain` is True, option text is just the bare model name (no size/hardware/cache
    decoration) — used by the Models tab dropdown; the verbose detail now lives in the
    Downloads tab's image-generation card instead.
    """
    if settings is None:
        try:
            from services.session import state

            settings = state.current_settings or {}
        except Exception:
            settings = {}

    deps_ok, missing = image_generation_deps_available()
    profile = get_system_profile()
    current = (settings.get("default_image_model") or "").strip()

    options: dict[str, str] = {}
    for entry in _image_catalog_entries():
        options[entry["name"]] = (
            _image_plain_label(entry)
            if plain
            else _image_entry_display_label(entry, profile, deps_ok=deps_ok, missing=missing)
        )

    env_model = (os.environ.get("LOMA_IMAGE_MODEL") or "").strip()
    if env_model and env_model not in options:
        env_entry = {
            "name": env_model,
            "label": f"Environment ({env_model})",
            "size": "custom",
            "min_ram": 0,
            "min_vram": 0,
        }
        options = {
            env_model: (
                _image_plain_label(env_entry)
                if plain
                else _image_entry_display_label(env_entry, profile, deps_ok=deps_ok, missing=missing)
            ),
            **options,
        }

    if current and current in options:
        return options, current

    if env_model and env_model in options:
        return options, env_model

    tiers = recommend_three_tiers("image_generation", profile)
    for key in ("recommended", "balanced", "fast", "quality"):
        pick = tiers.get(key)
        if pick and pick["name"] in options:
            return options, pick["name"]

    if options:
        return options, next(iter(options))
    return options, current or "Lykon/dreamshaper-8"


def get_default_image_model_from_settings(settings: dict | None = None) -> str:
    _, current = get_image_generation_select_options(settings)
    if current:
        return current
    return "Lykon/dreamshaper-8"


def _progress_tqdm_class(on_percent):
    """tqdm subclass reporting byte-level progress to `on_percent` — mirrors
    services.media_transcription's _hf_download_progress, but huggingface_hub accepts
    tqdm_class directly so no monkeypatching is needed. Skips the outer "Fetching N
    files" file-count bar (unit != "B") so on_percent tracks actual bytes, not file
    counts — it resets to 0 per file for a multi-file snapshot_download, same as the
    console output, rather than a single smooth 0-100% sweep."""
    from tqdm import tqdm as _tqdm

    class _ProgressTqdm(_tqdm):
        def update(self, n=1):
            result = super().update(n)
            if self.total and self.unit == "B":
                on_percent(min(0.99, self.n / self.total))
            return result

    return _ProgressTqdm


# Diffusers pipeline files only. Real-world community checkpoints often also host
# standalone merged .safetensors at repo root (A1111/webui format, "modelspec", etc.)
# that from_pretrained() never reads — snapshot_download fetches everything by default,
# so e.g. segmind/SSD-1B balloons from its advertised ~7GB to ~25GB, and
# SG161222/Realistic_Vision_V6.0_B1_noVAE from ~5.5GB to ~18GB, without this filter.
# Both the fp16 and fp32 variant are kept (not just fp16) since CPU-only users load the
# fp32 files (see _load_pipeline's device-gated `variant` kwarg). *.bin is included too —
# some repos (e.g. Realistic Vision V6.0) ship subfolder component weights only as .bin,
# with no .safetensors variant at all; restricting to */*.safetensors alone would fetch
# zero usable weight files for those. The root-level merged checkpoints are still
# excluded either way since they never appear under a subfolder ("*/...").
# *.md/.gitattributes: near-zero-byte repo metadata, but some huggingface_hub/transformers
# loaders (AutoTokenizer in particular — see _local_snapshot_subfolder's docstring in
# services/image_generation.py) refuse `local_files_only=True` entirely if the *whole
# repo's* file list isn't satisfied locally, even for files that loader never reads.
# Fetching them here means the completeness check never has anything to complain about.
_IMAGE_MODEL_ALLOW_PATTERNS = [
    "*.json", "*.txt", "*/*.safetensors", "*/*.bin", "*.md", ".gitattributes",
]

# flux_klein_gguf entries: only the small config/VAE/tokenizer pieces come from the base
# repo — the transformer and text_encoder weights are the GGUF files fetched separately
# (see download_image_model()), so their full-precision */*.safetensors folders must be
# excluded here or this would silently re-download the ~23GB the GGUF swap exists to avoid.
_FLUX_KLEIN_CONFIG_ALLOW_PATTERNS = [
    "model_index.json",
    "scheduler/*.json",
    "vae/*.json",
    "vae/*.safetensors",
    "tokenizer/*",
    # config.json only — from_single_file(config=model_id, subfolder="transformer")
    # needs this to resolve the transformer's architecture; the actual weights come
    # from the separate GGUF file, never this subfolder's own (gated, full-precision)
    # safetensors.
    "transformer/config.json",
    # Repo-root metadata, not read by any loader — included only so a whole-repo
    # completeness check (AutoTokenizer.from_pretrained's local_files_only path hit
    # this exact gap; see _IMAGE_MODEL_ALLOW_PATTERNS's comment above) never has a
    # "missing" file to refuse offline loading over.
    "*.md",
    ".gitattributes",
]


def download_image_model(model_id: str, *, on_percent: Callable[[float], None] | None = None) -> None:
    """Fetch an image checkpoint's weights only (no pipeline load / device placement) —
    same lightweight `snapshot_download` pattern services.voice_input uses for SenseVoice.
    Safe to call from a background thread; raises on failure so callers can report it.

    Pipeline-kind aware: an sdxl_lightning entry's repo also hosts unrelated 1/2-step and
    UNet-only/LoRA variants (~40GB+ total) — snapshot_download-ing the whole repo would
    fetch all of them, so that kind fetches only the specific low (4-step) and high
    (8-step) checkpoint files it needs via hf_hub_download, one call per file so both
    quality modes are ready without a second download later.
    An sdxl entry with an lcm_unet_id additionally pre-fetches that distilled UNet repo
    so the swap in _load_pipeline() doesn't stall on first generation."""
    from config.model_catalog import (
        image_checkpoint_files,
        image_gguf_spec,
        image_lcm_unet_id,
        image_pipeline_kind,
    )
    from huggingface_hub import hf_hub_download, snapshot_download

    tqdm_class = _progress_tqdm_class(on_percent) if on_percent else None

    kind = image_pipeline_kind(model_id)
    if kind == "flux_klein_gguf":
        spec = image_gguf_spec(model_id)
        if not spec:
            raise ValueError(f"No gguf_repo/text_encoder_repo configured for {model_id!r}")
        # Two GGUF weight files (transformer + text encoder) from their own repos, plus
        # model_id's own repo for its small config-only files (model_index.json,
        # scheduler/vae/tokenizer configs) — see _load_pipeline()'s flux_klein_gguf branch
        # for how each piece is used at load time.
        hf_hub_download(repo_id=spec["gguf_repo"], filename=spec["gguf_file"], tqdm_class=tqdm_class)
        hf_hub_download(
            repo_id=spec["text_encoder_repo"], filename=spec["text_encoder_file"], tqdm_class=tqdm_class
        )
        snapshot_download(
            repo_id=model_id, allow_patterns=_FLUX_KLEIN_CONFIG_ALLOW_PATTERNS, tqdm_class=tqdm_class
        )
        return
    if kind == "sdxl_lightning":
        files = image_checkpoint_files(model_id)
        if not files:
            raise ValueError(f"No checkpoint_file_low/checkpoint_file_high configured for {model_id!r}")
        for filename in files.values():
            hf_hub_download(repo_id=model_id, filename=filename, tqdm_class=tqdm_class)
        return

    snapshot_download(
        repo_id=model_id, allow_patterns=_IMAGE_MODEL_ALLOW_PATTERNS, tqdm_class=tqdm_class
    )
    lcm_unet_id = image_lcm_unet_id(model_id)
    if lcm_unet_id:
        snapshot_download(
            repo_id=lcm_unet_id, allow_patterns=_IMAGE_MODEL_ALLOW_PATTERNS, tqdm_class=tqdm_class
        )
    if kind == "sd15":
        # The "low" quality mode's LCM LoRA — never pre-fetched before, so it only ever
        # ended up cached as a side effect of an earlier *online* generation silently
        # downloading it on demand. Fetch it here too so a from-empty-cache model
        # library download is enough for low-quality generation to work fully offline
        # on the very first run, no prior online generation required.
        from services.image_generation import DEFAULT_LORA_ID

        if DEFAULT_LORA_ID:
            snapshot_download(
                repo_id=DEFAULT_LORA_ID, allow_patterns=_IMAGE_MODEL_ALLOW_PATTERNS,
                tqdm_class=tqdm_class,
            )


def image_generation_deps_available() -> tuple[bool, str]:
    """Return (ok, detail) for Diffusers / torch stack required to render images."""
    missing: list[str] = []
    try:
        import diffusers  # noqa: F401
    except ImportError:
        missing.append("diffusers")
    try:
        import torch  # noqa: F401
    except ImportError:
        missing.append("torch")
    if missing:
        return False, ", ".join(missing)
    return True, ""


def background_removal_deps_available() -> tuple[bool, str]:
    """Return (ok, detail) for rembg, used to extract a clean subject cutout for
    subject-swap mutations and standalone remove/replace-background requests."""
    try:
        import rembg  # noqa: F401
    except ImportError:
        return False, "rembg"
    return True, ""


def find_vision_model() -> str | None:
    """
    Returns the configured default vision model when capable, otherwise the first
    installed model that supports vision.
    """
    preferred = get_default_vision_model_from_settings()
    if preferred:
        return preferred

    capable = get_vision_capable_models()
    if capable:
        return capable[0]

    return None


def resolve_vision_model(settings: dict | None = None) -> str | None:
    """Alias used by agents and pipeline — honors settings default."""
    if settings is not None:
        preferred = get_default_vision_model_from_settings(settings)
        if preferred:
            return preferred
    return find_vision_model()


def resolve_chat_model(
    profile: dict,
    llm_type: str,
    images: list,
    *,
    context_files: list | None = None,
) -> tuple[str, str | None]:
    """
    Returns (model_name, error_message).
    error_message is set when vision is required but no capable model is installed.
    """
    if needs_vision(llm_type, images):
        vision = resolve_vision_model()
        if vision:
            return vision, None
        return resolve_general_model(profile), "__LOMA_GAP_VISION__"
    general = resolve_general_model(profile)
    if needs_audio(context_files) and check_model_supports_audio(general):
        return general, None
    return general, None


def resolve_coordinator_model(profile: dict | None = None) -> str:
    """Specialist model for Coordinator gating/repair; falls back to General."""
    prof = profile or {}
    installed = get_installed_models()

    def _pick(name: str) -> str:
        from services.model_assignments import _installed_match

        candidate = str(name or "").strip()
        if not candidate:
            return ""
        if installed:
            matched = _installed_match(candidate, installed)
            return matched or ""
        return candidate

    model_cfg = prof.get("MODEL", {})
    specialist = _pick(ROLES.get("Specialist"))
    if specialist:
        return specialist
    try:
        from services.session import state

        assignments = (state.current_settings or {}).get("assignments") or {}
        raw = assignments.get("Specialist", "")
        assigned = raw.get("label") if isinstance(raw, dict) else str(raw or "")
        assigned = _pick(assigned)
        if assigned:
            return assigned
    except Exception:
        pass
    return resolve_general_model(prof)


def resolve_planner_override(llm_type: str, images: list) -> str | None:
    """Allows the system planner task generator to route directly to a vision model when analyzing assets."""
    if needs_vision(llm_type, images):
        return resolve_vision_model()
    return None