# -*- coding: utf-8 -*-
"""Hardware-aware catalog for vision LLMs (Ollama) and image generation (Diffusers)."""

from __future__ import annotations

from services.system.profiler import (
    FREE_RAM_HEADROOM_GB,
    SAFETY_FACTOR,
    SystemProfile,
    effective_memory_gb,
    hardware_tier,
)

_VISION_LLM_RAW: list[dict] = [
    # Tier 1 — all vision + text
    {
        "name": "sorc/qwen3.5-instruct:2b",
        "label": "Qwen 3.5 Instruct (2B)",
        "size": "2.7 GB",
        "size_gb": 2.7,
        "hardware_tiers": [1],
        "family": "qwen35-instruct",
        "tier_speed": "fast",
        "tier": "fast",
        "min_ram": 4,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Fast",
        "desc": "Compact vision-language model for routing and quick tasks.",
    },
    {
        "name": "llava-phi3:3.8b",
        "label": "LLaVA Phi-3 (3.8B)",
        "size": "2.9 GB",
        "size_gb": 2.9,
        "hardware_tiers": [1],
        "family": "llava-phi3",
        "tier_speed": "balanced",
        "tier": "balanced",
        "min_ram": 6,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Recommended",
        "desc": "Balanced multimodal assistant for everyday chat and vision.",
    },
    {
        "name": "frob/qwen3.5-instruct:4b",
        "label": "Qwen 3.5 Instruct (4B)",
        "size": "3.4 GB",
        "size_gb": 3.4,
        "hardware_tiers": [1],
        "family": "qwen35-instruct",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 6,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Best on Tier 1",
        "desc": "Strongest curated option for 8 GB class machines.",
    },
    {
        "name": "gemma4:e2b",
        "label": "Gemma 4 (2B eff.)",
        "size": "7.2 GB",
        "size_gb": 7.2,
        "hardware_tiers": [1],
        "family": "gemma4",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 8,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Gemma vision",
        "desc": "Efficient Gemma 4 vision stack for tier-1 hardware.",
    },
    # Tier 2
    {
        "name": "llava:7b",
        "label": "LLaVA (7B)",
        "size": "4.7 GB",
        "size_gb": 4.7,
        "hardware_tiers": [2],
        "family": "llava",
        "tier_speed": "fast",
        "tier": "fast",
        "min_ram": 8,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Fast",
        "desc": "Light LLaVA variant when RAM is tight.",
    },
    {
        "name": "minicpm-v:8b",
        "label": "MiniCPM-V (8B)",
        "size": "5.5 GB",
        "size_gb": 5.5,
        "hardware_tiers": [2],
        "family": "minicpm-v",
        "tier_speed": "balanced",
        "tier": "balanced",
        "min_ram": 10,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Recommended",
        "desc": "Strong document and scene understanding.",
    },
    {
        "name": "frob/qwen3.5-instruct:9b",
        "label": "Qwen 3.5 Instruct (9B)",
        "size": "6.6 GB",
        "size_gb": 6.6,
        "hardware_tiers": [2],
        "family": "qwen35-instruct",
        "tier_speed": "balanced",
        "tier": "balanced",
        "min_ram": 10,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Balanced",
        "desc": "Mid-size multimodal model for agentic workflows.",
    },
    {
        "name": "gemma4:12b",
        "label": "Gemma 4 (12B)",
        "size": "7.6 GB",
        "size_gb": 7.6,
        "hardware_tiers": [2],
        "family": "gemma4",
        "tier_speed": "balanced",
        "tier": "balanced",
        "min_ram": 12,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Recommended",
        "desc": "Excellent OCR and layout understanding.",
    },
    {
        "name": "llama3.2-vision:11b",
        "label": "Llama 3.2 Vision (11B)",
        "size": "7.8 GB",
        "size_gb": 7.8,
        "hardware_tiers": [2],
        "family": "llama32-vision",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 12,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "High quality",
        "desc": "Deep visual reasoning on mid-range hardware.",
    },
    {
        "name": "llava:13b",
        "label": "LLaVA (13B)",
        "size": "8.0 GB",
        "size_gb": 8.0,
        "hardware_tiers": [2],
        "family": "llava",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 12,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "High quality",
        "desc": "Larger LLaVA when free RAM allows.",
    },
    {
        "name": "haervwe/GLM-4.6V-Flash-9B:latest",
        "label": "GLM-4.6V Flash (9B)",
        "size": "8.0 GB",
        "size_gb": 8.0,
        "hardware_tiers": [2],
        "family": "glm46v",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 12,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Vision specialist",
        "desc": "Fast GLM vision model for complex scenes.",
    },
    {
        "name": "gemma4:e4b",
        "label": "Gemma 4 (4B eff.)",
        "size": "9.6 GB",
        "size_gb": 9.6,
        "hardware_tiers": [2],
        "family": "gemma4",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 14,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Max Tier 2",
        "desc": "Largest Gemma 4 variant for 16 GB class machines.",
    },
    # Tier 3
    {
        "name": "mistral-small3.2:24b",
        "label": "Mistral Small 3.2 (24B)",
        "size": "15 GB",
        "size_gb": 15.0,
        "hardware_tiers": [3],
        "family": "mistral-small",
        "tier_speed": "fast",
        "tier": "fast",
        "min_ram": 16,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Light Tier 3",
        "desc": "Entry point for high-RAM orchestration.",
    },
    {
        "name": "gemma4:26b",
        "label": "Gemma 4 (26B)",
        "size": "18 GB",
        "size_gb": 18.0,
        "hardware_tiers": [3],
        "family": "gemma4-large",
        "tier_speed": "balanced",
        "tier": "balanced",
        "min_ram": 20,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Recommended",
        "desc": "Top-tier multimodal reasoning for capable PCs.",
    },
    {
        "name": "frob/qwen3.5-instruct:35b",
        "label": "Qwen 3.5 Instruct (35B)",
        "size": "24 GB",
        "size_gb": 24.0,
        "hardware_tiers": [3],
        "family": "qwen35-large",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 24,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Agentic",
        "desc": "Heavy multimodal model for coding and verification.",
    },
    {
        "name": "nemotron3:33b",
        "label": "Nemotron 3 (33B)",
        "size": "28 GB",
        "size_gb": 28.0,
        "hardware_tiers": [3],
        "family": "nemotron3",
        "tier_speed": "quality",
        "tier": "quality",
        "min_ram": 28,
        "min_vram": 0,
        "provider": "ollama",
        "enabled": True,
        "badge": "Orchestrator",
        "desc": "Maximum curated intelligence for planning and decomposition.",
    },
]

