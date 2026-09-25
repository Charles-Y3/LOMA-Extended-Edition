# -*- coding: utf-8 -*-
"""Diffusers image generation: SD 1.5 (+ optional LCM LoRA) and SDXL checkpoints."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Any

class ImageGenerationCancelled(Exception):
    """Raised from progress_cb (see run_pipe_with_progress) when the workflow's Stop
    button was hit mid-generation. Propagates past the broad except-Exception fallback
    handlers below (which would otherwise treat it as a real failure and write a
    fallback-metadata file) all the way to the caller in step_executor.py."""


class ImageGenerationDeclinedError(Exception):
    """Raised by a prompt-authoring path (e.g. services/poster_generation.py) when
    pipeline.image_safety_embeddings.is_explicit_prompt() flags the resolved prompt —
    callers should catch this specifically and show its message as-is (already a
    translated, user-facing decline message), not wrap it in a generic failure message."""


IMAGE_MARKER_RE = re.compile(r"\[IMAGE_PROMPT:\s*(.+?)\]", re.IGNORECASE)
_RESOLUTION_HYPE_RE = re.compile(
    r"\b(8k|4k|16k|ultra\s*hd|uhd)\s*(resolution)?\b",
    re.IGNORECASE,
)
_META_INSTRUCTION_RE = re.compile(
    r"^(generate|create|make|draw)\s+(an?\s+)?(image|picture|photo)\s+(of\s+)?",
    re.IGNORECASE,
)
_LOMA_CAPTION_PROMPT_RE = re.compile(
    r"\*\*Prompt:\*\*\s*(.+?)(?:\n\n|\n\*\*|\nSeed\b|\Z)",
    re.IGNORECASE | re.DOTALL,
)
_ACTION_VERB_RE = re.compile(
    r"\b(chas(e|ing)|run(ning)?|hunt(ing)?|attack(ing)?|pursu(e|ing)|fight(ing)?)\b",
    re.IGNORECASE,
)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "and",
        "or",
        "with",
        "after",
        "before",
        "from",
        "into",
        "through",
        "wild",
        "image",
        "photo",
        "picture",
    }
)
_IMAGE_QUALITY = os.environ.get("LOMA_IMAGE_QUALITY", "balanced").strip().lower()

GENERATED_IMAGE_DIR = os.path.join("data", "generated", "images")


def unique_output_path(source_path: str, suffix: str) -> str:
    """A `{stem}_{suffix}.png` path under GENERATED_IMAGE_DIR, numbered (_2, _3, ...)
    if that name is already taken — so repeated edits of the same source image (e.g.
    blue, then red, then white) each get their own file instead of overwriting the
    previous edit."""
    stem = Path(source_path).stem
    base = os.path.join(GENERATED_IMAGE_DIR, f"{stem}_{suffix}")
    path = f"{base}.png"
    n = 1
    while os.path.exists(path):
        n += 1
        path = f"{base}_{n}.png"
    return path
_DEBUG_LOG = Path(__file__).resolve().parents[2] / "debug-d4eaa9.log"
# Pre-merged LCM checkpoint — used when peft is not installed (no LoRA fuse step).
DEFAULT_LCM_MERGED_MODEL = os.environ.get(
    "LOMA_IMAGE_LCM_MERGED_MODEL",
    "SimianLuo/LCM_Dreamshaper",
)

# Base checkpoint (SD 1.5). Override with LOMA_IMAGE_MODEL (HF repo id or local path).
DEFAULT_MODEL_ID = os.environ.get(
    "LOMA_IMAGE_MODEL",
    "Lykon/dreamshaper-8",
)
# LCM LoRA for 4–8 step generation; set LOMA_IMAGE_LORA="" to disable.
DEFAULT_LORA_ID = os.environ.get(
    "LOMA_IMAGE_LORA",
    "latent-consistency/lcm-lora-sdv1-5",
)
_DEFAULT_STEPS_BY_QUALITY = {"fast": 6, "balanced": 8, "quality": 16}
_DEFAULT_GUIDANCE_BY_QUALITY = {"fast": 2.0, "balanced": 2.2, "quality": 2.5}
DEFAULT_STEPS = int(
    os.environ.get(
        "LOMA_IMAGE_STEPS",
        str(_DEFAULT_STEPS_BY_QUALITY.get(_IMAGE_QUALITY, 8)),
    )
)
DEFAULT_GUIDANCE = float(
    os.environ.get(
        "LOMA_IMAGE_GUIDANCE",
        str(_DEFAULT_GUIDANCE_BY_QUALITY.get(_IMAGE_QUALITY, 2.2)),
    )
)
LCM_NEGATIVE_PROMPT = "deformed, bad anatomy, blurry, low quality, watermark, text, ugly"
# The three SD-family models (DreamShaper 8, Realistic Vision V6.0, SDXL Lightning) share
# SD's known weakness on hands/fingers and face/anatomy — always appended to the negative
# prompt (user-supplied or default) rather than replacing it, so it applies across every SD
# path. Not used for flux_klein_gguf (Flux2KleinPipeline has no negative_prompt param at
# all — see _run_generate_image()).
ANATOMY_NEGATIVE_PROMPT = (
    "extra fingers, fused fingers, missing fingers, malformed hands, mutated hands, "
    "extra limbs, deformed hands, asymmetrical eyes, cross-eyed, deformed face, "
    "distorted face, long neck, disfigured"
)
# Narrow on purpose — nudity/explicit/pornographic terms only, not broad terms like "bare
# skin" or "revealing clothing" that would also suppress legitimate swimwear/beach/medical
# content. This is a soft bias (classifier-free guidance), not a hard block: it reduces
# likelihood, it doesn't guarantee absence, especially at this app's low default guidance
# scale (2.0-2.5) which weakens negative-prompt adherence generally. It exists specifically
# for the class of failure pipeline/image_safety_embeddings.py's text-level check cannot
# catch — confirmed real case: an innocuous prompt ("a serene woman meditating by a lotus
# pond, spiritual, tranquil garden") rendered a topless figure on a presentation slide, with
# no explicit wording anywhere in the prompt text for a text classifier to catch.
NSFW_NEGATIVE_PROMPT = "nudity, naked, nsfw, explicit content, pornographic"


def build_safety_negative_prompt(base: str = "") -> str:
    """Prepends ANATOMY_NEGATIVE_PROMPT + NSFW_NEGATIVE_PROMPT ahead of any
    caller-supplied negative-prompt text, rather than appending them after it —
    CLIP hard-truncates at 77 tokens (confirmed: the combined always-on negative
    prompt alone already used 65 of them), keeping the FIRST 77 and silently
    dropping the rest with no error. Appending safety terms last (the previous
    behavior) meant they were the first thing truncation would drop whenever a
    caller's own negative-prompt text ran long. Every negative_prompt construction
    site (generation, mutation/inpaint) should build through this helper rather
    than concatenating ANATOMY_NEGATIVE_PROMPT ad hoc, so this ordering fix and any
    future safety-term addition apply everywhere at once."""
    parts = [ANATOMY_NEGATIVE_PROMPT, NSFW_NEGATIVE_PROMPT]
    base = (base or "").strip().strip(",").strip()
    if base:
        parts.append(base)
    return ", ".join(parts)


# Positive-side complement to the negative "text, words, ..." suppression (above, and
# services/marker_visual.py's own suppression) — negative prompts and positive scene-
# framing steer through different mechanisms (subtracting vs. reinforcing a concept), so
# this is additive, not redundant. Always applied in _run_generate_image() regardless of
# caller: generate_image() is only ever reached for plain diffusion "photo" output —
# charts/diagrams/infographics use their own deterministic renderers and never call it.
NO_TEXT_POSITIVE_FRAMING = "pure visual scene, photographic, no text, no words"
# FLUX.2 Klein's guidance-distilled pipeline has no negative_prompt param at all (see
# is_flux_klein branch below) — ANATOMY_NEGATIVE_PROMPT above can't apply to it the way
# it does for SD-family, so the same "extra limbs/fingers" failure mode (e.g. a group
# high-five photo rendering a third arm) needs the positive-framing mechanism instead.
FLUX_ANATOMY_POSITIVE_FRAMING = "anatomically correct, natural human proportions, correct number of limbs and fingers"
# FLUX.2 Klein's own training already resists explicit content more than the SD-family
# models do (confirmed this session), but has no negative_prompt param to carry
# NSFW_NEGATIVE_PROMPT's suppression the way SD-family gets it — this positive-side
# equivalent is defense-in-depth, applied everywhere FLUX_ANATOMY_POSITIVE_FRAMING is.
FLUX_SAFETY_POSITIVE_FRAMING = "fully clothed, modest attire, safe for work"
DEFAULT_WIDTH = int(os.environ.get("LOMA_IMAGE_WIDTH", "512"))
DEFAULT_HEIGHT = int(os.environ.get("LOMA_IMAGE_HEIGHT", "512"))
SDXL_DEFAULT_WIDTH = int(os.environ.get("LOMA_IMAGE_SDXL_WIDTH", "1024"))
SDXL_DEFAULT_HEIGHT = int(os.environ.get("LOMA_IMAGE_SDXL_HEIGHT", "1024"))

# Per-model quality mode (Low/High) and resolution presets for the Model Library panel.
# Low = the fast distilled/LCM path (sd15: LCM LoRA + 8 steps). High = the slower,
# better path (sd15: no LCM, DPM++ SDE Karras sampler, ~28 steps) — see _load_pipeline()
# and _resolve_generation_params(). Both sd15 checkpoints (DreamShaper 8, Realistic
# Vision) share the same Low/High mechanics; only the specific checkpoint file differs.
# sdxl_lightning and flux_klein_gguf have no Low/High axis at all (fixed single
# setting) — see quality_modes_for_model().
SD15_LOW_STEPS = 8
SD15_HIGH_STEPS = 28
SD15_HIGH_GUIDANCE = 7.5
LIGHTNING_HIGH_STEPS = 8

# WxH per resolution preset, native to each pipeline family (SD 1.5 vs SDXL base res).
RESOLUTION_PRESETS_SD15 = {
    "square": (512, 512),
    "portrait": (512, 768),
    "landscape": (768, 512),
}
RESOLUTION_PRESETS_SDXL = {
    "square": (1024, 1024),
    "portrait": (896, 1152),
    "landscape": (1152, 896),
}
# FLUX.2 Klein's own example config uses 1024x1024; portrait/landscape reuse SDXL's
# same-megapixel-budget pairing since FLUX has no published preset of its own to cite.
RESOLUTION_PRESETS_FLUX = {
    "square": (1024, 1024),
    "portrait": (896, 1152),
    "landscape": (1152, 896),
}

# FLUX.2 Klein 4B (distilled) has no tunable Low/High mode — see quality_modes_for_model().
# Fixed at BFL's own documented config for the distilled checkpoint: 4 steps,
# guidance_scale 1.0 (guidance-distilled, not classifier-free guidance).
FLUX_KLEIN_STEPS = 4
FLUX_KLEIN_GUIDANCE = 1.0


def quality_modes_for_model(model_id: str) -> dict[str, int]:
    """{"low": steps, "high": steps} — the concrete step count each quality mode
    resolves to for this model, for the Model Library's Low/High selector. Empty dict
    means the model has no Low/High choice at all (Model Library hides the dropdown) —
    currently only flux_klein_gguf, since it's a single fixed-step distilled checkpoint
    with no undistilled/higher-step counterpart in this catalog."""
    family = _pipeline_kind(model_id)
    if family in ("flux_klein_gguf", "sdxl_lightning"):
        # sdxl_lightning: 4-step Low mode was dropped — 4-step and 8-step are
        # indistinguishable enough in practice that a second ~7GB download wasn't
        # worth it (see the catalog entry's own comment). One fixed setting now,
        # same as flux_klein_gguf.
        return {}
    return {"low": SD15_LOW_STEPS, "high": SD15_HIGH_STEPS}


def resolution_presets_for_model(model_id: str) -> dict[str, tuple[int, int]]:
    family = _pipeline_kind(model_id)
    if family == "flux_klein_gguf":
        return RESOLUTION_PRESETS_FLUX
    return RESOLUTION_PRESETS_SDXL if family == "sdxl_lightning" else RESOLUTION_PRESETS_SD15


def resolve_image_presets(
    model_id: str, *, quality_mode: str = "", resolution_preset: str = ""
) -> tuple[int | None, int | None, int | None]:
    """(steps, width, height) for a saved Low/High + resolution preset pair. An unset
    quality_mode/resolution_preset ("") resolves to "low"/"square" (matching what the
    Model Library UI shows as the default selection) rather than falling through to
    None, so an unset preference still gets a deliberate, known-good value instead of
    whatever a caller's own hardcoded fallback happens to be."""
    mode = (quality_mode or "").strip().lower() or "low"
    resolution_key = (resolution_preset or "").strip().lower() or "square"
    steps = quality_modes_for_model(model_id).get(mode)
    wh = resolution_presets_for_model(model_id).get(resolution_key)
    width, height = wh if wh else (None, None)
    _e2e_size = os.environ.get("LOMA_E2E_IMAGE_SIZE", "").strip()
    if _e2e_size.isdigit() and width:
        # CI hook: a tiny test model on a 4-vCPU runner needs a small canvas to finish in minutes.
        width = height = max(64, int(_e2e_size) // 8 * 8)
    return steps, width, height


USE_LCM = os.environ.get("LOMA_IMAGE_USE_LCM", "1").strip().lower() not in (
    "0",
    "false",
    "no",
)
# CPU offload mode: "auto" (default) offloads only when VRAM is too tight to hold the
# checkpoint (full-GPU placement is far faster); "on"/"1" forces it; "off"/"0" disables it.
_offload_raw = os.environ.get("LOMA_IMAGE_CPU_OFFLOAD", "auto").strip().lower()
if _offload_raw in ("1", "true", "yes", "on"):
    CPU_OFFLOAD_MODE = "on"
elif _offload_raw in ("0", "false", "no", "off"):
    CPU_OFFLOAD_MODE = "off"
else:
    CPU_OFFLOAD_MODE = "auto"

_pipe_lock = threading.Lock()
_cached_pipe: Any = None
_cached_key: tuple[str, str, str, bool, str, str] | None = None
_cpu_torch_warned = False

# Clause hints ranked so CLIP truncation keeps girl + cat + swing (start of string).
_SUBJECT_PRIORITY = (
    "lion",
    "tiger",
    "wolf",
    "bear",
    "rabbit",
    "rabbits",
    "hare",
    "deer",
    "prey",
    "predator",
    "chasing",
    "running",
    "pursuing",
    "hunt",
    "attack",
    "cat",
    "kitten",
    "dog",
    "girl",
    "boy",
    "child",
    "playing",
    "swing",
    "backyard",
    "grass",
    "fluffy",
    "orange",
    "golden hour",
    "realistic",
    "detailed",
)


def _agent_log(message: str, data: dict, hypothesis_id: str, *, run_id: str = "pre-fix") -> None:
    # #region agent log
    from pipeline.debug_session import debug_logging_enabled

    if not debug_logging_enabled():
        return  # the log path is inside the install folder (the signed .app on macOS)
    try:
        payload = {
            "sessionId": "d4eaa9",
            "timestamp": int(time.time() * 1000),
            "location": "image_processor/capability.py",
            "message": message,
            "data": data,
            "hypothesisId": hypothesis_id,
            "runId": run_id,
        }
        with _DEBUG_LOG.open("a", encoding="utf-8") as log_file:
            log_file.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion


def _peft_available() -> bool:
    try:
        import peft  # noqa: F401

        return True
    except ImportError:
        return False


def _runtime_diagnostics() -> dict[str, Any]:
    info: dict[str, Any] = {
        "python_executable": sys.executable,
        "peft_available": _peft_available(),
    }
    try:
        import diffusers

        info["diffusers_version"] = diffusers.__version__
    except ImportError as exc:
        info["diffusers_error"] = str(exc)
    try:
        import torch

        info["torch_version"] = torch.__version__
        info["cuda_available"] = torch.cuda.is_available()
    except ImportError as exc:
        info["torch_error"] = str(exc)
    return info


@dataclass
class ImageGenerationResult:
    path: str
    prompt: str
    seed: int
    width: int
    height: int
    steps: int
    guidance_scale: float
    model_id: str
    fallback: bool = False
    error: str = ""
    lora_id: str = ""
    scheduler: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "prompt": self.prompt,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "steps": self.steps,
            "guidance_scale": self.guidance_scale,
            "model_id": self.model_id,
            "lora_id": self.lora_id,
            "scheduler": self.scheduler,
            "fallback": self.fallback,
            "error": self.error,
        }


