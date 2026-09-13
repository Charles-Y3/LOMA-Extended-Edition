# -*- coding: utf-8 -*-
"""Composite edits: fill empty regions, copy objects between photos."""
from __future__ import annotations

import os
import re
from pathlib import Path

from services.image_generation import GENERATED_IMAGE_DIR, ImageGenerationResult, unique_output_path


def is_composite_edit(instruction: str) -> bool:
    from pipeline.query_intent_i18n import matches

    lower = (instruction or "").lower()
    if re.search(
        r"\b(add|place|put|insert|copy|paste|combine|merge)\b.+\b(?:to|into|onto)\b",
        lower,
    ):
        return True
    if re.search(
        r"\b(fill|empty|slot|insert|copy|paste|put|composite|another\s+(?:photo|image|picture)|"
        r"from\s+.+\s+(?:photo|image|picture).*(?:to|into))\b",
        lower,
    ):
        return True
    if re.search(r"\binto\s+(?:another|the other|photo|image)\b", lower):
        return True
    # Non-English fallback: the structural "verb ... to/into" regexes above are
    # English-grammar shaped; other locales get the broader phrase-concept check.
    return matches(lower, "image_composite_hints")


def is_cross_image_edit(instruction: str) -> bool:
    lower = (instruction or "").lower()
    return bool(
        re.search(
            r"(?:from|in)\s+.+\s+(?:photo|image|picture).*(?:to|into)|"
            r"(?:insert|copy|put).*(?:into|onto|in)\s+(?:another|other|second)",
            lower,
        )
    )


def is_multi_source_create(instruction: str) -> bool:
    """User attached 2+ images and wants a new composite from them."""
    from pipeline.query_intent_i18n import matches

    lower = (instruction or "").lower()
    if is_composite_edit(instruction) or is_cross_image_edit(instruction):
        return True
    if re.search(
        r"\b(?:from|using|with)\s+(?:the\s+)?(?:two|both|multiple|2)\s+"
        r"(?:sources?|images?|photos?|pictures?)\b|"
        r"\b(?:combine|merge|composite|create|make)\b.{0,48}\b"
        r"(?:sources?|images?|photos?|from)\b|"
        r"\bnew\s+image\s+from\b|"
        r"\bfrom\s+(?:the\s+)?sources?\b",
        lower,
    ):
        return True
    return matches(lower, "image_composite_hints")


def _load_rgb(path: str):
    from PIL import Image, ImageOps

    return ImageOps.exif_transpose(Image.open(path)).convert("RGB")


def rembg_available() -> bool:
    """True when the rembg background-removal library is importable."""
    import importlib.util

    return importlib.util.find_spec("rembg") is not None


# rembg's own default model (bria-rmbg, ~977MB) gets re-loaded from disk on every
# call when no session is passed in — u2net (~176MB) is the deliberate choice here
# instead: smaller, faster to load, and its quality is good enough for the masking
# use cases this app needs (subject/background swap, recolor). Cached at module
# level and reused for the life of the process so only the FIRST cutout call after
# startup pays the load cost — every call after that reuses the already-loaded
# session instead of reloading the model from disk each time.
_rembg_session = None


def _get_rembg_session():
    global _rembg_session
    if _rembg_session is None:
        from rembg import new_session

        _rembg_session = new_session("u2net")
    return _rembg_session


def _classical_cutout(image):
    """Border-keyed subject cutout — removes pixels close to the background colour.

    A dependency-free fallback for when rembg isn't installed. Works well on
    subjects over a fairly uniform background; weaker on busy backgrounds.
    """
    import numpy as np
    from PIL import Image, ImageFilter

    rgba = image.convert("RGBA")
    rgb = np.asarray(rgba)[:, :, :3].astype("int16")
    h, w = rgb.shape[:2]
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]], axis=0)
    bg = np.median(border, axis=0)
    dist = np.sqrt(((rgb - bg) ** 2).sum(axis=2))
    thr = max(28.0, float(np.percentile(dist, 55)))
    fg = ((dist > thr).astype("uint8")) * 255
    mask = Image.fromarray(fg, "L")
    # Close small holes, drop specks, then feather the edge.
    mask = mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.MinFilter(5))
    mask = mask.filter(ImageFilter.GaussianBlur(1.5))
    rgba.putalpha(mask)
    return rgba


