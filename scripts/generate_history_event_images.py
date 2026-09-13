#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate and save History Events encounter images into bundled assets."""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from extensions.history_events.encounters import ENCOUNTERS  # noqa: E402
from extensions.history_events.images import BUNDLED_IMAGE_DIR, get_encounter_image_path  # noqa: E402


def main() -> int:
    from services.model_router import image_generation_deps_available

    ok, missing = image_generation_deps_available()
    if not ok:
        print(f"Image generation unavailable: {missing}")
        return 1

    from services.image_generation import generate_image

    os.makedirs(BUNDLED_IMAGE_DIR, exist_ok=True)
    made = 0
    skipped = 0
    failed = 0
    for enc in ENCOUNTERS:
        out = os.path.join(BUNDLED_IMAGE_DIR, f"{enc.id}.png")
        if os.path.isfile(out):
            skipped += 1
            continue
        existing = get_encounter_image_path(enc.id)
        if existing and os.path.isfile(existing) and os.path.abspath(existing) != os.path.abspath(out):
            import shutil

            shutil.copy2(existing, out)
            print(f"copied {enc.id}")
            made += 1
            continue
        print(f"generating {enc.id} …")
        try:
            result = generate_image(
                enc.image_prompt,
                output_path=out,
                width=768,
                height=432,
                steps=22,
            )
            if result.path and os.path.isfile(result.path) and not result.fallback:
                made += 1
            else:
                failed += 1
                print(f"  failed: {getattr(result, 'error', 'fallback')}")
        except Exception as ex:
            failed += 1
            print(f"  error: {ex}")
    print(f"done — created/copied={made} skipped={skipped} failed={failed} total={len(ENCOUNTERS)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