def normalize_prompt_commas(text: str) -> str:
    """Fix spaced commas from LLM output (hurts CLIP tokenization)."""
    t = (text or "").strip()
    t = re.sub(r"\s*,\s*", ", ", t)
    t = re.sub(r"\s{2,}", " ", t)
    return t.strip(" ,.")


def extract_prompt_from_loma_caption(text: str) -> str | None:
    """Pull diffusion prompt from LOMA Preview caption markdown."""
    m = _LOMA_CAPTION_PROMPT_RE.search(text or "")
    if not m:
        return None
    line = m.group(1).strip()
    line = re.sub(r"\s+·\s+.*$", "", line, count=1)
    line = re.sub(r"\nSeed\s+.*", "", line, flags=re.IGNORECASE)
    return normalize_prompt_commas(line) if line else None


def query_priority_tokens(user_query: str) -> tuple[str, ...]:
    """Nouns/verbs from the user request to boost CLIP clause ordering."""
    words = re.findall(r"[a-zA-Z]{3,}", (user_query or "").lower())
    seen: set[str] = set()
    tokens: list[str] = []
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        tokens.append(w)
    return tuple(tokens[:12])


def prepare_image_prompt(raw: str) -> str:
    """
    Turn LLM/markdown output into a single CLIP-friendly scene description.
    SD 1.5 only sees ~77 tokens — long chatty prompts lose subjects at the end.
    """
    text = (raw or "").strip()
    if not text:
        return "A simple clean illustration"

    caption_prompt = extract_prompt_from_loma_caption(text)
    if caption_prompt:
        return caption_prompt

    text = normalize_prompt_commas(text)
    final_m = re.search(
        r"Final Output:\s*\n+(.+?)(?:\n\n[A-Z]|\n\n✅|\Z)",
        text,
        re.I | re.S,
    )
    if final_m:
        line = final_m.group(1).strip()
        if len(line) >= 12:
            return normalize_prompt_commas(line)
    marker = IMAGE_MARKER_RE.search(text)
    if marker:
        text = marker.group(1).strip()

    blocks: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = re.sub(r"^#+\s*", "", block.strip())
        block = block.strip("*_ ")
        if len(block) < 12:
            continue
        if _META_INSTRUCTION_RE.match(block) and len(block) < 80:
            block = _META_INSTRUCTION_RE.sub("", block).strip()
        if block:
            blocks.append(block)

    if not blocks:
        lines = [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 12]
        blocks = lines or [text]

    prompt = normalize_prompt_commas(max(blocks, key=len))
    prompt = _RESOLUTION_HYPE_RE.sub("", prompt)
    prompt = re.sub(r"\s{2,}", " ", prompt).strip(" ,.")
    return prompt or "A simple clean illustration"