def _extract_subject_rgba(image):
    """Cut the subject out of ``image`` as RGBA. rembg if available, else classical."""
    try:
        from rembg import remove

        return remove(image.convert("RGBA"), session=_get_rembg_session())
    except Exception:
        try:
            return _classical_cutout(image)
        except Exception:
            from PIL import Image

            rgba = image.convert("RGBA")
            w, h = rgba.size
            return rgba.crop((w // 4, h // 4, 3 * w // 4, 3 * h // 4))


def extract_donor_objects(
    donor_path: str,
    *,
    out_dir: str | None = None,
    min_area_frac: float = 0.004,
    max_objects: int = 12,
) -> list[dict]:
    """Cut the donor foreground and split it into distinct objects via connected
    components on the alpha mask. Each object is saved as a transparent PNG (only that
    component opaque). Returns dicts ``{index, bbox:(x0,y0,x1,y1), area, path}`` sorted
    largest-first — bbox is in donor pixel coords. Falls back to a single whole-foreground
    entry when splitting isn't possible (one blob, or cv2 unavailable)."""
    import numpy as np
    from PIL import Image

    if not donor_path or not os.path.isfile(donor_path):
        raise FileNotFoundError(f"Donor image not found: {donor_path}")
    out_dir = out_dir or os.path.join(GENERATED_IMAGE_DIR, "donor_objects")
    os.makedirs(out_dir, exist_ok=True)
    stem = Path(donor_path).stem

    donor = _load_rgb(donor_path)
    cut = _extract_subject_rgba(donor).convert("RGBA")
    arr = np.array(cut)
    alpha = arr[:, :, 3]
    binary = (alpha > 32).astype("uint8")
    total = int(binary.size)

    # Connected-component split: (area, boolean-mask, bbox) per object. Prefer cv2;
    # fall back to scipy.ndimage when OpenCV isn't installed.
    comps: list | None = None
    try:
        cv2 = __import__("cv2")
        kernel = np.ones((5, 5), np.uint8)  # close small gaps so one object stays whole
        closed = cv2.morphologyEx(binary * 255, cv2.MORPH_CLOSE, kernel, iterations=2)
        n, labels, stats, _ = cv2.connectedComponentsWithStats(
            (closed > 0).astype("uint8"), connectivity=8
        )
        comps = []
        for lbl in range(1, n):
            area = int(stats[lbl, cv2.CC_STAT_AREA])
            if area < min_area_frac * total:
                continue
            x = int(stats[lbl, cv2.CC_STAT_LEFT])
            y = int(stats[lbl, cv2.CC_STAT_TOP])
            w = int(stats[lbl, cv2.CC_STAT_WIDTH])
            h = int(stats[lbl, cv2.CC_STAT_HEIGHT])
            comps.append((area, labels == lbl, (x, y, x + w, y + h)))
    except Exception:
        comps = None

    if comps is None:
        try:
            from scipy import ndimage

            structure = np.ones((3, 3), dtype=bool)
            closed = ndimage.binary_closing(binary.astype(bool), structure=structure, iterations=2)
            lab, n = ndimage.label(closed, structure=structure)
            comps = []
            for i, sl in enumerate(ndimage.find_objects(lab), start=1):
                if sl is None:
                    continue
                comp_mask = lab == i
                area = int(comp_mask.sum())
                if area < min_area_frac * total:
                    continue
                y0, y1 = sl[0].start, sl[0].stop
                x0, x1 = sl[1].start, sl[1].stop
                comps.append((area, comp_mask, (x0, y0, x1, y1)))
        except Exception:
            comps = []

    objects: list[dict] = []
    comps.sort(key=lambda c: c[0], reverse=True)
    for idx, (area, comp_mask, bbox) in enumerate(comps[:max_objects]):
        obj = arr.copy()
        obj[:, :, 3] = np.where(comp_mask, alpha, 0).astype("uint8")
        x0, y0, x1, y1 = bbox
        crop = Image.fromarray(obj[y0:y1, x0:x1], "RGBA")
        path = os.path.join(out_dir, f"{stem}_obj{idx}.png")
        crop.save(path)
        objects.append({"index": idx, "bbox": bbox, "area": area, "path": path})

    if not objects:
        bbox = cut.getbbox()
        if not bbox:
            raise RuntimeError("Could not isolate any object in the donor image.")
        crop = cut.crop(bbox)
        path = os.path.join(out_dir, f"{stem}_obj0.png")
        crop.save(path)
        objects = [
            {"index": 0, "bbox": bbox, "area": crop.width * crop.height, "path": path}
        ]
    return objects


def _empty_slot_mask(image):
    """Mask bright/low-detail regions (e.g. empty muffin cups) for inpainting."""
    import numpy as np

    arr = np.array(image.convert("RGB"))
    cv2 = __import__("cv2")
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    blur = cv2.GaussianBlur(gray, (15, 15), 0)
    _, bright = cv2.threshold(blur, 200, 255, cv2.THRESH_BINARY)
    kernel = np.ones((7, 7), np.uint8)
    bright = cv2.morphologyEx(bright, cv2.MORPH_OPEN, kernel, iterations=2)
    bright = cv2.morphologyEx(bright, cv2.MORPH_CLOSE, kernel, iterations=3)
    from PIL import Image

    return Image.fromarray(bright).convert("L")


def fill_empty_slots(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
    steps: int = 22,
) -> ImageGenerationResult:
    from services.image_inpaint import inpaint_image_local

    return inpaint_image_local(
        source_path=source_path,
        instruction=instruction or "fill empty slots with matching items",
        output_path=output_path,
        steps=steps,
    )


def composite_object_into_scene(
    *,
    target_path: str,
    donor_path: str,
    instruction: str,
    output_path: str | None = None,
    cx_frac: float | None = None,
    cy_frac: float | None = None,
    size_frac: float = 0.35,
    rotation_deg: float = 0.0,
    subject_path: str | None = None,
) -> ImageGenerationResult:
    """Extract subject from donor, paste into target with seamless blending.

    Placement: ``cx_frac``/``cy_frac`` give the subject centre as a fraction of the
    target (0..1) — used when the UI captured a click. If they're None, fall back to
    a keyword in ``instruction`` (left/right/top/bottom/center). ``size_frac`` caps
    the subject's largest side to that fraction of the target. ``rotation_deg`` spins
    the donor subject (counter-clockwise) before it's sized and placed. ``subject_path``
    is a pre-extracted transparent PNG (from :func:`extract_donor_objects`) — used
    directly when the user picked a specific object, instead of re-cutting the donor.
    """
    import numpy as np

    target = _load_rgb(target_path)
    if subject_path and os.path.isfile(subject_path):
        from PIL import Image

        cut = Image.open(subject_path).convert("RGBA")
    else:
        donor = _load_rgb(donor_path)
        cut = _extract_subject_rgba(donor)
    bbox = cut.getbbox()
    if not bbox:
        raise RuntimeError("Could not isolate an object in the donor image.")
    subject = cut.crop(bbox)

    rotation_deg = float(rotation_deg or 0.0) % 360.0
    if rotation_deg:
        from PIL import Image

        subject = subject.rotate(
            rotation_deg, expand=True, resample=Image.Resampling.BICUBIC, fillcolor=(0, 0, 0, 0)
        )
        trimmed = subject.getbbox()
        if trimmed:
            subject = subject.crop(trimmed)

    tw, th = target.size
    size_frac = min(0.95, max(0.05, float(size_frac or 0.35)))
    max_w = int(tw * size_frac)
    max_h = int(th * size_frac)
    sw, sh = subject.size
    scale = min(max_w / sw, max_h / sh, 1.0)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    subject = subject.resize((nw, nh), resample=__import__("PIL").Image.Resampling.LANCZOS)

    alpha = subject.split()[-1]
    rgb = subject.convert("RGB")
    mask = np.array(alpha)
    mask = (mask > 32).astype("uint8") * 255

    if cx_frac is not None and cy_frac is not None:
        cx = int(min(1.0, max(0.0, cx_frac)) * tw)
        cy = int(min(1.0, max(0.0, cy_frac)) * th)
    else:
        cx, cy = tw // 2, th // 2
        m = re.search(r"\b(left|right|top|bottom|center|centre)\b", (instruction or "").lower())
        if m:
            pos = m.group(1)
            if pos == "left":
                cx, cy = tw // 4, th // 2
            elif pos == "right":
                cx, cy = 3 * tw // 4, th // 2
            elif pos == "top":
                cx, cy = tw // 2, th // 4
            elif pos == "bottom":
                cx, cy = tw // 2, 3 * th // 4

    x = max(0, min(tw - nw, cx - nw // 2))
    y = max(0, min(th - nh, cy - nh // 2))

    try:
        cv2 = __import__("cv2")
        target_bgr = cv2.cvtColor(np.array(target), cv2.COLOR_RGB2BGR)
        src_bgr = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
        result = cv2.seamlessClone(
            src_bgr, target_bgr, mask, (x + nw // 2, y + nh // 2), cv2.NORMAL_CLONE
        )
        out_rgb = cv2.cvtColor(result, cv2.COLOR_BGR2RGB)
    except ImportError:
        from PIL import Image

        canvas = target.copy()
        canvas.paste(rgb, (x, y), alpha)
        out_rgb = np.array(canvas)
    from PIL import Image

    out = Image.fromarray(out_rgb)
    os.makedirs(GENERATED_IMAGE_DIR, exist_ok=True)
    if output_path is None:
        output_path = unique_output_path(target_path, "composite")
    out.save(output_path)
    return ImageGenerationResult(
        output_path,
        instruction,
        0,
        out.width,
        out.height,
        0,
        0.0,
        "composite",
    )


def run_composite_edit(
    *,
    source_path: str,
    instruction: str,
    output_path: str | None = None,
    donor_path: str | None = None,
    steps: int = 22,
    cx_frac: float | None = None,
    cy_frac: float | None = None,
    size_frac: float = 0.35,
    rotation_deg: float = 0.0,
) -> ImageGenerationResult:
    lower = (instruction or "").lower()
    if donor_path and os.path.isfile(donor_path) and (
        is_cross_image_edit(instruction) or is_multi_source_create(instruction)
    ):
        return composite_object_into_scene(
            target_path=source_path,
            donor_path=donor_path,
            instruction=instruction,
            output_path=output_path,
            cx_frac=cx_frac,
            cy_frac=cy_frac,
            size_frac=size_frac,
            rotation_deg=rotation_deg,
        )
    if re.search(r"\b(fill|empty|slot)\b", lower):
        return fill_empty_slots(
            source_path=source_path,
            instruction=instruction,
            output_path=output_path,
            steps=steps,
        )
    from services.image_inpaint import inpaint_image_local

    return inpaint_image_local(
        source_path=source_path,
        instruction=instruction,
        output_path=output_path,
        steps=steps,
    )
