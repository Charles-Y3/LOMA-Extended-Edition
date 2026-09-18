# -*- coding: utf-8 -*-
"""Fast, local anatomy-defect detection for generated images — flags diffusion output
with obviously wrong hands/face/limbs (extra or fused fingers, a second half-formed
face, a duplicated limb) so callers can reseed-and-retry, the same pattern
services/image_text_detection.py already uses for garbled rendered text.

Single-model pipeline: DWPose (COCO-WholeBody, 133 keypoints: 17 body + 6 feet
+ 68 face + 2x21 hand) estimates keypoints directly on the whole image, rather
than the usual two-stage design (a YOLOX person detector locates each person
first, then DWPose runs on that crop). DWPose's own reference implementation
already supports this — when no bbox is supplied it falls back to treating the
whole image as the one region (see IDEA-Research/DWPose's onnxpose.py:
`if len(out_bbox) == 0: out_bbox = [[0, 0, img_shape[1], img_shape[0]]]`).

This trades accuracy on busy multi-person or small-subject scenes (the
whole-image crop won't center well on any one person, so keypoint confidence
comes out low/noisy there) for cutting the model footprint from ~350MB
(YOLOX-L + DWPose) to ~134MB (DWPose alone) — acceptable here because the
existing presence-gating in the checks below means low/noisy confidence just
skips judgment rather than producing a false flag, and this check only needs
to catch the common case: a single dominant generated subject.

DWPose is a small pretrained ONNX model (yzd-v/DWPose on Hugging Face — the
official DWPose release, https://github.com/IDEA-Research/DWPose), downloaded
on first use via huggingface_hub and cached like any other model this app
downloads — not bundled in the repo.

Deliberately simple, "obvious mismatch only" heuristics, not a quality judge:
  - Hand: the 5 fingertip keypoints (thumb/index/middle/ring/pinky tips) must
    each be confidently located AND spatially distinct from one another —
    fused/duplicate fingertips collapsing onto nearly the same point is the
    classic diffusion tell, and a genuinely malformed hand also tends to starve
    one or more fingertips of confidence entirely.
  - Face: exactly one left-eye cluster and one right-eye cluster expected per
    detected face, roughly symmetric around the face's own center — a second,
    lower-confidence eye cluster is the classic melted-second-face tell.
  - Limb: deliberately the loosest check — only flags a limb keypoint that is
    essentially duplicated (near-identical position to another limb keypoint
    of the same type), never a plain low/missing count, since a normal photo
    cropped at the waist or with an arm behind the back legitimately has fewer
    visible limbs and must not be flagged for that alone.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import numpy as np

_DWPOSE_REPO = "yzd-v/DWPose"
_POSE_FILE = "dw-ll_ucoco_384.onnx"

_POSE_INPUT = (288, 384)  # (w, h)
_SIMCC_SPLIT_RATIO = 2.0

# COCO-WholeBody 133-keypoint layout.
_BODY = slice(0, 17)
_FACE = slice(23, 91)
_LEFT_HAND = slice(91, 112)
_RIGHT_HAND = slice(112, 133)
# Within the 68-point face block (COCO-WholeBody face order mirrors the common
# 68-point face-landmark scheme): left eye 36-41, right eye 42-47.
_LEFT_EYE = slice(36, 42)
_RIGHT_EYE = slice(42, 48)
# Body keypoint indices (COCO order): shoulders 5/6, elbows 7/8, wrists 9/10,
# hips 11/12, knees 13/14, ankles 15/16 — bilateral pairs for the limb check.
_LIMB_PAIRS = ((7, 8), (9, 10), (13, 14), (15, 16))

_KPT_SCORE_THRESHOLD = 0.3
# A hand entirely out of frame or hidden behind the body still gets a full 21-point
# prediction from DWPose — just a low-confidence one — so fingertip confidence alone
# can't distinguish "not present" from "present but malformed" (confirmed: an
# off-frame hand's wrist score sat at ~0.08 vs. ~0.7+ for a genuinely visible one).
# Gate on wrist presence first; only a confidently-present hand gets judged at all.
_HAND_PRESENCE_THRESHOLD = 0.4
# Midpoint between measured genuine-defect mean scores (~0.56-0.59) and measured
# clean-hand mean scores, spread or naturally curled alike (~0.77-0.82).
_HAND_MEAN_SCORE_THRESHOLD = 0.68

_pose_session = None
_session_lock = threading.Lock()


def anatomy_detection_deps_available() -> tuple[bool, str]:
    """(ok, detail) — mirrors image_text_detection.text_detection_deps_available()."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "onnxruntime"
    return True, ""