_MULTI_SUBJECT_RE = re.compile(
    r"\b(?:a|an|the)\s+(\w{3,})\s+and\s+(?:(?:a|an|the)\s+)?(\w{3,})\b",
    re.IGNORECASE,
)
# "a lion chasing after a rabbit" etc. — two named subjects linked by an action verb
# instead of "and". Same underlying problem (two distinct named subjects in one scene)
# without the literal "and" _MULTI_SUBJECT_RE requires — predator/prey and confrontation
# phrasing is a very common way to describe exactly this, so it's worth its own pattern
# rather than folding into the "and" one.
_MULTI_SUBJECT_ACTION_RE = re.compile(
    r"\b(?:a|an|the)\s+(\w{3,})\s+(?:is\s+)?"
    r"(?:chas(?:e|ing)|hunt(?:ing)?|attack(?:ing)?|pursu(?:e|ing)|fight(?:ing)?|"
    r"eat(?:ing)?|catch(?:ing)?)\s*(?:after|down)?\s+"
    r"(?:a|an|the)\s+(\w{3,})\b",
    re.IGNORECASE,
)
_MULTI_SUBJECT_SKIP_WORDS = frozenset(
    {"few", "little", "small", "large", "same", "clear", "bright", "warm", "soft"}
)


def looks_like_multi_subject(query: str) -> bool:
    """Cheap heuristic: does this request name two distinct subjects — either joined by
    "and" (e.g. "a tiger and a lion") or by an action verb (e.g. "a lion chasing after a
    rabbit")? The three SD-family models struggle to render two distinct named subjects
    reliably (they tend to duplicate one subject instead) — a documented CLIP text
    conditioning weakness, not shared by FLUX.2 Klein's Qwen3 encoder (see
    pipeline/direct/step_executor.py's caller, which gates the caveat to non-flux
    models) — used to add an honest caveat to the "Image ready" message rather than to
    change generation itself. False positives are low-cost here (just an extra advisory
    line), so this stays a lightweight regex rather than real NLP."""
    text = (query or "").strip()
    if not text:
        return False
    for pattern in (_MULTI_SUBJECT_RE, _MULTI_SUBJECT_ACTION_RE):
        for match in pattern.finditer(text):
            first, second = match.group(1).lower(), match.group(2).lower()
            if first == second:
                continue
            if first in _MULTI_SUBJECT_SKIP_WORDS or second in _MULTI_SUBJECT_SKIP_WORDS:
                continue
            return True
    return False