MODEL_CATALOG: dict[str, list[dict]] = {
    "vision_llm": list(_VISION_LLM_RAW),
    "llm": list(_VISION_LLM_RAW),
    "vision": list(_VISION_LLM_RAW),
    "image_generation": [
        {
            "name": "SG161222/Realistic_Vision_V6.0_B1_noVAE",
            "label": "Realistic Vision V6.0",
            "size": "~5.5 GB",
            "min_ram": 8,
            "min_vram": 4,
            "tier": "fast",
            "hardware_tiers": [1],
            "pipeline": "sd15",
            "enabled": True,
            "badge": "Photorealistic",
            "desc": (
                "SD 1.5 checkpoint tuned for photorealism. Low = LCM LoRA, 8 steps. "
                "High = DPM++ SDE Karras sampler, ~28 steps, no LCM."
            ),
        },
        {
            "name": "Lykon/dreamshaper-8",
            "label": "DreamShaper 8",
            "size": "~8 GB",
            "min_ram": 8,
            "min_vram": 4,
            "tier": "balanced",
            "hardware_tiers": [2],
            "pipeline": "sd15",
            "enabled": True,
            "badge": "Recommended",
            "desc": (
                "Versatile SD 1.5 checkpoint. Low = LCM LoRA, 8 steps. "
                "High = DPM++ SDE Karras sampler, ~28 steps, no LCM."
            ),
        },
        {
            "name": "ByteDance/SDXL-Lightning",
            "label": "SDXL Lightning",
            "size": "~7 GB",
            "size_gb": 7.0,
            "min_ram": 16,
            "min_vram": 8,
            "tier": "quality",
            "hardware_tiers": [3],
            "pipeline": "sdxl_lightning",
            # 8-step only — the 4-step checkpoint's output is indistinguishable enough
            # from 8-step in practice (same distilled recipe, same underlying artifacts)
            # that a second ~7GB download for a Low mode isn't worth it; see the earlier
            # discussion this was scoped from. checkpoint_file_high name kept as-is
            # (image_checkpoint_file()/_files() key off it) even though there's no
            # "_low" counterpart anymore — renaming it would just be churn.
            "checkpoint_file_high": "sdxl_lightning_8step.safetensors",
            "enabled": True,
            "badge": "1024px",
            "desc": "Distilled SDXL checkpoint — near-base-SDXL 1024px quality, 8 steps.",
        },
        {
            "name": "black-forest-labs/FLUX.2-klein-4B",
            "label": "FLUX.2 Klein 4B",
            "size": "~5.4 GB",
            "size_gb": 5.4,
            "min_ram": 16,
            "min_vram": 8,
            "tier": "flux",
            "hardware_tiers": [4],
            "pipeline": "flux_klein_gguf",
            # Transformer + text-encoder weights come from separate GGUF repos, not the
            # base repo above (which only supplies model_index.json/scheduler/vae/tokenizer
            # config — see image_gguf_spec() and _load_pipeline()'s flux_klein_gguf branch).
            "gguf_repo": "unsloth/FLUX.2-klein-4B-GGUF",
            "gguf_file": "flux-2-klein-4b-Q4_K_M.gguf",
            "text_encoder_repo": "unsloth/Qwen3-4B-GGUF",
            "text_encoder_file": "Qwen3-4B-Q4_K_M.gguf",
            "enabled": True,
            "badge": "FLUX",
            "desc": (
                "DiT architecture, distilled (4 steps, guidance-distilled) — No "
                "Low/High mode: a single fixed quality setting. 4-bit quantised model. "
                "Best image generator but slowest in LOMA Extended Edition."
            ),
        },
    ],
    "ocr": [
        {
            "name": "vision-llm",
            "label": "OCR via Vision Model",
            "size": "Uses vision model",
            "min_ram": 8,
            "min_vram": 0,
            "enabled": False,
            "badge": "Uses vision model",
            "desc": "Scanned PDF OCR uses your installed vision LLM.",
        },
    ],
    "voice_input": [
        {
            "name": "hybrid-whisper",
            "label": "Hybrid Whisper (live dictation)",
            "size": "~150 MB+",
            "min_ram": 4,
            "enabled": True,
            "badge": "All languages",
            "desc": "faster-whisper tiny preview + accurate model for live mic/dictate.",
        },
        {
            "name": "sensevoice",
            "label": "SenseVoice (en/zh)",
            "size": "~900 MB",
            "min_ram": 4,
            "enabled": True,
            "badge": "Fast en/zh",
            "desc": "FunASR SenseVoiceSmall for English and Chinese dictation.",
        },
    ],
    "video_input": [
        {
            "name": "video-frame-extract",
            "label": "Video Frame Analysis",
            "size": "Uses vision model",
            "min_ram": 8,
            "enabled": False,
            "badge": "Uses vision model",
            "desc": "Video frames use your installed vision LLM.",
        },
    ],
    "audio_generation": [
        {
            "name": "local-tts",
            "label": "Local Text-to-Speech",
            "size": "~500 MB",
            "min_ram": 8,
            "enabled": False,
            "badge": "Coming soon",
            "desc": "Neural TTS — currently a placeholder tone generator only.",
        },
    ],
    "video_generation": [
        {
            "name": "local-video-gen",
            "label": "Local Video Generation",
            "size": "TBD",
            "min_ram": 16,
            "min_vram": 8,
            "enabled": False,
            "badge": "Coming soon",
            "desc": "Video generation — currently a placeholder metadata export only.",
        },
    ],
}

