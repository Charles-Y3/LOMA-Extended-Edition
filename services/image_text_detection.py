# -*- coding: utf-8 -*-
"""Fast, local text-region detection for generated images — flags diffusion output that
still contains hallucinated/garbled text despite the negative-prompt and positive-framing
suppression in services/image_generation.py, so callers can reseed-and-retry (or mutate
the region away) rather than ship a bad image.

Uses EAST (a small pretrained scene-text-detection network, via OpenCV's DNN module) —
tested directly against this app's own garbled/clean generated images and got clean
separation (garbled: hundreds of detected regions at ~1.0 confidence; clean: zero),
unlike a classical-CV MSER approach tried first, which couldn't reliably tell diffusion-
hallucinated glyphs apart from photo texture (soft AI-art textures produce as many
false-positive blobs as real letterforms do under geometric heuristics alone). ~1s per
check on CPU, no GPU/VRAM involvement — it never contends with the image pipeline or
triggers the resource governor the way a vision-LLM-based check would.

Only meaningful for plain diffusion "photo" output (services.marker_visual._try_photo,
services.image_generation.generate_image's default path) — never call this on a real
chart/diagram/infographic image (services.chart_generation / diagram_generation /
infographic_generation), which legitimately contain correct, intentional text rendered
by a deterministic renderer, not diffusion.

The model file is bundled with the app (assets/east_text_detector/), not downloaded on
first use — see packaging/loma_core.spec."""
from __future__ import annotations

import os
import threading

_EAST_RELATIVE_PATH = os.path.join("assets", "east_text_detector", "frozen_east_text_detection.pb")

_net = None
_net_lock = threading.Lock()


def _model_path() -> str:
    from services.platform_paths import resource_root

    return os.path.join(resource_root(), _EAST_RELATIVE_PATH)


def text_detection_deps_available() -> tuple[bool, str]:
    """(ok, detail) — mirrors image_generation_deps_available()'s pattern elsewhere."""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False, "opencv-python-headless"
    if not os.path.isfile(_model_path()):
        return False, "frozen_east_text_detection.pb"
    return True, ""


def _get_net():
    global _net
    if _net is not None:
        return _net
    with _net_lock:
        if _net is None:
            import cv2

            _net = cv2.dnn.readNet(_model_path())
    return _net


def _prepare_blob(image, cv2_module, np_module):
    """BGR array (for cv2), the resized (new_w, new_h), and the (scale_x, scale_y)
    factors to map detections back to the original image size — EAST requires both
    input dimensions to be multiples of 32, so the image is rarely detected at its
    native resolution."""
    orig = np_module.array(image.convert("RGB"))[:, :, ::-1]  # RGB -> BGR
    h, w = orig.shape[:2]
    new_w, new_h = max(32, (w // 32) * 32), max(32, (h // 32) * 32)
    resized = cv2_module.resize(orig, (new_w, new_h))
    blob = cv2_module.dnn.blobFromImage(
        resized, 1.0, (new_w, new_h), (123.68, 116.78, 103.94), swapRB=True, crop=False
    )
    return blob, (w / new_w, h / new_h)


def has_rendered_text(image, *, confidence_threshold: float = 0.5) -> bool:
    """True when EAST detects any text-shaped region in `image` (a PIL Image) above
    `confidence_threshold`. See module docstring for what this is/isn't appropriate for.
    Cheap and coarse by design — a yes/no trip-wire, not a transcription."""
    import cv2
    import numpy as np

    net = _get_net()
    blob, _scale = _prepare_blob(image, cv2, np)
    net.setInput(blob)
    scores, _geometry = net.forward(["feature_fusion/Conv_7/Sigmoid", "feature_fusion/concat_3"])
    return bool((scores[0, 0] >= confidence_threshold).any())


def detect_text_boxes(
    image, *, confidence_threshold: float = 0.5, nms_threshold: float = 0.4
) -> list[tuple[int, int, int, int]]:
    """Decoded, NMS-deduplicated (x, y, w, h) boxes in `image`'s own coordinate space —
    standard EAST post-processing (decode the rotated-box geometry output, apply
    non-max suppression, scale back from the resized detection input to the original
    image size). Used for building an inpaint mask (text_region_mask()) — has_rendered_
    text() above skips all of this and just checks the raw score map, since a yes/no
    answer doesn't need real boxes."""
    import cv2
    import numpy as np

    net = _get_net()
    blob, (scale_x, scale_y) = _prepare_blob(image, cv2, np)
    net.setInput(blob)
    scores, geometry = net.forward(["feature_fusion/Conv_7/Sigmoid", "feature_fusion/concat_3"])

    num_rows, num_cols = scores.shape[2:4]
    rects: list[list[float]] = []
    confidences: list[float] = []
    for y in range(num_rows):
        scores_row = scores[0, 0, y]
        x0, x1, x2, x3 = geometry[0, 0, y], geometry[0, 1, y], geometry[0, 2, y], geometry[0, 3, y]
        angles_row = geometry[0, 4, y]
        for x in range(num_cols):
            conf = float(scores_row[x])
            if conf < confidence_threshold:
                continue
            offset_x, offset_y = x * 4.0, y * 4.0
            angle = angles_row[x]
            cos_a, sin_a = float(np.cos(angle)), float(np.sin(angle))
            box_h = x0[x] + x2[x]
            box_w = x1[x] + x3[x]
            end_x = offset_x + cos_a * x1[x] + sin_a * x2[x]
            end_y = offset_y - sin_a * x1[x] + cos_a * x2[x]
            start_x, start_y = end_x - box_w, end_y - box_h
            rects.append([start_x, start_y, box_w, box_h])
            confidences.append(conf)

    if not rects:
        return []
    indices = cv2.dnn.NMSBoxes(rects, confidences, confidence_threshold, nms_threshold)
    boxes: list[tuple[int, int, int, int]] = []
    for i in (indices.flatten() if len(indices) else []):
        x, y, w, h = rects[int(i)]
        boxes.append((int(x * scale_x), int(y * scale_y), int(w * scale_x), int(h * scale_y)))
    return boxes


def text_region_mask(image, *, dilate_px: int = 16):
    """A binary PIL mask (white = repaint) covering detected text regions, dilated for
    margin so an inpaint pass fully covers glyph edges rather than leaving a garbled
    fringe — for feeding an inpaint pipeline's `mask_image`. None if no text detected."""
    from PIL import Image

    boxes = detect_text_boxes(image)
    if not boxes:
        return None
    import numpy as np

    mask = np.zeros((image.height, image.width), dtype=np.uint8)
    for x, y, w, h in boxes:
        x0, y0 = max(0, x - dilate_px), max(0, y - dilate_px)
        x1, y1 = min(image.width, x + w + dilate_px), min(image.height, y + h + dilate_px)
        mask[y0:y1, x0:x1] = 255
    return Image.fromarray(mask, mode="L")