def _clause_priority(clause: str, boost: tuple[str, ...] = ()) -> int:
    lower = clause.lower()
    score = sum(3 for kw in _SUBJECT_PRIORITY if kw in lower)
    score += sum(4 for tok in boost if tok in lower)
    return score + min(len(clause) // 40, 2)


def _prompt_token_count(prompt: str, tokenizer: Any, max_length: int = 77) -> int:
    try:
        encoded = tokenizer(prompt, truncation=False, return_tensors="pt")
        return int(encoded.input_ids.shape[-1])
    except Exception:
        return len(prompt) // 4


def _compact_for_clip(
    prompt: str,
    tokenizer: Any,
    max_length: int = 77,
    *,
    boost: tuple[str, ...] = (),
) -> str:
    """Reorder comma clauses when over CLIP budget, then truncate."""
    parts = [p.strip() for p in re.split(r",\s*", prompt) if p.strip()]
    needs_reorder = len(parts) > 1 and _prompt_token_count(prompt, tokenizer, max_length) > max_length
    if needs_reorder:
        parts.sort(key=lambda c: _clause_priority(c, boost), reverse=True)
        prompt = ", ".join(parts)
    try:
        encoded = tokenizer(
            prompt,
            truncation=True,
            max_length=max_length,
            return_tensors="pt",
        )
        ids = encoded.input_ids[0].tolist()
        return tokenizer.decode(ids, skip_special_tokens=True).strip()
    except Exception:
        return prompt[:320]


def _effective_steps(steps: int) -> int:
    """Use the requested step count as-is. A previous CPU-only path added +2 steps for a
    marginal quality bump, but each CPU step costs ~15s, so it just made generation
    noticeably slower — and with LCM few-step scheduling the extra steps aren't needed."""
    return steps


_cpu_flux_warned = False


def _warn_cpu_flux_once(steps: int) -> None:
    """FLUX.2 Klein's transformer is a much heavier per-step compute load than the
    SD-family models — on CPU (no CUDA/MPS) this is dramatically slower than on any GPU,
    regardless of VRAM amount (see _load_flux_klein_gguf_pipeline: the text encoder
    always runs on CPU already, so a CPU-only machine only adds the transformer+VAE to
    that). The minute estimate below is a rough order-of-magnitude multiple of SD1.5's
    known ~10-15s/CPU-step, not a benchmarked figure for this model — flagged as such."""
    global _cpu_flux_warned
    if _cpu_flux_warned:
        return
    _cpu_flux_warned = True
    est_minutes = max(1, round(steps * 90 / 60))
    msg = (
        "FLUX.2 Klein 4B has no GPU on this machine, so it's running on CPU only — "
        f"expect roughly {est_minutes}+ minutes for this image, much slower than the "
        "SD-family models (this is an approximate estimate, not a measured benchmark). "
        "A GPU of any VRAM size will run this model far faster; without one, DreamShaper "
        "8 or Realistic Vision will generate images much quicker on CPU."
    )
    try:
        from services.session import state

        state.add_log(msg)
    except Exception:
        print(f"LOMA: {msg}")


def _warn_cpu_torch_once() -> None:
    global _cpu_torch_warned
    if _cpu_torch_warned:
        return
    _cpu_torch_warned = True
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        return
    mps = bool(getattr(getattr(torch, "backends", None), "mps", None) and torch.backends.mps.is_available())
    if mps:
        return
    from services.platform_paths import pip_install_cmd

    if sys.platform == "darwin":
        msg = (
            "Image generation is using CPU PyTorch (no MPS). "
            f"Install Apple Silicon / macOS torch: {pip_install_cmd('torch', 'torchvision')}"
        )
    elif sys.platform == "win32":
        msg = (
            "Image generation is using CPU PyTorch (no CUDA). "
            f"Install GPU PyTorch: {pip_install_cmd('torch', 'torchvision', '--index-url', 'https://download.pytorch.org/whl/cu124')}"
        )
    else:
        msg = (
            "Image generation is using CPU PyTorch (no CUDA). "
            f"Install a CUDA build of torch if you have an NVIDIA GPU: {pip_install_cmd('torch', 'torchvision')}"
        )
    try:
        from services.session import state

        state.add_log(msg)
    except Exception:
        print(f"LOMA: {msg}")
    _agent_log("cpu torch warning", {"torch_version": torch.__version__}, "H1", run_id="post-fix")


def _pipeline_kind(model_id: str) -> str:
    from config.model_catalog import image_pipeline_kind

    return image_pipeline_kind(model_id)


def load_edit_pipeline(model_id: str, sd15_cls, sdxl_cls, *, torch_dtype, quality_mode: str = "high"):
    """Load an img2img/inpaint pipeline for `model_id`, model-family aware.

    SDXL Lightning (`config.model_catalog.image_pipeline_kind() == "sdxl_lightning"`)
    has no `model_index.json` — it's a checkpoint-only HF repo — so `from_pretrained()`
    404s on it (the same distinction `_load_pipeline()` above already makes correctly
    for plain generation). `sd15_cls`/`sdxl_cls` are the diffusers pipeline classes to
    use for each family, e.g. StableDiffusionImg2ImgPipeline /
    StableDiffusionXLImg2ImgPipeline.
    """
    if _pipeline_kind(model_id) == "flux_klein_gguf":
        # `sd15_cls`/`sdxl_cls` are ignored here — FLUX has its own inpaint pipeline
        # class (Flux2KleinInpaintPipeline), not diffusers' generic SD-family classes
        # this function's other branches use. See mutate_remove_text(), the only
        # current caller for this family (there's no img2img-by-strength caller for
        # FLUX yet, only the inpaint-a-detected-region path).
        from services.system.profiler import resolve_torch_device

        device = resolve_torch_device()
        return _load_flux_klein_inpaint_pipeline(model_id, device, torch_dtype)
    if _pipeline_kind(model_id) == "sdxl_lightning":
        from huggingface_hub import hf_hub_download

        from config.model_catalog import image_checkpoint_file

        filename = image_checkpoint_file(model_id, quality_mode)
        if not filename:
            raise ValueError(f"No checkpoint_file configured for {model_id!r} ({quality_mode})")
        try:
            local_path = hf_hub_download(repo_id=model_id, filename=filename, local_files_only=True)
        except Exception:
            local_path = hf_hub_download(repo_id=model_id, filename=filename)
        return sdxl_cls.from_single_file(local_path, torch_dtype=torch_dtype)
    # SD1.5's bundled NSFW safety checker silently replaces a flagged image with a
    # solid black frame instead of raising — no exception, no field anyone here
    # checks, so a false positive looks exactly like a normal successful edit. It's
    # notorious for false-positiving on tight crops of glossy/wet-looking skin-like
    # textures (e.g. a frog's skin) — a real, previously silent failure mode on this
    # local, single-user, offline tool where the user already owns the source photo
    # and the checker provides no actual protection. Disabled here for every SD1.5
    # edit/inpaint pipeline (see also _load_pipeline() below for plain generation).
    try:
        return sd15_cls.from_pretrained(
            model_id, torch_dtype=torch_dtype, local_files_only=True,
            safety_checker=None, requires_safety_checker=False,
        )
    except (OSError, ValueError):
        return sd15_cls.from_pretrained(
            model_id, torch_dtype=torch_dtype,
            safety_checker=None, requires_safety_checker=False,
        )


def place_edit_pipeline_on_device(pipe, device: str, model_id: str = "") -> Any:
    """VRAM-aware placement for an unplaced SD-family img2img/inpaint pipeline —
    same offload/slicing decision `_load_pipeline()` makes for plain txt2img (see
    that function's "Decide placement + memory-saving tricks from VRAM" block).
    Callers (image_edit.py, image_inpaint.py, mutate_remove_text() below) used to
    unconditionally `.to(device)` on cuda regardless of free VRAM, which caused
    silent OS-level VRAM paging (~10x slower per step) on tight cards instead of
    diffusers' own (much faster) enable_model_cpu_offload(). Returns the pipe
    (placement may replace it, matching `.to()`'s return value)."""
    import torch

    sdxl_like = _pipeline_kind(model_id) in ("sdxl", "sdxl_lightning")
    if device == "cuda":
        total_vram = 0.0
        try:
            total_vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
        except Exception:
            pass
        min_vram = 8.0 if sdxl_like else 4.0
        if CPU_OFFLOAD_MODE == "on":
            use_offload = True
        elif CPU_OFFLOAD_MODE == "off":
            use_offload = False
        else:  # auto
            use_offload = 0.0 < total_vram < min_vram
        memory_constrained = use_offload or (0.0 < total_vram < min_vram + 2.0)
    else:
        use_offload = False
        memory_constrained = True  # CPU: always minimize memory footprint

    if memory_constrained:
        pipe.enable_attention_slicing()
        if hasattr(pipe, "enable_vae_slicing"):
            pipe.enable_vae_slicing()

    if device == "cuda" and not use_offload:
        pipe = pipe.to(device)
    elif device == "cuda":
        pipe.enable_model_cpu_offload()
    else:
        pipe = pipe.to(device)
    return pipe


def _resolve_generation_params(
    steps: int | None,
    guidance_scale: float | None,
    width: int | None,
    height: int | None,
    quality_mode: str,
    model_id: str = "",
) -> tuple[int, float, int, int, bool]:
    """Returns (steps, guidance_scale, width, height, lcm_enabled). quality_mode is
    "low" (fast: sd15 LCM LoRA / lightning 4-step) or "high" (sd15 no-LCM DPM++ SDE
    Karras / lightning 8-step) — see quality_modes_for_model(). lcm_enabled is only
    ever True for sd15's low mode; sdxl_lightning never uses the LCM LoRA path."""
    family = _pipeline_kind(model_id)
    mode = (quality_mode or "").strip().lower()
    if mode not in ("low", "high"):
        mode = "low" if USE_LCM else "high"

    if family == "flux_klein_gguf":
        # No Low/High mode — quality_mode is ignored, same fixed distilled config always.
        return (
            steps if steps is not None else FLUX_KLEIN_STEPS,
            guidance_scale if guidance_scale is not None else FLUX_KLEIN_GUIDANCE,
            width if width is not None else RESOLUTION_PRESETS_FLUX["square"][0],
            height if height is not None else RESOLUTION_PRESETS_FLUX["square"][1],
            False,
        )

    if family == "sdxl_lightning":
        # Fixed 8-step (see quality_modes_for_model()) — near-zero CFG is ByteDance's
        # documented recommendation; more guidance does not improve quality here.
        # NOT 0.0, though: diffusers' do_classifier_free_guidance property is
        # `guidance_scale > 1` (verified against StableDiffusionXLPipeline directly) —
        # at exactly 0.0 (or anywhere <= 1), the negative_prompt this app always passes
        # (LCM_NEGATIVE_PROMPT / the default quality negative / this family's own
        # anti-text-in-image suppression in services/marker_visual.py) is silently
        # never applied at all, CFG being the only mechanism negative prompts work
        # through. 1.5 is the lowest value that reliably engages CFG while staying
        # close to ByteDance's near-zero guidance, so the negative prompt actually does
        # something instead of being a no-op.
        return (
            steps if steps is not None else LIGHTNING_HIGH_STEPS,
            guidance_scale if guidance_scale is not None else 1.5,
            width if width is not None else SDXL_DEFAULT_WIDTH,
            height if height is not None else SDXL_DEFAULT_HEIGHT,
            False,
        )

    if mode == "high":
        return (
            steps if steps is not None else SD15_HIGH_STEPS,
            guidance_scale if guidance_scale is not None else SD15_HIGH_GUIDANCE,
            width if width is not None else DEFAULT_WIDTH,
            height if height is not None else DEFAULT_HEIGHT,
            False,
        )

    return (
        steps if steps is not None else DEFAULT_STEPS,
        guidance_scale if guidance_scale is not None else DEFAULT_GUIDANCE,
        width if width is not None else DEFAULT_WIDTH,
        height if height is not None else DEFAULT_HEIGHT,
        True,
    )


def _safe_stem(prompt: str, seed: int = 0) -> str:
    """Filename stem from short user wording (no hash)."""
    del seed  # kept for call-site compatibility
    cleaned = re.sub(r"[^a-z0-9\s]+", " ", (prompt or "").lower())
    words = [w for w in cleaned.split() if w]
    slug = "_".join(words[:8])[:48].strip("_")
    return slug or "image"


def _unique_image_path(stem: str, ext: str) -> str:
    """cats.png, then cats_2.png, cats_3.png, … — never overwrite."""
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    base = os.path.join(GENERATED_IMAGE_DIR, f"{stem}.{ext}")
    if not os.path.exists(base):
        return base
    n = 2
    while True:
        candidate = os.path.join(GENERATED_IMAGE_DIR, f"{stem}_{n}.{ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1
        if n > 9999:
            return os.path.join(GENERATED_IMAGE_DIR, f"{stem}_{n}.{ext}")


def _fallback_file(path: str, prompt: str, error: str, metadata: dict) -> str:
    from services.platform_paths import pip_install_cmd

    fallback_path = os.path.splitext(path)[0] + ".txt"
    with open(fallback_path, "w", encoding="utf-8") as f:
        f.write(
            "LOMA image generation fallback\n\n"
            "Diffusers image generation could not run in this environment.\n"
            "Install deps:\n"
            f"  {pip_install_cmd('-r', 'requirements.txt')}\n"
            "(includes peft, diffusers, torch)\n\n"
            f"Prompt:\n{prompt}\n\n"
            f"Error:\n{error}\n\n"
            f"Metadata:\n{json.dumps(metadata, indent=2)}\n"
        )
    return fallback_path


def _local_snapshot_subfolder(model_id: str, subfolder: str) -> str | None:
    """Local cache path for `subfolder` inside `model_id`'s latest cached snapshot, or
    None if not cached — lets a caller load straight from disk instead of going through
    huggingface_hub's from_pretrained/snapshot_download, whose local_files_only mode
    refuses to serve a snapshot missing ANY repo file (even irrelevant ones never
    downloaded, like README.md) rather than the specific files actually needed."""
    try:
        from huggingface_hub import scan_cache_dir

        for repo in scan_cache_dir().repos:
            if repo.repo_id != model_id:
                continue
            for revision in repo.revisions:
                path = revision.snapshot_path / subfolder
                if path.is_dir():
                    return str(path)
    except Exception:
        pass
    return None


def _build_flux_klein_components(model_id: str, device: str, dtype: Any) -> dict[str, Any]:
    """Build the five components Flux2KleinPipeline / Flux2KleinInpaintPipeline both
    take (identical constructor signature — see mutate_remove_text()'s FLUX branch,
    which reuses this same builder to wrap the inpaint variant instead). Transformer and
    text encoder are separate GGUF files (config.model_catalog.image_gguf_spec()),
    everything else (vae/scheduler/tokenizer) comes from `model_id`'s own repo as small
    full-precision config/weights.

    Fixed placement, not diffusers' generic enable_model_cpu_offload(): the text encoder
    (Qwen3-4B, large even quantized once transformers dequantizes GGUF weights at load
    time) stays on CPU permanently, since it only runs once per image to produce prompt
    embeddings — see _run_generate_image()'s flux branch, which calls
    Flux2KleinPipeline._get_qwen3_prompt_embeds() directly against the CPU-resident
    encoder and moves only the resulting (small) embedding tensor to GPU. The
    transformer and VAE — which run every denoising step — stay fully GPU-resident the
    whole time. This is deliberately cheaper than generic offload, which would also
    shuttle the transformer itself between devices on every one of the 4 steps."""
    import torch
    from diffusers import (
        AutoencoderKLFlux2,
        Flux2Transformer2DModel,
        FlowMatchEulerDiscreteScheduler,
        GGUFQuantizationConfig,
    )
    from huggingface_hub import hf_hub_download
    from transformers import AutoTokenizer, Qwen3ForCausalLM

    from config.model_catalog import image_gguf_spec

    spec = image_gguf_spec(model_id)
    if not spec:
        raise ValueError(f"No gguf_repo/text_encoder_repo configured for {model_id!r}")

    try:
        transformer_path = hf_hub_download(
            repo_id=spec["gguf_repo"], filename=spec["gguf_file"], local_files_only=True
        )
    except Exception:
        transformer_path = hf_hub_download(repo_id=spec["gguf_repo"], filename=spec["gguf_file"])

    transformer = Flux2Transformer2DModel.from_single_file(
        transformer_path,
        # Without an explicit config, from_single_file()'s architecture-inference
        # heuristic resolves this checkpoint shape to the gated black-forest-labs/
        # FLUX.2-dev repo (401 Unauthorized) instead of Klein's own config — pin it
        # explicitly to model_id's transformer subfolder.
        config=model_id,
        subfolder="transformer",
        quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
        torch_dtype=dtype,
    )
    # fp32, not `dtype` (fp16 on CUDA machines) — this module runs on CPU only, where
    # fp16 kernels are often unsupported or slow.
    try:
        text_encoder = Qwen3ForCausalLM.from_pretrained(
            spec["text_encoder_repo"], gguf_file=spec["text_encoder_file"],
            torch_dtype=torch.float32, local_files_only=True,
        )
    except (OSError, ValueError):
        text_encoder = Qwen3ForCausalLM.from_pretrained(
            spec["text_encoder_repo"], gguf_file=spec["text_encoder_file"], torch_dtype=torch.float32
        )
    # AutoTokenizer.from_pretrained(..., local_files_only=True) runs huggingface_hub's
    # whole-repo completeness check, which fails if ANY repo file is missing locally —
    # even irrelevant ones (README.md, LICENSE.md, .gitattributes) that were never
    # downloaded since only the needed subfolders were pulled. diffusers' own loaders
    # (vae/scheduler below) resolve per-file instead and don't hit this. Route around it
    # by resolving straight to the cached snapshot folder on disk when one exists.
    local_tokenizer_dir = _local_snapshot_subfolder(model_id, "tokenizer")
    try:
        tokenizer = AutoTokenizer.from_pretrained(
            local_tokenizer_dir or model_id, subfolder="" if local_tokenizer_dir else "tokenizer",
            local_files_only=True,
        )
    except (OSError, ValueError):
        tokenizer = AutoTokenizer.from_pretrained(model_id, subfolder="tokenizer")
    try:
        vae = AutoencoderKLFlux2.from_pretrained(
            model_id, subfolder="vae", torch_dtype=dtype, local_files_only=True
        )
    except (OSError, ValueError):
        vae = AutoencoderKLFlux2.from_pretrained(model_id, subfolder="vae", torch_dtype=dtype)
    try:
        scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(
            model_id, subfolder="scheduler", local_files_only=True
        )
    except (OSError, ValueError):
        scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(model_id, subfolder="scheduler")

    transformer = transformer.to(device)
    vae = vae.to(device)
    # text_encoder deliberately NOT moved to `device` — stays on CPU permanently.
    return {
        "scheduler": scheduler,
        "vae": vae,
        "text_encoder": text_encoder,
        "tokenizer": tokenizer,
        "transformer": transformer,
    }


def _load_flux_klein_gguf_pipeline(model_id: str, device: str, dtype: Any) -> tuple[Any, str, str]:
    """Text-to-image FLUX.2 Klein 4B pipeline — see _build_flux_klein_components()."""
    from diffusers import Flux2KleinPipeline

    components = _build_flux_klein_components(model_id, device, dtype)
    pipe = Flux2KleinPipeline(**components, is_distilled=True)
    return pipe, "", "FlowMatchEulerDiscreteScheduler"


def _load_flux_klein_inpaint_pipeline(model_id: str, device: str, dtype: Any) -> Any:
    """Inpaint FLUX.2 Klein 4B pipeline for mutate_remove_text()'s FLUX branch —
    Flux2KleinInpaintPipeline takes the exact same five components as the text-to-image
    Flux2KleinPipeline (confirmed via its constructor signature), so this is the same
    component build, just wrapped in the inpaint pipeline class instead."""
    from diffusers import Flux2KleinInpaintPipeline

    components = _build_flux_klein_components(model_id, device, dtype)
    return Flux2KleinInpaintPipeline(**components, is_distilled=True)


def _load_pipeline(
    model_id: str, lora_id: str, use_lcm: bool, quality_mode: str = "low"
) -> tuple[Any, str, str]:
    """Load or return cached Diffusers pipeline: "sd15" (LCM LoRA for "low", DPM++ SDE
    Karras sampler for "high") or "sdxl_lightning" (single merged .safetensors
    checkpoint, fixed 8-step — no quality_mode axis, see quality_modes_for_model())."""
    global _cached_pipe, _cached_key

    family = _pipeline_kind(model_id)
    mode = (quality_mode or "").strip().lower()
    if mode not in ("low", "high"):
        mode = "low"
    sdxl_like = family == "sdxl_lightning"

    lcm_mode = ""
    load_model_id = model_id
    if family == "sd15":
        lcm_mode = "lora" if (use_lcm and lora_id and _peft_available()) else ""
        if use_lcm and lora_id and not _peft_available():
            lcm_mode = "merged"
        if lcm_mode == "merged":
            load_model_id = DEFAULT_LCM_MERGED_MODEL or model_id

    use_lcm_key = use_lcm and family != "sdxl_lightning"
    key = (
        family,
        load_model_id,
        lora_id if lcm_mode == "lora" else "",
        use_lcm_key,
        lcm_mode,
        "",
    )
    with _pipe_lock:
        if _cached_pipe is not None and _cached_key == key:
            if lcm_mode in ("lora", "merged"):
                scheduler_name = "LCMScheduler"
            elif family == "sd15" and mode == "high":
                scheduler_name = "DPMSolverMultistepScheduler"
            else:
                scheduler_name = type(_cached_pipe.scheduler).__name__
            active = lora_id if lcm_mode == "lora" else ""
            return _cached_pipe, active, scheduler_name

        _agent_log(
            "pipeline load start",
            {
                **_runtime_diagnostics(),
                "requested_model": model_id,
                "load_model_id": load_model_id,
                "pipeline_family": family,
                "quality_mode": mode,
                "lora_id": lora_id,
                "lcm_mode": lcm_mode or "none",
            },
            "H1-H2",
        )

        import torch

        from services.system.profiler import resolve_torch_device

        device = resolve_torch_device()
        dtype = torch.float16 if device == "cuda" else torch.float32

        if family == "flux_klein_gguf":
            # Own loader entirely — no safety-checker kwargs (Flux2KleinPipeline has none),
            # no LCM/LoRA, and no CPU-offload/attention-slicing block below (see
            # _load_flux_klein_gguf_pipeline's docstring for why offload is skipped here).
            pipe, active_lora, scheduler_name = _load_flux_klein_gguf_pipeline(
                load_model_id, device, dtype
            )
            _cached_pipe = pipe
            _cached_key = key
            _agent_log(
                "pipeline load done",
                {
                    "load_model_id": load_model_id,
                    "pipeline_family": family,
                    "scheduler": scheduler_name,
                    "device": device,
                },
                "H3",
            )
            return pipe, active_lora, scheduler_name

        # See load_edit_pipeline()'s comment above — SD1.5's bundled NSFW safety
        # checker silently substitutes a solid black image instead of raising, an
        # undetected failure mode disabled here too for the plain generation path.
        load_kwargs: dict[str, Any] = {
            "torch_dtype": dtype, "safety_checker": None, "requires_safety_checker": False,
        }
        if device == "cuda":
            load_kwargs["variant"] = "fp16"

        if family == "sdxl_lightning":
            from diffusers import EulerDiscreteScheduler, StableDiffusionXLPipeline
            from huggingface_hub import hf_hub_download

            from config.model_catalog import image_checkpoint_file

            filename = image_checkpoint_file(model_id, mode)
            if not filename:
                raise ValueError(f"No checkpoint_file configured for {model_id!r} ({mode})")
            try:
                local_path = hf_hub_download(repo_id=model_id, filename=filename, local_files_only=True)
            except Exception:
                local_path = hf_hub_download(repo_id=model_id, filename=filename)

            single_file_kwargs: dict[str, Any] = {"torch_dtype": dtype}
            pipe = StableDiffusionXLPipeline.from_single_file(local_path, **single_file_kwargs)
            pipe.scheduler = EulerDiscreteScheduler.from_config(
                pipe.scheduler.config, timestep_spacing="trailing"
            )
            scheduler_name = "EulerDiscreteScheduler"
            active_lora = ""
        else:
            from diffusers import LCMScheduler, StableDiffusionPipeline

            try:
                pipe = StableDiffusionPipeline.from_pretrained(
                    load_model_id, local_files_only=True, **load_kwargs
                )
            except (OSError, ValueError):
                # Many community checkpoints only ever publish fp32 weights — a missing
                # fp16 "variant" raises ValueError (not OSError), and diffusers reports it
                # as "no such modeling files are available" even when the repo itself is
                # fully cached. Drop `variant` and retry locally (no network) before
                # falling back to a remote fetch.
                local_kwargs = {k: v for k, v in load_kwargs.items() if k != "variant"}
                try:
                    pipe = StableDiffusionPipeline.from_pretrained(
                        load_model_id, local_files_only=True, **local_kwargs
                    )
                except (OSError, ValueError):
                    try:
                        pipe = StableDiffusionPipeline.from_pretrained(load_model_id, **load_kwargs)
                    except (OSError, ValueError):
                        load_kwargs.pop("variant", None)
                        pipe = StableDiffusionPipeline.from_pretrained(load_model_id, **load_kwargs)

            scheduler_name = type(pipe.scheduler).__name__
            active_lora = ""

            if use_lcm and lcm_mode in ("lora", "merged"):
                pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)
                scheduler_name = "LCMScheduler"
                if lcm_mode == "lora":
                    # Offline mode can't list the repo to discover its LoRA filename, so
                    # local_files_only alone raises "you must specify a weight_name" even
                    # when the file is fully cached — try the standard diffusers LoRA
                    # training output name first, then fall back for repos that differ.
                    try:
                        pipe.load_lora_weights(
                            lora_id, weight_name="pytorch_lora_weights.safetensors",
                            local_files_only=True,
                        )
                    except (OSError, ValueError):
                        try:
                            pipe.load_lora_weights(lora_id, local_files_only=True)
                        except (OSError, ValueError):
                            pipe.load_lora_weights(lora_id)
                    pipe.fuse_lora()
                    active_lora = lora_id
            elif mode == "high":
                # "High" quality mode: no LCM — proper DPM++ SDE Karras sampler at the
                # step count each checkpoint's own model card recommends (see
                # SD15_HIGH_STEPS), instead of whatever scheduler the checkpoint ships
                # with by default.
                from diffusers import DPMSolverMultistepScheduler

                pipe.scheduler = DPMSolverMultistepScheduler.from_config(
                    pipe.scheduler.config,
                    algorithm_type="sde-dpmsolver++",
                    use_karras_sigmas=True,
                )
                scheduler_name = "DPMSolverMultistepScheduler"

        # Decide placement + memory-saving tricks from VRAM. Full-GPU placement is
        # dramatically faster than CPU offload (which shuttles weights over PCIe every
        # step); attention/VAE slicing likewise trade compute speed for memory. Only use
        # them when actually memory-constrained — SDXL-scale needs ~8GB VRAM, SD1.5 ~4GB.
        if device == "cuda":
            total_vram = 0.0
            try:
                total_vram = torch.cuda.get_device_properties(0).total_memory / (1024**3)
            except Exception:
                pass
            min_vram = 8.0 if sdxl_like else 4.0
            if CPU_OFFLOAD_MODE == "on":
                use_offload = True
            elif CPU_OFFLOAD_MODE == "off":
                use_offload = False
            else:  # auto
                use_offload = 0.0 < total_vram < min_vram
            # Slice only when tight (offloading, or little headroom above the model size).
            memory_constrained = use_offload or (0.0 < total_vram < min_vram + 2.0)
        else:
            use_offload = False
            memory_constrained = True  # CPU: always minimize memory footprint

        if memory_constrained:
            pipe.enable_attention_slicing()
            if hasattr(pipe, "enable_vae_slicing"):
                pipe.enable_vae_slicing()

        if device == "cuda" and not use_offload:
            pipe = pipe.to(device)
        elif device == "cuda":
            pipe.enable_model_cpu_offload()
        else:
            pipe = pipe.to(device)

        _cached_pipe = pipe
        _cached_key = key
        _agent_log(
            "pipeline load done",
            {
                "load_model_id": load_model_id,
                "pipeline_family": family,
                "lcm_mode": lcm_mode or "none",
                "scheduler": scheduler_name,
                "device": device,
            },
            "H3",
        )
        return pipe, active_lora, scheduler_name


def pipeline_is_loaded() -> bool:
    """True when a diffusion pipeline is cached in memory."""
    return _cached_pipe is not None


def unload_pipeline() -> None:
    """Release cached pipeline VRAM (e.g. before heavy LLM workloads)."""
    global _cached_pipe, _cached_key
    with _pipe_lock:
        _cached_pipe = None
        _cached_key = None
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        else:
            backends = getattr(torch, "backends", None)
            mps = getattr(backends, "mps", None) if backends else None
            if mps is not None and mps.is_available() and hasattr(torch, "mps"):
                torch.mps.empty_cache()
    except Exception:
        pass


def run_pipe_with_progress(pipe, *, progress_cb=None, total_steps: int = 0, **kwargs):
    """Call a diffusers pipeline, streaming per-step progress via progress_cb(step, total).

    Tries the modern `callback_on_step_end` first, falls back to the legacy
    `callback`/`callback_steps` signature, then to a plain call — so it works across
    diffusers versions and pipeline types (txt2img / img2img / inpaint)."""
    if progress_cb is None:
        return pipe(**kwargs)

    def _on_step_end(_pipe, step, _timestep, cbk):
        try:
            progress_cb(int(step) + 1, total_steps)
        except ImageGenerationCancelled:
            # Diffusers' own supported early-stop switch (checked once per denoising
            # loop iteration) — set it in addition to re-raising, so even a pipeline
            # variant that somehow swallows the propagated exception still stops ASAP.
            _pipe._interrupt = True
            raise
        except Exception:
            pass
        return cbk

    try:
        return pipe(callback_on_step_end=_on_step_end, **kwargs)
    except TypeError:
        pass

    def _legacy(step, _timestep, _latents):
        try:
            progress_cb(int(step) + 1, total_steps)
        except ImageGenerationCancelled:
            pipe._interrupt = True
            raise
        except Exception:
            pass

    try:
        return pipe(callback=_legacy, callback_steps=1, **kwargs)
    except TypeError:
        return pipe(**kwargs)


def generate_image(
    prompt: str,
    output_path: str | None = None,
    *,
    negative_prompt: str = "",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    guidance_scale: float | None = None,
    seed: int | None = None,
    model_id: str | None = None,
    quality_mode: str | None = None,
    use_lcm: bool | None = None,
    output_format: str = "png",
    name_hint: str | None = None,
    progress_cb=None,
) -> ImageGenerationResult:
    """Generate an image with SD 1.5 (+ optional LCM LoRA) or write a metadata fallback file.
    `quality_mode` is "low" (fast: sd15 LCM LoRA / lightning 4-step) or "high" (sd15
    DPM++ SDE Karras sampler / lightning 8-step) — see quality_modes_for_model().
    `use_lcm` is a legacy alias kept for callers that predate quality_mode: True/False
    map to "low"/"high" when quality_mode itself isn't given.
    `output_format` (png/jpg/jpeg/webp/bmp) only applies when output_path is not given —
    an explicit output_path's own extension always wins.
    `name_hint` (e.g. the user's short query) is preferred for the auto filename stem.
    """
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    raw_prompt = (prompt or "").strip()
    prompt = prepare_image_prompt(raw_prompt)

    if not model_id:
        try:
            from services.model_router import get_default_image_model_from_settings

            model_id = get_default_image_model_from_settings()
        except Exception:
            model_id = DEFAULT_MODEL_ID
    model_id = model_id or DEFAULT_MODEL_ID
    family = _pipeline_kind(model_id)
    mode = (quality_mode or "").strip().lower()
    if mode not in ("low", "high"):
        mode = "low" if use_lcm is None or use_lcm else "high"
    steps, guidance_scale, width, height, lcm_enabled = _resolve_generation_params(
        steps, guidance_scale, width, height, mode, model_id
    )
    lora_id = (DEFAULT_LORA_ID or "").strip() if (family == "sd15" and lcm_enabled) else ""

    seed = int(
        seed if seed is not None else int(hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:8], 16)
    )
    if output_path is None:
        ext = (output_format or "png").strip().lower().lstrip(".") or "png"
        if ext not in ("png", "jpg", "jpeg", "webp", "bmp"):
            ext = "png"
        hint = (name_hint or "").strip()
        # Prefer short user wording over the expanded diffusion prompt for filenames.
        if hint and len(hint) <= 120:
            stem_src = hint
        elif raw_prompt and len(raw_prompt) <= len(prompt) and len(raw_prompt) <= 120:
            stem_src = raw_prompt
        else:
            stem_src = " ".join((raw_prompt or prompt).split()[:8])
        output_path = _unique_image_path(_safe_stem(stem_src, seed), ext)
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    metadata = {
        "raw_prompt_len": len(raw_prompt),
        "prompt_len": len(prompt),
        "image_quality": _IMAGE_QUALITY,
        "seed": seed,
        "width": width,
        "height": height,
        "steps": steps,
        "guidance_scale": guidance_scale,
        "model_id": model_id,
        "lora_id": lora_id if lcm_enabled else "",
        "use_lcm": lcm_enabled,
        "pipeline_family": family,
    }

    try:
        from services.resource_governor import ResourceGovernor

        with ResourceGovernor.acquire("image_gen"):
            return _run_generate_image(
                prompt=prompt,
                output_path=output_path,
                raw_prompt=raw_prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                steps=steps,
                guidance_scale=guidance_scale,
                seed=seed,
                model_id=model_id,
                lora_id=lora_id,
                lcm_enabled=lcm_enabled,
                quality_mode=mode,
                metadata=metadata,
                progress_cb=progress_cb,
            )
    except ImageGenerationCancelled:
        raise
    except Exception as ex:
        error = str(ex)
        fallback_path = _fallback_file(output_path, prompt, error, metadata)
        return ImageGenerationResult(
            fallback_path,
            prompt,
            seed,
            width,
            height,
            steps,
            guidance_scale,
            model_id,
            lora_id=lora_id if lcm_enabled else "",
            fallback=True,
            error=error,
        )


