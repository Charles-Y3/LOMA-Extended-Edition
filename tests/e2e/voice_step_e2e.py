# -*- coding: utf-8 -*-
"""End-to-end check of the setup wizard's speech-recognition step in a READ-ONLY install.

The app must be started with LOMA_E2E_WIZARD_STEP=voice (opens the wizard on that step) from
a read-only location (macOS: a read-only disk image, which behaves like App Translocation;
Windows: a folder with write denied). Clicking "Download & continue" pip-installs
faster-whisper and downloads a Whisper model; that must succeed even though the app's own
folder cannot be written to (a tester hit "[Errno 30] Read-only file system" here).

Run by the e2e workflows, not pytest. Usage: python voice_step_e2e.py <engine>
"""
from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = f"http://127.0.0.1:{os.environ.get('LOMA_PORT', '8765')}/"
STEP = "Step 4: Speech recognition"
NEXT_STEP = "Step 5: Voice reply (optional)"
FAILED = "Voice input install failed"


def _body(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _wait_body(page, needle: str, timeout_s: float, fail_if: str | None = None) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        text = _body(page)
        if needle in text:
            return
        if fail_if and fail_if in text:
            i = text.index(fail_if)
            raise AssertionError(f"install failed: {text[i:i + 400]!r}")
        time.sleep(2)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {needle!r}")


def _set_checked(page, index: int, want: bool) -> None:
    box = page.locator(".q-checkbox").nth(index)
    checked = box.get_attribute("aria-checked") == "true"
    if checked != want:
        box.click()


def main(engine: str) -> int:
    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page()
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
            page.wait_for_selector(".loma-language-gate", timeout=120_000)
            page.locator(".loma-language-gate button").click()
            _wait_body(page, STEP, 240)
            print(f"[{engine}] speech step shown")

            # Checkboxes in order: whisper base, small, turbo, large, then SenseVoice.
            # Smallest Whisper only, and no SenseVoice (~1 GB) to keep CI quick; the pip
            # install of faster-whisper is the part that used to fail read-only.
            # E2E_SENSEVOICE=1 also installs SenseVoice (funasr is bundled; the ~1 GB model is
            # downloaded) — the slow variant, run from the heavy tier.
            with_sensevoice = os.environ.get("E2E_SENSEVOICE") == "1"
            for i, want in enumerate((True, False, False, False, with_sensevoice)):
                _set_checked(page, i, want)
            page.locator("button", has_text="Download & continue").click()
            print(f"[{engine}] clicked Download & continue")

            _wait_body(page, NEXT_STEP, 1800 if with_sensevoice else 900, fail_if=FAILED)
            print(f"[{engine}] voice input installed from a read-only app folder")
        except Exception as exc:
            print(f"[{engine}] FAILED: {exc}")
            print("--- page text ---")
            print(_body(page)[:2500])
            browser.close()
            return 1
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "chromium"))
