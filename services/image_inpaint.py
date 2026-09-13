# -*- coding: utf-8 -*-
"""Localized image edits via inpainting (mask + diffusion)."""
from __future__ import annotations

import os
import re
from pathlib import Path

from services.image_generation import (
    DEFAULT_MODEL_ID,
    GENERATED_IMAGE_DIR,
    ImageGenerationResult,
    prepare_image_prompt,
    unique_output_path,
)


# Diffusers img2img only actually denoises for round(num_inference_steps *
# strength) iterations — the rest of the requested "steps" are simply skipped,
# not run at lower quality. A user's saved "low quality" preference (as few as
# 8 steps, meant for a quick plain txt2img preview) combined with the high
# strength these two swap functions need (0.75-0.95, since they're discarding
# most of the original crop/frame) can collapse to as few as 6 real denoising
# steps — nowhere near enough to converge, producing exactly the "melted
# stained glass" incoherent look reported and reproduced. A step floor here
# guarantees a minimum number of REAL denoising iterations regardless of the
# caller's general quality preference — "low quality" should mean fast/fewer
# steps for a plain generation, not a broken result for a strength-heavy edit.
_MIN_EFFECTIVE_SWAP_STEPS = 18


def _steps_for_strength(requested_steps: int, strength: float) -> int:
    import math

    if strength <= 0:
        return requested_steps
    floor_steps = math.ceil(_MIN_EFFECTIVE_SWAP_STEPS / strength)
    return max(requested_steps, floor_steps)


def _load_image_oriented(source_path: str):
    from PIL import Image, ImageOps

    return ImageOps.exif_transpose(Image.open(source_path)).convert("RGB")


def is_simple_color_edit(instruction: str) -> bool:
    """True when user only wants a color change (no layout/composite edits)."""
    lower = (instruction or "").lower()
    if not re.search(r"\b(color|colour)\b", lower) and not re.search(
        r"\b(?:to|into)\s+(blue|red|green|yellow|orange|purple|pink|white|black)\b", lower
    ):
        return False
    if re.search(r"\b(fill|empty|insert|copy|composite|add|remove)\b", lower):
        return False
    if re.search(r"\b(all|every|each|muffin|cupcake|cookies|berries)\b", lower):
        return False
    color = _color_word_from_instruction(instruction)
    # Rainbow is multi-hue — a single-hue HSV shift can't produce it, so let it fall
    # through to diffusion inpaint (which gets a "rainbow multicolor gradient" prompt).
    if color == "rainbow":
        return False
    return bool(color)


def _rgb_to_hsv_np(rgb: "np.ndarray") -> "np.ndarray":
    """RGB uint8 (H,W,3) → HSV float32 (OpenCV scale: H 0-179, S/V 0-255)."""
    import numpy as np

    rgb_f = rgb.astype(np.float32) / 255.0
    r, g, b = rgb_f[..., 0], rgb_f[..., 1], rgb_f[..., 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    v = maxc
    deltac = maxc - minc
    s = np.where(maxc > 1e-8, deltac / maxc, 0.0)
    h = np.zeros_like(maxc)
    mask = deltac > 1e-8
    rc = np.where(mask, (maxc - r) / deltac, 0.0)
    gc = np.where(mask, (maxc - g) / deltac, 0.0)
    bc = np.where(mask, (maxc - b) / deltac, 0.0)
    h = np.where(mask & (r == maxc), bc - gc, h)
    h = np.where(mask & (g == maxc), 2.0 + rc - bc, h)
    h = np.where(mask & (b == maxc), 4.0 + gc - rc, h)
    h = (h / 6.0) % 1.0
    hsv = np.stack([h * 179.0, s * 255.0, v * 255.0], axis=-1)
    return hsv.astype(np.float32)


def _hsv_to_rgb_np(hsv: "np.ndarray") -> "np.ndarray":
    """HSV float32 (OpenCV scale) → RGB uint8."""
    import numpy as np

    h = (hsv[..., 0] / 179.0) % 1.0
    s = np.clip(hsv[..., 1] / 255.0, 0.0, 1.0)
    v = np.clip(hsv[..., 2] / 255.0, 0.0, 1.0)
    i = np.floor(h * 6.0).astype(np.int32)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - f * s)
    t = v * (1.0 - (1.0 - f) * s)
    i_mod = i % 6
    r = np.choose(i_mod, [v, q, p, p, t, v])
    g = np.choose(i_mod, [t, v, v, q, p, p])
    b = np.choose(i_mod, [p, p, t, v, v, q])
    rgb = np.stack([r, g, b], axis=-1)
    return (np.clip(rgb, 0.0, 1.0) * 255.0).astype(np.uint8)