def generate_image_verified(
    prompt: str,
    output_path: str | None = None,
    *,
    max_reseeds: int = 1,
    on_reseed: Any = None,
    **kwargs: Any,
) -> tuple[ImageGenerationResult, bool]:
    """generate_image(), then — for a real, non-fallback result — checks the output with
    services.image_text_detection.has_rendered_text() and reseeds up to `max_reseeds`
    times if garbled/hallucinated text is detected.

    on_reseed(attempt, max_reseeds), if given, is called right before each retry
    generation starts — the only per-attempt signal this function emits. Without it,
    a caller showing live per-step diffusion progress has nothing to show during the
    gap between one attempt finishing and the next attempt's first step (model
    reload, VRAM acquisition), so the UI is left displaying the previous attempt's
    stale "step N/N (100%)" the whole time a fresh multi-second generation is
    actually running underneath it.

    Returns (final_result, still_has_text). `still_has_text` is True only when every
    attempt, including all reseeds, still had detected text — the caller decides what
    "out of attempts" means: discard the image (a document/slide can skip a visual
    entirely — see pipeline/deliverables/presentation_compile.py's own image_desc
    handling) or fall back to mutate_remove_text() for a standalone request, where
    discarding isn't an acceptable outcome.

    Skips verification (returns (result, False) unconditionally) when the text
    detector's dependencies aren't available, or the initial generation already
    failed — a missing optional dependency degrades to "no check", not a hard block
    on generating images at all. Each reseed uses an explicit random seed, not
    generate_image()'s own prompt-hash default, which would otherwise reproduce the
    identical (still-bad) image on every "retry"."""
    result = generate_image(prompt, output_path, **kwargs)

    from services.image_text_detection import has_rendered_text, text_detection_deps_available

    deps_ok, _ = text_detection_deps_available()
    if result.fallback or not deps_ok:
        return result, False

    attempt = 0
    while True:
        try:
            from PIL import Image

            with Image.open(result.path) as img:
                text_found = has_rendered_text(img)
        except Exception:
            # Detector failing shouldn't block shipping an otherwise-fine image.
            return result, False
        if not text_found:
            return result, False
        if attempt >= max_reseeds:
            return result, True
        attempt += 1
        if on_reseed is not None:
            try:
                on_reseed(attempt, max_reseeds)
            except Exception:
                pass
        import random

        retry_kwargs = dict(kwargs)
        retry_kwargs["seed"] = random.randint(1, 2**31 - 1)
        result = generate_image(prompt, output_path, **retry_kwargs)
        if result.fallback:
            return result, False