MUST_HAVE_TOOLS: list[dict[str, str]] = [
    {"key": "ollama", "label": "Backend inference (Ollama)"},
    {"key": "ffmpeg", "label": "ffmpeg (audio/video)"},
    {"key": "image_deps", "label": "Image generation runtime (torch/diffusers)"},
    {"key": "whisper", "label": "Whisper (local transcription)"},
]

SUBSYSTEM_CATEGORIES: list[dict[str, str]] = [
    {"key": "image_generation", "label": "Image generation"},
    {"key": "ocr", "label": "OCR (scanned documents)"},
    {"key": "voice_input", "label": "Voice input"},
    {"key": "video_input", "label": "Video input"},
    {"key": "audio_generation", "label": "Sound generation"},
    {"key": "video_generation", "label": "Video generation"},
]

_CATALOG_NAMES: set[str] = {e["name"] for e in _VISION_LLM_RAW}


def vision_llm_catalog_names() -> set[str]:
    return set(_CATALOG_NAMES)


def is_curated_vision_llm(model_name: str) -> bool:
    name = (model_name or "").strip()
    if name in _CATALOG_NAMES:
        return True
    return any(name in n or n in name for n in _CATALOG_NAMES)


def catalog_entry(category: str, model_name: str) -> dict | None:
    cat = _resolve_category(category)
    for entry in MODEL_CATALOG.get(cat, []):
        if entry.get("name") == model_name:
            return entry
    return None