def _model_path() -> str:
    from huggingface_hub import hf_hub_download

    try:
        return hf_hub_download(repo_id=_DWPOSE_REPO, filename=_POSE_FILE, local_files_only=True)
    except Exception:
        return hf_hub_download(repo_id=_DWPOSE_REPO, filename=_POSE_FILE)


def _get_pose_session():
    global _pose_session
    if _pose_session is not None:
        return _pose_session
    with _session_lock:
        if _pose_session is None:
            import onnxruntime as ort

            _pose_session = ort.InferenceSession(_model_path(), providers=["CPUExecutionProvider"])
    return _pose_session


# ---- DWPose whole-body keypoint estimation (standard RTMPose SimCC decode) ----


def _bbox_to_center_scale(bbox: tuple[float, float, float, float], padding: float = 1.25):
    x0, y0, x1, y1 = bbox
    center = np.array([(x0 + x1) / 2, (y0 + y1) / 2])
    scale = np.array([(x1 - x0), (y1 - y0)]) * padding
    w, h = _POSE_INPUT
    aspect = w / h
    sw, sh = scale
    scale = np.array([sw, sw / aspect]) if sw > sh * aspect else np.array([sh * aspect, sh])
    return center, scale


def _warp_pose_input(img: np.ndarray, center: np.ndarray, scale: np.ndarray):
    import cv2

    w, h = _POSE_INPUT
    src_dir = np.array([0.0, scale[0] * -0.5])
    dst_dir = np.array([0.0, w * -0.5])

    def third_point(a, b):
        d = a - b
        return b + np.array([-d[1], d[0]])

    src = np.zeros((3, 2), dtype=np.float32)
    src[0] = center
    src[1] = center + src_dir
    src[2] = third_point(src[0], src[1])
    dst = np.zeros((3, 2), dtype=np.float32)
    dst[0] = [w / 2, h / 2]
    dst[1] = np.array([w / 2, h / 2]) + dst_dir
    dst[2] = third_point(dst[0], dst[1])

    warp_mat = cv2.getAffineTransform(src, dst)
    return cv2.warpAffine(img, warp_mat, (w, h), flags=cv2.INTER_LINEAR)


