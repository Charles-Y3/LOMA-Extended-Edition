# -*- coding: utf-8 -*-
"""Single dispatch point for image-edit instructions.

Routes an edit instruction to the pixel-editing service that actually fits it
(composite / recolor / inpaint / img2img), instead of regenerating a fresh
image from a text prompt. Shared by the direct pipeline, the agentic/plan
pipeline, and Art Studio's "Auto" mode.
"""
from __future__ import annotations

from typing import Any


def dispatch_image_mutation(
    *,
    source_paths: list[str],
    instruction: str,
    output_path: str | None = None,
    strength: float | None = None,
    steps: int | None = None,
    model_id: str | None = None,
    progress_cb=None,
) -> Any:
    """Edit an existing image per `instruction`.

    `source_paths[0]` is the target/primary image; `source_paths[1]`, if
    present, is a donor image for cross-image composite edits (e.g. "put the
    person from photo B into photo A").

    `strength`/`steps`/`model_id`/`progress_cb` are forwarded to the two
    diffusion-backed paths (inpaint/subject-or-background-swap, img2img edit) so
    callers can apply the user's saved quality/model preference instead of each
    service's own hardcoded fallback defaults; omit any of them to keep that
    service's default.
    """
    if not source_paths:
        raise ValueError("dispatch_image_mutation requires at least one source image")

    target_path = source_paths[0]
    donor_path = source_paths[1] if len(source_paths) > 1 else None

    if donor_path:
        from services.image_composite import (
            is_composite_edit,
            is_cross_image_edit,
            is_multi_source_create,
            run_composite_edit,
        )

        if is_cross_image_edit(instruction) or is_multi_source_create(instruction) or is_composite_edit(
            instruction
        ):
            return run_composite_edit(
                source_path=target_path,
                donor_path=donor_path,
                instruction=instruction,
                output_path=output_path,
            )

    from services.image_inpaint import (
        inpaint_image_local,
        is_simple_color_edit,
        recolor_image_local,
        swap_subject_local,
    )

    if is_simple_color_edit(instruction):
        return recolor_image_local(
            source_path=target_path,
            instruction=instruction,
            output_path=output_path,
        )

    diffusion_kwargs: dict[str, Any] = {}
    if strength is not None:
        diffusion_kwargs["strength"] = strength
    if steps is not None:
        diffusion_kwargs["steps"] = steps
    if model_id is not None:
        diffusion_kwargs["model_id"] = model_id
    if progress_cb is not None:
        diffusion_kwargs["progress_cb"] = progress_cb

    from pipeline.query_intent_i18n import matches

    # This pipeline can reliably do two kinds of local edit: swap the main
    # subject (rembg cutout + isolated regenerate + recomposite onto the
    # untouched original) or swap the background (mirror image — regenerate the
    # frame, recomposite the untouched subject on top). A generic whole-frame
    # img2img edit for anything else (arbitrary style transforms, multi-region
    # edits, ...) reliably produced the "melted"/incoherent results users were
    # reporting, since it has no notion of protecting anything — so it's no
    # longer attempted here; unmatched requests get a clear limitation message
    # instead of a low-quality attempt.
    if matches(instruction, "background_change_target_hints"):
        return inpaint_image_local(
            source_path=target_path,
            instruction=instruction,
            output_path=output_path,
            **diffusion_kwargs,
        )

    swapped = swap_subject_local(
        source_path=target_path,
        instruction=instruction,
        output_path=output_path,
        **diffusion_kwargs,
    )
    if swapped is not None:
        return swapped

    from pipeline.i18n import t as tr
    from services.image_generation import ImageGenerationResult

    return ImageGenerationResult(
        output_path or target_path, instruction, 0, 0, 0, 0, 0.0, model_id or "",
        fallback=True, error=tr("chat.mutation_unsupported_edit"),
    )