def _resolve_category(category: str) -> str:
    if category in ("llm", "vision"):
        return "vision_llm"
    return category


def image_pipeline_kind(model_id: str) -> str:
    """"sd15" | "sdxl" | "sdxl_lightning" | "flux_klein_gguf" — the loader path
    _load_pipeline() should use. "sdxl_lightning" is deliberately its own kind, not
    folded into "sdxl": it loads from a single merged .safetensors checkpoint
    (image_checkpoint_file()) via from_single_file(), not from_pretrained() against a
    standard multi-file diffusers repo. "flux_klein_gguf" is its own DiT-architecture
    kind entirely — see image_gguf_spec()."""
    entry = catalog_entry("image_generation", model_id) or {}
    kind = (entry.get("pipeline") or "").strip().lower()
    if kind == "flux_klein_gguf":
        return "flux_klein_gguf"
    if kind in ("sdxl", "sd15", "sd1.5", "sdxl_lightning"):
        if kind == "sdxl_lightning":
            return "sdxl_lightning"
        return "sdxl" if kind == "sdxl" else "sd15"
    mid = (model_id or "").lower().replace("\\", "/")
    if "stable-diffusion-xl" in mid or "sdxl" in mid.split("/")[-1]:
        return "sdxl"
    return "sd15"


def image_checkpoint_file(model_id: str, quality_mode: str) -> str:
    """The .safetensors filename to fetch for an sdxl_lightning-kind entry — its repo
    also hosts unrelated 1/2-step and UNet-only/LoRA variants, so a specific filename is
    always targeted, never the whole repo. `quality_mode` is accepted for call-site
    compatibility but ignored: there's only the 8-step checkpoint (checkpoint_file_high)
    now — the 4-step Low mode was dropped as not worth a second ~7GB download for an
    indistinguishable-in-practice result (see the catalog entry's own comment). Empty
    for every other pipeline kind, which load a standard multi-file repo."""
    del quality_mode
    entry = catalog_entry("image_generation", model_id) or {}
    return (entry.get("checkpoint_file_high") or "").strip()


def image_checkpoint_files(model_id: str) -> dict[str, str]:
    """{"high": ...} — the checkpoint_file_* this sdxl_lightning-kind entry needs
    downloaded. Empty dict for every other pipeline kind."""
    entry = catalog_entry("image_generation", model_id) or {}
    files = {}
    if entry.get("checkpoint_file_low"):
        files["low"] = entry["checkpoint_file_low"]
    if entry.get("checkpoint_file_high"):
        files["high"] = entry["checkpoint_file_high"]
    return files