def mutate_remove_text(image_path: str, prompt: str, *, model_id: str, output_path: str | None = None) -> ImageGenerationResult:
    """Last-resort remediation for generate_image_verified()'s standalone-request case
    (see its docstring): inpaint over the region(s) services.image_text_detection
    flagged as garbled/hallucinated text, rather than reseeding indefinitely or shipping
    a bad image outright. Works for both SD-family (via load_edit_pipeline()'s existing
    StableDiffusionInpaintPipeline/StableDiffusionXLInpaintPipeline branches) and FLUX
    (load_edit_pipeline()'s flux_klein_gguf branch → Flux2KleinInpaintPipeline).

    Returns the ORIGINAL image (unchanged, fallback=False) if the detector no longer
    sees any text — nothing to fix, e.g. if called speculatively. Returns the original
    image with fallback=True (not a hard error) if the mask/pipeline load/generation
    itself fails — a failed mutation attempt should degrade to "here's the original,
    imperfect as it is", since the caller already has a real image in hand either way."""
    from PIL import Image

    from services.image_text_detection import text_region_mask

    with Image.open(image_path) as im:
        image = im.convert("RGB")
    mask = text_region_mask(image)
    if mask is None:
        return ImageGenerationResult(image_path, prompt, 0, image.width, image.height, 0, 0.0, model_id)

    if output_path is None:
        output_path = unique_output_path(image_path, "detextified")

    from services.marker_visual import _strip_text_in_image_instructions

    cleaned_prompt = _strip_text_in_image_instructions(prompt) or prompt
    family = _pipeline_kind(model_id)
    full_prompt = f"{cleaned_prompt}, {NO_TEXT_POSITIVE_FRAMING}"
    if family == "flux_klein_gguf":
        full_prompt = f"{full_prompt}, {FLUX_ANATOMY_POSITIVE_FRAMING}, {FLUX_SAFETY_POSITIVE_FRAMING}"

    try:
        import random

        import torch
        from diffusers import StableDiffusionInpaintPipeline, StableDiffusionXLInpaintPipeline

        from services.resource_governor import ResourceGovernor
        from services.system.profiler import resolve_torch_device

        device = resolve_torch_device()
        dtype = torch.float16 if device == "cuda" else torch.float32
        with ResourceGovernor.acquire("image_gen"):
            # load_edit_pipeline() below always builds its OWN separate pipeline object
            # (a different diffusers class than the plain txt2img one), so a still-warm
            # cached txt2img pipe from the grace-window keepalive (see
            # resource_governor.py's grace-release mechanism, which deliberately keeps it
            # resident across back-to-back generations for speed) would sit in VRAM
            # alongside this new edit pipeline instead of being replaced by it — doubling
            # VRAM footprint and forcing OS-level paging on tight cards (observed: ~40x
            # slower per step, the same symptom place_edit_pipeline_on_device() fixes for
            # the *offload* side of this, but that fix alone doesn't help when it's a
            # second whole pipeline, not just one mis-placed pipeline, competing for VRAM).
            unload_pipeline()
            pipe = load_edit_pipeline(
                model_id, StableDiffusionInpaintPipeline, StableDiffusionXLInpaintPipeline, torch_dtype=dtype,
            )
            if family != "flux_klein_gguf":
                # FLUX's loader already places its own components on-device (see
                # _load_flux_klein_inpaint_pipeline) — SD-family's load_edit_pipeline()
                # returns an unplaced pipeline, matching every other caller of it
                # (services/image_edit.py, services/image_inpaint.py).
                pipe = place_edit_pipeline_on_device(pipe, device, model_id)

            generator = torch.Generator(device=device).manual_seed(random.randint(1, 2**31 - 1))
            pipe_kwargs: dict[str, Any] = {
                "image": image,
                "mask_image": mask,
                "generator": generator,
                # Close to 1.0: this is "erase and replace the masked region", not a
                # light touch-up — low strength would leave garbled fragments blended in.
                "strength": 0.99,
            }
            if family == "flux_klein_gguf":
                # Same device split as _run_generate_image()'s flux branch: the text
                # encoder lives permanently on CPU (see _build_flux_klein_components),
                # but Flux2KleinInpaintPipeline.__call__ would otherwise re-invoke its
                # own encode_prompt() against self._execution_device (the GPU, since
                # transformer/vae live there), moving input_ids onto CUDA before
                # handing them to the CPU-resident text_encoder — a device mismatch.
                # Precomputing prompt_embeds here the same way skips that internal
                # call entirely.
                prompt_embeds = pipe._get_qwen3_prompt_embeds(
                    text_encoder=pipe.text_encoder,
                    tokenizer=pipe.tokenizer,
                    prompt=[full_prompt],
                    device=torch.device("cpu"),
                    max_sequence_length=512,
                )
                pipe_kwargs["prompt"] = None
                pipe_kwargs["prompt_embeds"] = prompt_embeds.to(device=device, dtype=pipe.transformer.dtype)
                pipe_kwargs.update(num_inference_steps=FLUX_KLEIN_STEPS, guidance_scale=FLUX_KLEIN_GUIDANCE)
            else:
                pipe_kwargs["prompt"] = full_prompt
                # SDXL Lightning is a *distilled* checkpoint tuned for a fixed ~8-step,
                # low-guidance regime (see LIGHTNING_HIGH_STEPS / _resolve_generation_params)
                # — forcing it through a generic undistilled SDXL's 30-step/cfg=7.5 config
                # (as this used to do unconditionally) doesn't just waste ~4x the compute
                # per mutation, a distilled model run outside its trained step count can
                # come out worse, not better. sd15 has no such distillation concern, so it
                # keeps the higher-quality 30-step/cfg=7.5 config.
                if family == "sdxl_lightning":
                    mutate_steps, mutate_guidance = LIGHTNING_HIGH_STEPS, 1.5
                else:
                    mutate_steps, mutate_guidance = 30, 7.5
                pipe_kwargs.update(
                    num_inference_steps=mutate_steps,
                    guidance_scale=mutate_guidance,
                    negative_prompt=build_safety_negative_prompt("text, words, letters"),
                )
            result = run_pipe_with_progress(pipe, **pipe_kwargs)
        out = result.images[0]
        out.save(output_path)
        return ImageGenerationResult(
            output_path,
            cleaned_prompt,
            0,
            out.width,
            out.height,
            pipe_kwargs.get("num_inference_steps", 0),
            pipe_kwargs.get("guidance_scale", 0.0),
            model_id,
        )
    except Exception as ex:
        _agent_log("mutate_remove_text failed", {"error": str(ex), "model_id": model_id}, "MUTATE")
        return ImageGenerationResult(
            image_path, prompt, 0, image.width, image.height, 0, 0.0, model_id,
            fallback=True, error=str(ex),
        )