def _luminance_rgb(arr: "np.ndarray") -> "np.ndarray":
    """Per-pixel BT.601 luminance (H, W) float32."""
    import numpy as np

    return (
        0.299 * arr[..., 0].astype(np.float32)
        + 0.587 * arr[..., 1].astype(np.float32)
        + 0.114 * arr[..., 2].astype(np.float32)
    )


def _recolor_achromatic(arr: "np.ndarray", alpha: "np.ndarray", target: str) -> "np.ndarray":
    """Map masked subject to black/white/gray while preserving local texture."""
    import numpy as np

    lum = _luminance_rgb(arr)
    core = alpha[..., 0] > 0.35
    # Wider percentile stretch (was [8, 92]) so more of the source photo's actual
    # shading/highlight detail survives the remap — the narrower band flattened the
    # subject into a nearly uniform blob with no visible form.
    if np.any(core):
        lo, hi = np.percentile(lum[core], [2, 98])
    else:
        lo, hi = np.percentile(lum, [2, 98])
    span = max(float(hi - lo), 8.0)
    norm = np.clip((lum - lo) / span, 0.0, 1.0)

    # Wider output range per target (was 6-38 / 217-255 / 80-175) — the narrow bands
    # read as a muddy, flat gray rather than a convincing black/white/gray subject.
    if target == "black":
        new_lum = norm * 60.0 + 0.0
    elif target == "white":
        new_lum = norm * 105.0 + 150.0
    else:  # gray / grey
        new_lum = norm * 95.0 + 80.0

    recolored = np.stack([new_lum, new_lum, new_lum], axis=-1)
    return recolored.astype(np.float32)


def recolor_image_local(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
) -> ImageGenerationResult:
    """Fast HSV recolor on masked subject — preserves pose, layout, and resolution."""
    import numpy as np
    from PIL import Image

    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source image not found: {source_path}")

    target = _color_word_from_instruction(instruction) or "blue"
    init_image = _load_image_oriented(source_path)

    mask = build_edit_mask(init_image, instruction)  # feathered L-mode subject mask
    # Achromatic targets show halos through soft feathered edges — re-threshold to a
    # crisper boundary, but still feather enough (4px, was 2px) to avoid a jagged/
    # staircased edge once the underlying mask is already a good subject cutout.
    if target in ("white", "black", "gray", "grey"):
        mask = _feather_mask(mask.point(lambda p: 255 if p > 96 else 0), 4)
    arr = np.array(init_image)
    alpha = (np.array(mask).astype(np.float32) / 255.0)[..., None]

    if target in ("white", "black", "gray", "grey"):
        recolored = _recolor_achromatic(arr, alpha, target)
    elif target == "brown":
        hsv = _rgb_to_hsv_np(arr)
        hsv[:, :, 0] = 18
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 0.8 + 60, 0, 255)
        hsv[:, :, 2] = np.clip(hsv[:, :, 2] * 0.55, 0, 255)
        recolored = _hsv_to_rgb_np(hsv).astype(np.float32)
    else:
        hsv = _rgb_to_hsv_np(arr)
        hue_map = {
            "red": 0,
            "orange": 15,
            "yellow": 30,
            "green": 60,
            "blue": 110,
            "purple": 140,
            "pink": 160,
        }
        target_h = float(hue_map.get(target, 110))
        hsv[:, :, 0] = target_h
        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * 1.1, 0, 255)
        recolored = _hsv_to_rgb_np(hsv).astype(np.float32)

    out = arr.astype(np.float32) * (1.0 - alpha) + recolored * alpha
    result = Image.fromarray(np.clip(out, 0.0, 255.0).astype(np.uint8))
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(source_path, "recolor")
    result.save(output_path)
    return ImageGenerationResult(
        output_path,
        instruction,
        0,
        result.width,
        result.height,
        0,
        0.0,
        "hsv-recolor",
    )


