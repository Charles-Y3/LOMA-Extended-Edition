# -*- coding: utf-8 -*-
"""Local image editing via Stable Diffusion img2img."""
from __future__ import annotations

import os
from pathlib import Path

from services.image_generation import (
    DEFAULT_MODEL_ID,
    GENERATED_IMAGE_DIR,
    ImageGenerationResult,
    require_english_prompt,
    prepare_image_prompt,
    unique_output_path,
)


def edit_image_local(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
    strength: float = 0.28,
    steps: int = 12,
    model_id: str | None = None,
    seed: int | None = None,
    progress_cb=None,
) -> ImageGenerationResult:
    """Apply a local img2img edit guided by the user instruction."""
    from PIL import Image

    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source image not found: {source_path}")

    prompt = require_english_prompt(prepare_image_prompt(instruction or "edit the image"))
    if not model_id:
        try:
            from services.model_router import get_default_image_model_from_settings

            model_id = get_default_image_model_from_settings()
        except Exception:
            model_id = DEFAULT_MODEL_ID
    model_id = model_id or DEFAULT_MODEL_ID

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(source_path, "edit")

    from PIL import ImageOps

    init_image = ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")
    w, h = init_image.size
    # SD 1.5 works best on multiples of 8; cap size for memory.
    max_side = 768
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        w, h = int(w * scale) // 8 * 8, int(h * scale) // 8 * 8
        init_image = init_image.resize((w, h), Image.Resampling.LANCZOS)

    try:
        from services.resource_governor import ResourceGovernor

        with ResourceGovernor.acquire("image_gen"):
            return _run_img2img(
                init_image=init_image,
                prompt=prompt,
                output_path=output_path,
                strength=strength,
                steps=steps,
                model_id=model_id,
                seed=seed,
                progress_cb=progress_cb,
            )
    except Exception as exc:
        from services.image_generation import _fallback_file

        meta = {"error": str(exc), "mode": "img2img"}
        fallback = _fallback_file(output_path, prompt, str(exc), meta)
        return ImageGenerationResult(
            fallback,
            prompt,
            0,
            init_image.width,
            init_image.height,
            steps,
            7.5,
            model_id,
            fallback=True,
            error=str(exc),
        )


def _run_img2img(
    *,
    init_image,
    prompt: str,
    output_path: str,
    strength: float,
    steps: int,
    model_id: str,
    seed: int | None = None,
    progress_cb=None,
    negative_prompt: str | None = None,
    guidance_scale: float = 7.5,
) -> ImageGenerationResult:
    import torch
    from diffusers import StableDiffusionImg2ImgPipeline, StableDiffusionXLImg2ImgPipeline

    from services.image_generation import load_edit_pipeline, place_edit_pipeline_on_device, run_pipe_with_progress

    from services.system.profiler import resolve_torch_device

    device = resolve_torch_device()
    dtype = torch.float16 if device == "cuda" else torch.float32
    pipe = load_edit_pipeline(
        model_id,
        StableDiffusionImg2ImgPipeline,
        StableDiffusionXLImg2ImgPipeline,
        torch_dtype=dtype,
    )
    pipe = place_edit_pipeline_on_device(pipe, device, model_id)

    used_seed = 42 if seed is None else int(seed)
    generator = torch.Generator(device=device).manual_seed(used_seed)
    extra_kwargs = {"negative_prompt": negative_prompt} if negative_prompt else {}
    result = run_pipe_with_progress(
        pipe,
        progress_cb=progress_cb,
        total_steps=steps,
        prompt=prompt,
        image=init_image,
        strength=min(0.95, max(0.15, strength)),
        num_inference_steps=steps,
        guidance_scale=guidance_scale,
        generator=generator,
        **extra_kwargs,
    )
    out = result.images[0]
    out.save(output_path)
    return ImageGenerationResult(
        output_path,
        prompt,
        used_seed,
        out.width,
        out.height,
        steps,
        7.5,
        model_id,
    )