def _run_generate_image(
    *,
    prompt: str,
    output_path: str,
    raw_prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    guidance_scale: float,
    seed: int,
    model_id: str,
    lora_id: str,
    lcm_enabled: bool,
    quality_mode: str,
    metadata: dict,
    progress_cb=None,
) -> ImageGenerationResult:
    try:
        if os.environ.get("LOMA_IMAGE_DISABLE_DIFFUSERS") == "1":
            raise RuntimeError("Diffusers disabled by LOMA_IMAGE_DISABLE_DIFFUSERS=1")

        _warn_cpu_torch_once()
        _agent_log("generate_image start", _runtime_diagnostics(), "H1")

        import torch

        steps = _effective_steps(steps)
        metadata["steps"] = steps

        pipe, active_lora, scheduler_name = _load_pipeline(
            model_id, lora_id if lcm_enabled else "", lcm_enabled, quality_mode
        )
        family = _pipeline_kind(model_id)
        is_flux_klein = family == "flux_klein_gguf"

        if is_flux_klein:
            # Qwen2Tokenizer + Qwen3 text encoder handle up to 512 tokens (see
            # Flux2KleinPipeline's max_sequence_length) — CLIP's 77-token compaction
            # below doesn't apply and would truncate prompts far more aggressively
            # than this encoder needs. Plenty of headroom to just append the framing.
            clip_prompt = (
                f"{prompt}, {NO_TEXT_POSITIVE_FRAMING}, "
                f"{FLUX_ANATOMY_POSITIVE_FRAMING}, {FLUX_SAFETY_POSITIVE_FRAMING}"
            )
        else:
            tokenizer = getattr(pipe, "tokenizer", None)
            if tokenizer is None and getattr(pipe, "tokenizer_2", None) is not None:
                tokenizer = pipe.tokenizer_2
            boost: tuple[str, ...] = ()
            try:
                from services.session import state as _loma_state

                boost = query_priority_tokens(
                    getattr(_loma_state, "last_image_user_query", "") or ""
                )
            except Exception:
                boost = ()

            # Protect the "no text" framing from CLIP's 77-token truncation the same way
            # boost already protects the user's own priority subjects — appended plain,
            # it would be the first thing cut whenever the prompt is already near the
            # limit, which defeats the point of adding it.
            boost = boost + ("pure", "visual", "scene", "photographic")
            prompt_with_framing = f"{prompt}, {NO_TEXT_POSITIVE_FRAMING}"
            if tokenizer is not None:
                clip_prompt = _compact_for_clip(prompt_with_framing, tokenizer, boost=boost)
            else:
                clip_prompt = prompt_with_framing

        from services.system.profiler import resolve_torch_device

        device = resolve_torch_device()
        if is_flux_klein and device == "cpu":
            _warn_cpu_flux_once(steps)
        generator = torch.Generator(device=device).manual_seed(seed)

        pipe_kwargs: dict[str, Any] = {
            "prompt": clip_prompt,
            "width": width,
            "height": height,
            "num_inference_steps": steps,
            "guidance_scale": guidance_scale,
            "generator": generator,
        }
        if is_flux_klein:
            # Flux2KleinPipeline.__call__ has no `negative_prompt` string param (only
            # negative_prompt_embeds) — it's guidance-distilled, so the SD-family
            # anatomy/negative-prompt convention below doesn't apply here.
            neg = ""
            # Text encoder lives permanently on CPU (see _load_flux_klein_gguf_pipeline);
            # __call__ always re-invokes encode_prompt() internally, but encode_prompt
            # only touches the text encoder when prompt_embeds is None — so computing it
            # here ourselves, once, against the CPU-resident encoder, then moving only the
            # small resulting tensor to GPU, means __call__'s own encode_prompt call
            # becomes a no-op and the text encoder never needs to move devices at all.
            prompt_embeds = pipe._get_qwen3_prompt_embeds(
                text_encoder=pipe.text_encoder,
                tokenizer=pipe.tokenizer,
                prompt=[clip_prompt],
                device=torch.device("cpu"),
                max_sequence_length=512,
            )
            pipe_kwargs["prompt"] = None
            pipe_kwargs["prompt_embeds"] = prompt_embeds.to(device=device, dtype=pipe.transformer.dtype)
        else:
            neg = (negative_prompt or "").strip()
            if lcm_enabled and not neg:
                neg = LCM_NEGATIVE_PROMPT
            elif not neg:
                neg = "blurry, low quality, distorted, watermark, text"
            pipe_kwargs["negative_prompt"] = build_safety_negative_prompt(neg)

        _agent_log(
            "generate_image params",
            {
                "raw_prompt_preview": raw_prompt[:120],
                "prepared_prompt": prompt[:200],
                "clip_prompt": clip_prompt,
                "clip_has_cat": "cat" in clip_prompt.lower(),
                "clip_has_swing": "swing" in clip_prompt.lower(),
                "steps": steps,
                "guidance_scale": guidance_scale,
                "width": width,
                "height": height,
                "cuda_available": device == "cuda",
                "image_quality": _IMAGE_QUALITY,
            },
            "H2-H4",
            run_id="post-fix",
        )

        result = run_pipe_with_progress(
            pipe,
            progress_cb=progress_cb,
            total_steps=steps,
            **pipe_kwargs,
        )
        image = result.images[0]
        if output_path.lower().endswith((".jpg", ".jpeg")) and image.mode in ("RGBA", "P", "LA"):
            image = image.convert("RGB")
        image.save(output_path)

        _agent_log(
            "generate_image success",
            {"path": os.path.basename(output_path), "scheduler": scheduler_name},
            "H3",
            run_id="post-fix",
        )

        return ImageGenerationResult(
            output_path,
            clip_prompt,
            seed,
            width,
            height,
            steps,
            guidance_scale,
            model_id,
            lora_id=active_lora,
            scheduler=scheduler_name,
        )
    except ImageGenerationCancelled:
        raise
    except Exception as ex:
        error = str(ex)
        _agent_log(
            "generate_image failed",
            {**_runtime_diagnostics(), "error": error},
            "H1-H4",
        )
        if "peft" in error.lower() or "PEFT" in error:
            from services.platform_paths import pip_install_cmd, venv_python

            error = (
                f"{error}\n\nPython in use: {sys.executable}\n"
                f'Start LOMA with: "{venv_python()}" main.py\n'
                "Or install deps for that interpreter:\n"
                f"  {pip_install_cmd('-r', 'requirements.txt')}"
            )
        fallback_path = _fallback_file(output_path, prompt, error, metadata)
        return ImageGenerationResult(
            fallback_path,
            prompt,
            seed,
            width,
            height,
            steps,
            guidance_scale,
            model_id,
            lora_id=lora_id if lcm_enabled else "",
            fallback=True,
            error=error,
        )