_COLOR_WORDS_RE = r"(blue|red|green|yellow|orange|purple|pink|white|black|brown|gray|grey|rainbow)"


def _color_word_from_instruction(instruction: str) -> str | None:
    lower = (instruction or "").lower()
    m = re.search(rf"\b(?:to|into|as)\s+{_COLOR_WORDS_RE}\b", lower)
    if m:
        return m.group(1)
    # Fallback for phrasing without a preposition — "make the frog blue color",
    # "frog blue" — is_simple_color_edit() already gates on "color"/"colour" being
    # present before this ever gets called for a bare color word, so this can't start
    # misclassifying unrelated requests.
    m = re.search(rf"\b{_COLOR_WORDS_RE}\b", lower)
    return m.group(1) if m else None


def _in_range_hsv(hsv: "np.ndarray", lower: tuple[int, int, int], upper: tuple[int, int, int]) -> "np.ndarray":
    import numpy as np

    lo = np.array(lower, dtype=np.float32)
    hi = np.array(upper, dtype=np.float32)
    return np.all((hsv >= lo) & (hsv <= hi), axis=-1).astype(np.uint8) * 255


def _subject_mask_hsv(image, *, target_hue: str | None = None):
    """Build a mask for the main colored subject (e.g. green frog) using HSV."""
    import numpy as np
    from PIL import Image, ImageFilter

    arr = np.array(image.convert("RGB"))
    hsv = _rgb_to_hsv_np(arr)

    if target_hue == "blue":
        mask = _in_range_hsv(hsv, (90, 40, 40), (130, 255, 255))
    elif target_hue == "red":
        mask = _in_range_hsv(hsv, (0, 40, 40), (10, 255, 255)) | _in_range_hsv(
            hsv, (170, 40, 40), (179, 255, 255)
        )
    else:
        mask = _in_range_hsv(hsv, (35, 40, 40), (90, 255, 255))

    pil_mask = Image.fromarray(mask).convert("L")
    return pil_mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(3))


def _warm_food_mask(image):
    """Mask beige/brown baked goods (muffins, bread) for recolor/inpaint."""
    import numpy as np
    from PIL import Image, ImageFilter

    arr = np.array(image.convert("RGB"))
    hsv = _rgb_to_hsv_np(arr)
    warm = _in_range_hsv(hsv, (5, 12, 55), (45, 220, 255))
    pink_tray = _in_range_hsv(hsv, (135, 35, 70), (175, 255, 255))
    gray_tray = _in_range_hsv(hsv, (0, 0, 40), (179, 40, 140))
    exclude = np.maximum(pink_tray, gray_tray)
    mask = np.where(exclude > 128, 0, warm).astype(np.uint8)
    pil_mask = Image.fromarray(mask).convert("L")
    return pil_mask.filter(ImageFilter.MaxFilter(5))


def _feather_mask(mask, radius: int = 6):
    """Soften mask edges with a Gaussian blur so edits blend in (no hard boundary)."""
    from PIL import ImageFilter

    return mask.convert("L").filter(ImageFilter.GaussianBlur(radius))


def extract_subject(image):
    """Full subject cutout via rembg: returns (subject_rgba, mask, bbox) at the
    source image's resolution, or None if rembg is unavailable, not installed, or
    finds essentially nothing. `mask` is a feathered L-mode alpha mask (see
    _feather_mask); `bbox` is the mask's bounding box as (left, top, right,
    bottom) pixel coordinates — used by swap_subject_local() to isolate a crop for
    regeneration instead of in-place masked inpainting."""
    try:
        import numpy as np
        from rembg import remove

        from services.image_composite import _get_rembg_session

        cut = remove(image.convert("RGBA"), session=_get_rembg_session())
        if cut.mode != "RGBA":
            return None
        alpha = cut.split()[-1]
        if float(np.array(alpha).mean()) < 3.0:  # basically empty
            return None
        binary = alpha.point(lambda p: 255 if p > 40 else 0)
        bbox = binary.getbbox()
        if bbox is None:
            return None
        return cut, _feather_mask(binary, 5), bbox
    except Exception:
        return None