def image_lcm_unet_id(model_id: str) -> str:
    """Repo id of a standalone LCM-distilled UNet2DConditionModel to swap into this
    catalog entry's pipeline (e.g. SSD-1B's latent-consistency/lcm-ssd-1b) — distinct from
    an LCM LoRA (services.image_generation.DEFAULT_LORA_ID), since this one ships full UNet
    weights to load directly, not adapter weights to fuse onto the existing UNet."""
    entry = catalog_entry("image_generation", model_id) or {}
    return (entry.get("lcm_unet_id") or "").strip()


def image_gguf_spec(model_id: str) -> dict[str, str]:
    """{"gguf_repo", "gguf_file", "text_encoder_repo", "text_encoder_file"} for a
    flux_klein_gguf-kind entry — the transformer and text-encoder GGUF weights live in
    separate repos from `model_id` itself (model_id's own repo only supplies
    model_index.json/scheduler/vae/tokenizer config, which stay full-precision since
    they're small). Empty dict for every other pipeline kind."""
    entry = catalog_entry("image_generation", model_id) or {}
    keys = ("gguf_repo", "gguf_file", "text_encoder_repo", "text_encoder_file")
    if not all(entry.get(k) for k in keys):
        return {}
    return {k: entry[k] for k in keys}


def model_option_label(entry: dict) -> str:
    """Short label for model select dropdowns (no badge / fit tag)."""
    name = entry.get("name", "")
    return f"{entry.get('label', name)} ({entry.get('size', '?')})"


def get_entry_by_name(name: str) -> dict | None:
    """Look up a catalog entry (vision_llm) by exact model name."""
    for e in MODEL_CATALOG["vision_llm"]:
        if e.get("name") == name:
            return e
    return None


def model_select_dict(entries: list[dict]) -> dict[str, str]:
    """Choice options: {model_name: display_label} (radio / select)."""
    return {e["name"]: model_option_label(e) for e in entries}


def model_select_indexed(entries: list[dict]) -> tuple[dict[str, str], dict[str, str]]:
    """Safe dropdown keys (0..n) when model ids contain ':' or '/'."""
    options: dict[str, str] = {}
    key_to_name: dict[str, str] = {}
    for i, entry in enumerate(entries):
        key = str(i)
        options[key] = model_option_label(entry)
        key_to_name[key] = entry["name"]
    return options, key_to_name


def _catalog_provider_id(provider_id: str | None) -> str:
    return str(provider_id or "").strip().lower()


def _entry_provider_matches(entry: dict, provider_id: str | None) -> bool:
    pid = _catalog_provider_id(provider_id)
    if not pid:
        return True
    prov = str(entry.get("provider", "both")).strip().lower()
    return prov in ("both", pid)


def hardware_tag(entry: dict, profile: SystemProfile) -> str:
    size_gb = float(entry.get("size_gb") or 0)
    effective = effective_memory_gb(profile)
    if size_gb > 0 and size_gb <= effective * SAFETY_FACTOR:
        if profile.available_ram_gb - size_gb >= FREE_RAM_HEADROOM_GB:
            return "★ Recommended"
        return "OK"
    if size_gb > effective:
        return "⚠️ Heavy"
    return "OK"


def _entry_fits(entry: dict, profile: SystemProfile, provider_id: str = "") -> bool:
    if not _entry_provider_matches(entry, provider_id):
        return False
    min_ram = float(entry.get("min_ram", 0))
    min_vram = float(entry.get("min_vram", 0))
    if not profile.fits_hardware(min_ram, min_vram):
        return False
    size_gb = float(entry.get("size_gb") or 0)
    if size_gb > 0:
        return size_gb <= effective_memory_gb(profile) * SAFETY_FACTOR
    return True