def _estimate_pose(img: np.ndarray, bbox: tuple[float, float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    """Returns (keypoints[133,2] in original image coords, scores[133])."""
    pose = _get_pose_session()
    center, scale = _bbox_to_center_scale(bbox)
    warped = _warp_pose_input(img, center, scale)
    mean = np.array([123.675, 116.28, 103.53])
    std = np.array([58.395, 57.12, 57.375])
    normalized = (warped.astype(np.float32) - mean) / std
    blob = normalized.transpose(2, 0, 1)[None].astype(np.float32)

    simcc_x, simcc_y = pose.run(None, {pose.get_inputs()[0].name: blob})
    x_locs = np.argmax(simcc_x[0], axis=1).astype(np.float32)
    y_locs = np.argmax(simcc_y[0], axis=1).astype(np.float32)
    x_vals = np.amax(simcc_x[0], axis=1)
    y_vals = np.amax(simcc_y[0], axis=1)
    scores = np.minimum(x_vals, y_vals)

    keypoints = np.stack([x_locs, y_locs], axis=-1) / _SIMCC_SPLIT_RATIO
    w, h = _POSE_INPUT
    keypoints = keypoints / np.array([w, h]) * scale + center - scale / 2
    return keypoints, scores


# ---- Simple "obvious mismatch" heuristics ----


def _hand_is_malformed(keypoints: np.ndarray, scores: np.ndarray, hand_box: slice, ref_size: float) -> bool:
    """Deliberately NOT based on fingertip position — a fingertip-distance check
    (two tips within some % of each other) can't tell "fingers fused by a bad
    render" apart from "fingers naturally close in a normal pose" (pinching,
    gripping, interlocked hands), because both produce near-identical keypoint
    geometry. Confirmed empirically: a genuinely fused hand and a clean pinch
    gesture had statistically indistinguishable fingertip-collapse distances.

    What does separate them: DWPose's own OVERALL confidence across the whole
    21-point hand, not just the fingertips. A confidently-present but badly
    rendered hand still gets a low mean score across most of its keypoints
    (~0.56-0.59, measured), while a clean hand scores meaningfully higher
    (~0.77-0.82) regardless of whether its fingers are spread or naturally
    curled/touching. Only fires on this clearer, coarser signal — accepts
    giving up detection of the "fingers touching" defect class specifically,
    since it's shown to be unreliable to separate from normal poses anyway."""
    if scores[hand_box.start] < _HAND_PRESENCE_THRESHOLD:
        return False  # hand not confidently present at all (off-frame/occluded) — nothing to judge
    mean_score = scores[hand_box.start:hand_box.stop].mean()
    return bool(mean_score < _HAND_MEAN_SCORE_THRESHOLD)


def _face_is_malformed(keypoints: np.ndarray, scores: np.ndarray, ref_size: float) -> bool:
    left_idx = list(range(_LEFT_EYE.start, _LEFT_EYE.stop))
    right_idx = list(range(_RIGHT_EYE.start, _RIGHT_EYE.stop))
    if scores[left_idx].mean() < _KPT_SCORE_THRESHOLD or scores[right_idx].mean() < _KPT_SCORE_THRESHOLD:
        return False  # not confident either eye is even there — nothing to flag
    left_center = keypoints[left_idx].mean(axis=0)
    right_center = keypoints[right_idx].mean(axis=0)
    return bool(np.linalg.norm(left_center - right_center) < 0.03 * ref_size)


def _limb_is_duplicated(keypoints: np.ndarray, scores: np.ndarray, ref_size: float) -> bool:
    for a, b in _LIMB_PAIRS:
        if scores[a] < _KPT_SCORE_THRESHOLD or scores[b] < _KPT_SCORE_THRESHOLD:
            continue
        if np.linalg.norm(keypoints[a] - keypoints[b]) < 0.02 * ref_size:
            return True  # left/right pair collapsed onto the same point — likely a phantom limb
    return False


def has_anatomy_defect(image) -> bool:
    """True when an obvious hand/face/limb mismatch is detected in `image` (a PIL
    Image). Cheap yes/no trip-wire, not a quality judge — see module docstring for
    exactly what each check does and does not flag, and for why this always treats
    the whole image as a single region rather than detecting each person first."""
    bgr = np.array(image.convert("RGB"))[:, :, ::-1]
    h, w = bgr.shape[:2]
    box = (0, 0, w, h)
    ref_size = max(w, h)
    if ref_size <= 0:
        return False
    keypoints, scores = _estimate_pose(bgr, box)
    if _hand_is_malformed(keypoints, scores, _LEFT_HAND, ref_size):
        return True
    if _hand_is_malformed(keypoints, scores, _RIGHT_HAND, ref_size):
        return True
    if _face_is_malformed(keypoints, scores, ref_size):
        return True
    if _limb_is_duplicated(keypoints, scores, ref_size):
        return True
    return False