def _rembg_cutout_mask(image):
    """Clean, feathered foreground-subject mask via rembg — isolates the actual subject
    regardless of its colour (e.g. a bronze/dark frog). None if rembg is unavailable or
    finds essentially nothing."""
    extracted = extract_subject(image)
    return extracted[1] if extracted else None


def build_edit_mask(image, instruction: str):
    """Return a feathered PIL L-mode mask (white = edit region). Prefers a clean subject
    cutout so recolours land tightly on the subject with soft edges — not a hard circle."""
    lower = (instruction or "").lower()
    if re.search(r"\b(muffins?|cupcakes?|cookies?|bread|pastries?|pastry|cakes?)\b", lower):
        try:
            return _feather_mask(_warm_food_mask(image), 4)
        except Exception:
            pass

    # Primary: a clean foreground cutout. The named target colour ("blue") is NOT the
    # subject's own colour, so a colour threshold picks the wrong pixels; the cutout
    # isolates the real subject and gives a tight, soft-edged mask.
    cutout = _rembg_cutout_mask(image)
    if cutout is not None:
        return cutout

    # Second fallback: the same dependency-free, border-keyed subject cutout already
    # proven in services/image_composite.py — works regardless of subject hue, and
    # doesn't need rembg or any other optional package. It assumes a roughly uniform
    # background, though — a border sampled from a mix of materials (e.g. a stone
    # wall behind + a tile floor below) can median out to something closer to a
    # shadow than either, misclassifying half the photo as "foreground". Reject a
    # cutout that covers an implausibly large fraction of the frame (a real subject
    # cutout is a minority of the image) rather than trust it blindly — the coverage
    # threshold was tuned against exactly this failure mode (51% coverage, wrong)
    # vs. a real single-subject cutout (~10% here).
    try:
        from services.image_composite import _classical_cutout

        rgba = _classical_cutout(image)
        alpha = rgba.split()[-1]
        import numpy as np

        coverage = float(np.array(alpha).mean()) / 255.0
        if 0.01 <= coverage <= 0.35:
            return _feather_mask(alpha.point(lambda p: 255 if p > 40 else 0), 5)
    except Exception:
        pass

    # Third fallback: colour-threshold subject mask (feathered). Pure numpy/PIL — no
    # cv2 dependency despite historically being gated behind one (that gate never
    # actually used cv2 and just skipped this whole branch when cv2 wasn't installed).
    try:
        src_color = None
        if re.search(r"\b(color|colour|frog|change)\b", lower):
            target_color = _color_word_from_instruction(lower)
            for c in re.findall(r"\b(green|blue|red|yellow|orange|purple|pink)\b", lower):
                if c != target_color:
                    src_color = c
                    break
        return _feather_mask(_subject_mask_hsv(image, target_hue=src_color), 4)
    except Exception:
        pass

    # Last resort: a soft central ellipse (feathered heavily so there's no hard circle edge).
    from PIL import Image, ImageDraw

    w, h = image.size
    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((w * 0.15, h * 0.2, w * 0.85, h * 0.85), fill=255)
    return _feather_mask(mask, max(10, int(min(w, h) * 0.05)))