def _pick_best_in_family(candidates: list[dict], profile: SystemProfile) -> dict | None:
    if not candidates:
        return None
    effective = effective_memory_gb(profile)
    fitting = [e for e in candidates if float(e.get("size_gb", 999)) <= effective * SAFETY_FACTOR]
    if not fitting:
        fitting = [min(candidates, key=lambda e: float(e.get("size_gb", 999)))]
    fitting.sort(key=lambda e: float(e.get("size_gb", 0)), reverse=True)
    for entry in fitting:
        if profile.available_ram_gb - float(entry.get("size_gb", 0)) >= FREE_RAM_HEADROOM_GB:
            return entry
    return fitting[0]


def _dedupe_families(entries: list[dict], profile: SystemProfile) -> list[dict]:
    by_family: dict[str, list[dict]] = {}
    for entry in entries:
        fam = str(entry.get("family") or entry["name"])
        by_family.setdefault(fam, []).append(entry)
    out: list[dict] = []
    for group in by_family.values():
        picked = _pick_best_in_family(group, profile)
        if picked:
            out.append(picked)
    return sorted(out, key=lambda e: float(e.get("size_gb", 0)))


def entries_for_hardware_tier(profile: SystemProfile, *, provider_id: str = "") -> list[dict]:
    tier = hardware_tier(profile)
    pool = [
        e
        for e in MODEL_CATALOG["vision_llm"]
        if e.get("enabled", True) is not False and tier in e.get("hardware_tiers", [])
    ]
    if provider_id:
        pool = [e for e in pool if _entry_provider_matches(e, provider_id)]
    if tier == 2:
        pool = _dedupe_families(pool, profile)
    return pool


def entries_for_role(role: str, profile: SystemProfile, *, provider_id: str = "") -> list[dict]:
    if role == "General":
        pool = [
            e
            for e in MODEL_CATALOG["vision_llm"]
            if e.get("enabled", True) is not False and 1 in e.get("hardware_tiers", [])
        ]
        if provider_id:
            pool = [e for e in pool if _entry_provider_matches(e, provider_id)]
        return sorted(pool, key=lambda e: float(e.get("size_gb", 0)))
    if role in ("Specialist", "Verifier"):
        pool = [
            e
            for e in MODEL_CATALOG["vision_llm"]
            if e.get("enabled", True) is not False and 2 in e.get("hardware_tiers", [])
        ]
        if provider_id:
            pool = [e for e in pool if _entry_provider_matches(e, provider_id)]
        fitting = [e for e in pool if _entry_fits(e, profile, provider_id)]
        pool = fitting or pool
        return sorted(pool, key=lambda e: float(e.get("size_gb", 0)))
    pool = entries_for_hardware_tier(profile, provider_id=provider_id)
    if role == "Orchestrator":
        return sorted(pool, key=lambda e: float(e.get("size_gb", 0)), reverse=True)
    return list(pool)


def _vision_pool_for_hw_tier(
    hw_tier: int,
    profile: SystemProfile,
    *,
    provider_id: str = "",
) -> list[dict]:
    pool = [
        e
        for e in MODEL_CATALOG["vision_llm"]
        if e.get("enabled", True) is not False and hw_tier in e.get("hardware_tiers", [])
    ]
    if provider_id:
        pool = [e for e in pool if _entry_provider_matches(e, provider_id)]
    fitting = [e for e in pool if _entry_fits(e, profile, provider_id)]
    pool = fitting or pool
    if hw_tier == 2:
        pool = _dedupe_families(pool, profile)
    return pool