def swap_subject_local(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
    strength: float = 0.75,
    steps: int = 20,
    model_id: str | None = None,
    seed: int | None = None,
    progress_cb=None,
) -> ImageGenerationResult | None:
    """Subject-swap mutation: extract the subject via rembg, regenerate ONLY an
    isolated crop of it via img2img, then recomposite onto the ORIGINAL untouched
    background. An isolated crop gives the model room to fully replace a
    differently-shaped subject (e.g. frog -> toad) instead of fighting the old
    silhouette that bleeds through an in-place mask's edges — and since everything
    outside the (feathered) subject mask is pasted back byte-for-byte from the
    original, the background can never degrade or "melt", unlike full-frame
    inpainting/regeneration.

    Returns None (never raises) when rembg is unavailable or finds no usable
    subject — the caller (inpaint_image_local) falls back to its existing
    in-place inpaint path in that case, so this is a pure quality upgrade, not a
    new failure mode."""
    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source image not found: {source_path}")

    from PIL import Image

    original = _load_image_oriented(source_path)
    extracted = extract_subject(original)
    if extracted is None:
        return None
    _cutout, mask, bbox = extracted

    if not model_id:
        try:
            from services.model_router import get_default_image_model_from_settings

            model_id = get_default_image_model_from_settings()
        except Exception:
            model_id = DEFAULT_MODEL_ID
    model_id = model_id or DEFAULT_MODEL_ID

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(source_path, "subject_swap")

    # Pad the bbox so the regenerated crop has margin to blend into, not a
    # razor-tight silhouette crop with nothing around it for the model to work with.
    w, h = original.size
    x0, y0, x1, y1 = bbox
    pad_x, pad_y = int((x1 - x0) * 0.15), int((y1 - y0) * 0.15)
    cx0, cy0 = max(0, x0 - pad_x), max(0, y0 - pad_y)
    cx1, cy1 = min(w, x1 + pad_x), min(h, y1 + pad_y)
    crop = original.crop((cx0, cy0, cx1, cy1))

    # SD1.5 was trained at ~512px and produces chaotic, mushy output when fed a
    # much smaller crop (a small subject bbox is common — a 137x82px crop is not
    # unusual). Always scale the crop's long side to a comfortable working
    # resolution — UP as well as down — then downscale the result back to the
    # crop's real size after generation; dimensions must be multiples of 8.
    cw, ch = crop.size
    target_long = 640
    scale = target_long / float(max(cw, ch))
    nw, nh = max(64, int(cw * scale) // 8 * 8), max(64, int(ch * scale) // 8 * 8)
    crop_resized = crop.resize((nw, nh), Image.Resampling.LANCZOS)

    # Bare instruction text ("change the frog to a toad") makes a poor img2img
    # prompt on its own — SD tends to render it as a flat, over-outlined "sticker"
    # rather than blending into the surrounding photo. Quality-tag suffix/negative
    # prompt cost nothing extra (no LLM call) and noticeably reduce that look; this
    # benefits the isolated-crop regeneration specifically since its output has to
    # visually match a real photographic background once recomposited.
    prompt = prepare_image_prompt(instruction or "edit the subject")
    prompt = f"{prompt}, photorealistic, natural lighting, detailed, high quality photo"
    negative_prompt = (
        "cartoon, illustration, drawing, sticker, outline, cel shading, "
        "oversaturated, artificial, deformed, blurry, low quality"
    )

    from services.image_edit import _run_img2img
    from services.resource_governor import ResourceGovernor

    effective_strength = min(0.85, max(0.15, strength))
    steps = _steps_for_strength(steps, effective_strength)

    tmp_path = f"{output_path}.subject_tmp.png"
    try:
        with ResourceGovernor.acquire("image_gen"):
            _run_img2img(
                init_image=crop_resized,
                prompt=prompt,
                output_path=tmp_path,
                strength=effective_strength,
                steps=steps,
                model_id=model_id,
                seed=seed,
                progress_cb=progress_cb,
                negative_prompt=negative_prompt,
            )
        new_crop = Image.open(tmp_path).convert("RGB").resize((cx1 - cx0, cy1 - cy0), Image.Resampling.LANCZOS)
    except Exception:
        # Generation failure (OOM, model load error, ...) — fall back to the
        # caller's in-place inpaint path rather than raising, same as the
        # no-subject-found case above.
        return None
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    # Recomposite: the regenerated crop is pasted back at its original position,
    # alpha-blended through the (already feathered) subject mask cropped to the
    # same box — every pixel outside the mask stays byte-identical to the source.
    composed = original.copy()
    mask_crop = mask.crop((cx0, cy0, cx1, cy1))
    composed.paste(new_crop, (cx0, cy0), mask_crop)
    composed.save(output_path)

    used_seed = 42 if seed is None else int(seed)
    return ImageGenerationResult(
        output_path, prompt, used_seed, composed.width, composed.height, steps, 7.5, model_id,
    )


def swap_background_local(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
    strength: float = 0.75,
    steps: int = 20,
    model_id: str | None = None,
    seed: int | None = None,
    progress_cb=None,
) -> ImageGenerationResult | None:
    """Background-swap mutation: extract the subject via rembg, regenerate the
    FULL frame via img2img (so the new background is coherent), then paste the
    ORIGINAL untouched subject cutout back on top through its feathered mask.
    Mirror image of swap_subject_local() — there the subject is regenerated and
    the background protected; here the background is regenerated and the subject
    protected.

    Returns None (never raises) when rembg is unavailable or finds no usable
    subject — the caller (inpaint_image_local) falls back to its existing
    in-place inpaint path in that case."""
    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source image not found: {source_path}")

    from PIL import Image

    original = _load_image_oriented(source_path)
    extracted = extract_subject(original)
    if extracted is None:
        return None
    cutout, mask, _bbox = extracted

    if not model_id:
        try:
            from services.model_router import get_default_image_model_from_settings

            model_id = get_default_image_model_from_settings()
        except Exception:
            model_id = DEFAULT_MODEL_ID
    model_id = model_id or DEFAULT_MODEL_ID

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(source_path, "background_swap")

    # SD1.5 was trained at ~512px and produces chaotic, mushy output when fed a
    # much smaller frame (many phone/web photos are well under that). Always
    # scale the long side to a comfortable working resolution — UP as well as
    # down — then downscale the result back to the original size after
    # generation; dimensions must be multiples of 8.
    w, h = original.size
    target_long = 768
    scale = target_long / float(max(w, h))
    nw, nh = max(64, int(w * scale) // 8 * 8), max(64, int(h * scale) // 8 * 8)
    frame_resized = original.resize((nw, nh), Image.Resampling.LANCZOS)

    prompt = prepare_image_prompt(instruction or "change the background")
    prompt = f"{prompt}, full scene, photorealistic, natural lighting, detailed, high quality photo"
    negative_prompt = (
        "cartoon, illustration, drawing, sticker, outline, cel shading, "
        "oversaturated, artificial, deformed, blurry, low quality"
    )

    # Unlike swap_subject_local (which preserves most of the original crop's
    # structure by design), a background swap needs to discard the ORIGINAL
    # scene almost entirely — the whole point is a different backdrop, and the
    # subject is protected separately by the recomposite step below regardless
    # of how aggressively the background is regenerated. A moderate strength
    # (this function's own default, or whatever a generic caller passes) mostly
    # just denoises the same original scene, which is why "change the
    # background to outer space" was previously coming back still looking like
    # the original room. Force strong regeneration + higher prompt guidance
    # here specifically, regardless of the caller's requested strength.
    effective_strength = max(0.85, min(0.95, strength))
    steps = _steps_for_strength(steps, effective_strength)

    from services.image_edit import _run_img2img
    from services.resource_governor import ResourceGovernor

    tmp_path = f"{output_path}.background_tmp.png"
    try:
        with ResourceGovernor.acquire("image_gen"):
            _run_img2img(
                init_image=frame_resized,
                prompt=prompt,
                output_path=tmp_path,
                strength=effective_strength,
                guidance_scale=9.5,
                steps=steps,
                model_id=model_id,
                seed=seed,
                progress_cb=progress_cb,
                negative_prompt=negative_prompt,
            )
        new_bg = Image.open(tmp_path).convert("RGB").resize((w, h), Image.Resampling.LANCZOS)
    except Exception:
        # Generation failure (OOM, model load error, ...) — fall back to the
        # caller's in-place inpaint path rather than raising.
        return None
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    # Recomposite: the original subject cutout is pasted back over the newly
    # regenerated background, alpha-blended through the (already feathered)
    # subject mask — every subject pixel stays byte-identical to the source.
    composed = new_bg.copy()
    composed.paste(cutout.convert("RGB"), (0, 0), mask)
    composed.save(output_path)

    used_seed = 42 if seed is None else int(seed)
    return ImageGenerationResult(
        output_path, prompt, used_seed, composed.width, composed.height, steps, 7.5, model_id,
    )


def inpaint_image_local(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
    strength: float = 0.75,
    steps: int = 20,
    model_id: str | None = None,
    seed: int | None = None,
    progress_cb=None,
) -> ImageGenerationResult:
    """Inpaint only masked regions; unmasked pixels stay from the source."""
    if not source_path or not os.path.isfile(source_path):
        raise FileNotFoundError(f"Source image not found: {source_path}")

    # A plain "change the <subject> to <color>" is done far more faithfully by a
    # masked HSV recolor (keeps pose, texture, and resolution) than by re-diffusing
    # the region, which SD 1.5 inpaint tends to muddy. Route those here.
    if is_simple_color_edit(instruction):
        return recolor_image_local(
            source_path=source_path, instruction=instruction, output_path=output_path
        )

    # "Change the background to X" targets the background, not the subject — route
    # it to swap_background_local() (regenerate the full frame, protect the
    # original subject) instead of the subject-swap catch-all below, which would
    # otherwise garble a tight crop of the subject while leaving the background
    # untouched (the opposite of what was asked).
    from pipeline.query_intent_i18n import matches

    if matches(instruction, "background_change_target_hints"):
        bg_swapped = swap_background_local(
            source_path=source_path, instruction=instruction, output_path=output_path,
            strength=strength, steps=steps, model_id=model_id, seed=seed, progress_cb=progress_cb,
        )
        if bg_swapped is not None:
            return bg_swapped

    # Anything past a simple recolor is usually a full subject replacement (e.g.
    # "change the frog to a toad") — try the isolated-crop extract/regenerate/
    # recomposite path first (see swap_subject_local's docstring for why it beats
    # in-place masked inpaint for this case). Falls straight through to today's
    # in-place inpaint below when rembg is unavailable or finds no clear subject —
    # never a new failure mode, only a quality upgrade when it succeeds.
    swapped = swap_subject_local(
        source_path=source_path, instruction=instruction, output_path=output_path,
        strength=strength, steps=steps, model_id=model_id, seed=seed, progress_cb=progress_cb,
    )
    if swapped is not None:
        return swapped

    import torch
    from diffusers import StableDiffusionInpaintPipeline, StableDiffusionXLInpaintPipeline
    from PIL import Image

    prompt = prepare_image_prompt(instruction or "edit the masked region")
    if re.search(r"\brainbow\b", (instruction or "").lower()):
        prompt = f"vivid rainbow multicolor gradient, {prompt}"
    if not model_id:
        try:
            from services.model_router import get_default_image_model_from_settings

            model_id = get_default_image_model_from_settings()
        except Exception:
            model_id = DEFAULT_MODEL_ID
    model_id = model_id or DEFAULT_MODEL_ID

    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(source_path, "inpaint")

    init_image = _load_image_oriented(source_path)
    mask = build_edit_mask(init_image, instruction)

    w, h = init_image.size
    max_side = 768
    if max(w, h) > max_side:
        scale = max_side / float(max(w, h))
        nw, nh = int(w * scale) // 8 * 8, int(h * scale) // 8 * 8
        init_image = init_image.resize((nw, nh), Image.Resampling.LANCZOS)
        mask = mask.resize((nw, nh), Image.Resampling.BILINEAR)  # keep feathered edges soft
        out_size = (w, h)
    else:
        out_size = None

    from services.image_generation import run_pipe_with_progress
    from services.resource_governor import ResourceGovernor

    from services.system.profiler import resolve_torch_device

    device = resolve_torch_device()
    dtype = torch.float16 if device == "cuda" else torch.float32
    # Free the LLM from VRAM before loading the inpaint model on constrained GPUs.
    with ResourceGovernor.acquire("image_gen"):
        from services.image_generation import load_edit_pipeline, place_edit_pipeline_on_device

        pipe = load_edit_pipeline(
            model_id,
            StableDiffusionInpaintPipeline,
            StableDiffusionXLInpaintPipeline,
            torch_dtype=dtype,
        )
        pipe = place_edit_pipeline_on_device(pipe, device, model_id)

        used_seed = 42 if seed is None else int(seed)
        generator = torch.Generator(device=device).manual_seed(used_seed)
        result = run_pipe_with_progress(
            pipe,
            progress_cb=progress_cb,
            total_steps=steps,
            prompt=prompt,
            image=init_image,
            mask_image=mask,
            strength=min(0.95, max(0.35, strength)),
            num_inference_steps=steps,
            guidance_scale=7.5,
            generator=generator,
        )
    out = result.images[0]
    if out_size and out.size != out_size:
        out = out.resize(out_size, Image.Resampling.LANCZOS)
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