def _pick_slot_entry(entries: list[dict], slot: str) -> dict | None:
    if not entries:
        return None
    if slot == "fast":
        fast = [e for e in entries if e.get("tier") == "fast"]
        pool = fast or entries
        return min(pool, key=lambda e: float(e.get("size_gb", 999)))
    if slot == "quality":
        quality = [e for e in entries if e.get("tier") == "quality"]
        pool = quality or entries
        return max(pool, key=lambda e: float(e.get("size_gb", 0)))
    balanced = [e for e in entries if e.get("tier") == "balanced"]
    if balanced:
        ordered = sorted(balanced, key=lambda e: float(e.get("size_gb", 0)))
        return ordered[len(ordered) // 2]
    ordered = sorted(entries, key=lambda e: float(e.get("size_gb", 0)))
    return ordered[len(ordered) // 2]


def _pick_three_from_pool(pool: list[dict]) -> dict[str, dict | None]:
    by_tier: dict[str, dict | None] = {"recommended": None, "quality": None, "fast": None}
    used: set[str] = set()
    for slot in ("fast", "recommended", "quality"):
        candidates = [e for e in pool if e["name"] not in used]
        pick = _pick_slot_entry(candidates, slot)
        if pick:
            by_tier[slot] = pick
            used.add(pick["name"])
    return by_tier


def _recommend_vision_llm_three(
    profile: SystemProfile,
    *,
    provider_id: str = "",
) -> dict[str, dict | None]:
    """Pick three download options based on PC hardware tier (1/2/3)."""
    hw = hardware_tier(profile)
    if hw == 1:
        return _pick_three_from_pool(_vision_pool_for_hw_tier(1, profile, provider_id=provider_id))

    if hw == 2:
        t1 = _vision_pool_for_hw_tier(1, profile, provider_id=provider_id)
        t2 = _vision_pool_for_hw_tier(2, profile, provider_id=provider_id)
        fast = _pick_slot_entry(t1, "fast")
        used = {fast["name"]} if fast else set()
        t1_rest = [e for e in t1 if e["name"] not in used]
        return {
            "fast": fast,
            "recommended": _pick_slot_entry(t1_rest, "recommended"),
            "quality": _pick_slot_entry(t2, "quality") or _pick_slot_entry(t2, "recommended"),
        }

    t1 = _vision_pool_for_hw_tier(1, profile, provider_id=provider_id)
    t2 = _vision_pool_for_hw_tier(2, profile, provider_id=provider_id)
    t3 = _vision_pool_for_hw_tier(3, profile, provider_id=provider_id)
    return {
        "fast": _pick_slot_entry(t1, "fast"),
        "recommended": _pick_slot_entry(t2, "recommended") or _pick_slot_entry(t2, "balanced"),
        "quality": _pick_slot_entry(t3, "quality") or _pick_slot_entry(t3, "recommended"),
    }


def recommend_general_dropdown(
    profile: SystemProfile,
    *,
    provider_id: str = "",
    limit: int = 4,
) -> list[dict]:
    """Tier-1 models for General / fast chat (setup dropdown)."""
    pool = entries_for_role("General", profile, provider_id=provider_id)
    if not pool:
        pool = entries_for_role("General", profile, provider_id="")
    if limit > 0:
        return pool[:limit]
    return pool


def recommend_tier_dropdown(
    hw_tier: int,
    profile: SystemProfile,
    *,
    provider_id: str = "",
    limit: int = 4,
    offset: int = 0,
    dedupe_family: bool = True,
) -> list[dict]:
    """Curated vision LLM list for a hardware tier (setup optional picks / config)."""
    pool = [
        e
        for e in MODEL_CATALOG["vision_llm"]
        if e.get("enabled", True) is not False and hw_tier in e.get("hardware_tiers", [])
    ]
    if provider_id:
        pool = [e for e in pool if _entry_provider_matches(e, provider_id)]
    pool = sorted(pool, key=lambda e: float(e.get("size_gb", 0)))
    if not pool:
        pool = sorted(
            [
                e
                for e in MODEL_CATALOG["vision_llm"]
                if e.get("enabled", True) is not False and hw_tier in e.get("hardware_tiers", [])
            ],
            key=lambda e: float(e.get("size_gb", 0)),
        )
    if dedupe_family:
        seen: set[str] = set()
        unique: list[dict] = []
        for entry in pool:
            fam = str(entry.get("family") or entry.get("name") or "").strip()
            if fam in seen:
                continue
            seen.add(fam)
            unique.append(entry)
        pool = unique
    if offset > 0:
        pool = pool[offset:]
    if limit > 0:
        return pool[:limit]
    return pool


def recommend_tier_role_dropdown(
    hw_tier: int,
    profile: SystemProfile,
    role: str,
    *,
    provider_id: str = "",
    per_role_limit: int = 5,
) -> list[dict]:
    """Fixed tier-2 pools for Specialist vs Verifier (Configuration tab)."""
    if hw_tier != 2:
        return recommend_tier_dropdown(
            hw_tier, profile, provider_id=provider_id, limit=per_role_limit, dedupe_family=True
        )
    specialist_names = [
        "llava:7b",
        "minicpm-v:8b",
        "frob/qwen3.5-instruct:9b",
        "gemma4:12b",
    ]
    verifier_names = [
        "llama3.2-vision:11b",
        "llava:13b",
        "haervwe/GLM-4.6V-Flash-9B:latest",
        "gemma4:e4b",
    ]
    names = verifier_names if role == "Verifier" else specialist_names
    by_name = {
        e["name"]: e
        for e in MODEL_CATALOG["vision_llm"]
        if e.get("enabled", True) is not False
    }
    pool: list[dict] = []
    for name in names:
        entry = by_name.get(name)
        if not entry:
            continue
        if provider_id and not _entry_provider_matches(entry, provider_id):
            continue
        pool.append(entry)
    return pool


def recommend_starter_optional(
    profile: SystemProfile,
    *,
    provider_id: str = "",
) -> dict[str, dict | None]:
    """Optional tier-2/3 upgrade picks for setup wizard (hardware-gated)."""
    hw = hardware_tier(profile)
    picks = _recommend_vision_llm_three(profile, provider_id=provider_id)
    out: dict[str, dict | None] = {}
    if hw == 2:
        out["balanced"] = picks.get("quality")
    elif hw >= 3:
        out["balanced"] = picks.get("recommended")
        out["quality"] = picks.get("quality")
    return out


def recommend_three_tiers(
    category: str,
    profile: SystemProfile,
    *,
    provider_id: str = "",
) -> dict[str, dict | None]:
    """Return recommended / quality / fast catalog entries for vision LLM or image gen."""
    cat = _resolve_category(category)
    if cat == "vision_llm":
        return _recommend_vision_llm_three(profile, provider_id=provider_id)
    else:
        tier = hardware_tier(profile)
        pool = [
            e
            for e in MODEL_CATALOG.get(cat, [])
            if e.get("enabled", True) is not False
            and (not e.get("hardware_tiers") or tier in e.get("hardware_tiers", []))
        ]
        fitting = [e for e in pool if _entry_fits(e, profile, provider_id)]
        pool = fitting or pool

    by_tier: dict[str, dict | None] = {"recommended": None, "quality": None, "fast": None}
    if not pool:
        return by_tier

    balanced = [e for e in pool if e.get("tier") == "balanced"]
    quality = [e for e in pool if e.get("tier") == "quality"]
    fast = [e for e in pool if e.get("tier") == "fast"]

    if balanced:
        by_tier["recommended"] = min(balanced, key=lambda e: float(e.get("size_gb", 999)))
    else:
        by_tier["recommended"] = pool[len(pool) // 2]

    if quality:
        by_tier["quality"] = max(quality, key=lambda e: float(e.get("size_gb", 0)))
    else:
        by_tier["quality"] = max(pool, key=lambda e: float(e.get("size_gb", 0)))

    if fast:
        by_tier["fast"] = min(fast, key=lambda e: float(e.get("size_gb", 999)))
    else:
        by_tier["fast"] = min(pool, key=lambda e: float(e.get("size_gb", 999)))

    return by_tier


def category_status(category: str, *, installed_check: bool = False) -> str:
    entries = MODEL_CATALOG.get(_resolve_category(category), [])
    if not entries:
        return "Planned"
    if entries[0].get("enabled") is False:
        return "Planned"
    if installed_check:
        return "Missing"
    return "Ready"
