# -*- coding: utf-8 -*-
"""End-to-end check of the shipped macOS app's first-run screens, driven through a real
browser engine (chromium or webkit == Safari's engine) against an already-running app.

Run by .github/workflows/mac-e2e.yml, not by pytest. Usage: python mac_wizard_e2e.py <engine>
"""
from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = f"http://127.0.0.1:{os.environ.get('LOMA_PORT', '8765')}/"


def _body(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def _wait_body_contains(page, needle: str, timeout_s: float, fail_if: str | None = None) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        text = _body(page)
        if needle in text:
            return
        if fail_if and fail_if in text:
            raise AssertionError(f"saw {fail_if!r} while waiting for {needle!r}")
        time.sleep(1)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {needle!r}")


def main(engine: str) -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
        failed_urls: list[str] = []
        page.on("requestfailed", lambda r: failed_urls.append(r.url))
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=120_000)

            page.wait_for_selector(".loma-language-gate", timeout=120_000)
            print(f"[{engine}] language gate shown")
            page.locator(".loma-language-gate button").click()

            _wait_body_contains(page, "Step 1: Network connectivity", 180)
            print(f"[{engine}] setup wizard step 1 shown")

            # The exact bug a Mac user hit: stdlib urllib had no CA bundle, so the probe
            # failed and the wizard reported "offline" with downloads disabled.
            _wait_body_contains(page, "You are online", 90, fail_if="You appear offline")
            print(f"[{engine}] wizard reports ONLINE")

            page.locator("button", has_text="Continue").last.click()
            _wait_body_contains(page, "Step 2: Choose LLM provider", 60)
            print(f"[{engine}] advanced to provider step")
        except Exception as exc:
            print(f"[{engine}] FAILED: {exc}")
            print("--- page text ---")
            print(_body(page)[:3000])
            print("--- browser errors ---")
            print("\n".join(errors[:30]))
            print("--- failed request URLs (unique) ---")
            print("\n".join(sorted(set(failed_urls))[:15]) or "(none)")
            # Diagnostic: is the wizard merely stuck on a page that has since reloaded?
            # If a manual reload brings it back, the app is fine; if not, the server thinks
            # the wizard is already running and never re-shows it.
            try:
                page.reload(wait_until="domcontentloaded", timeout=60_000)
                time.sleep(25)
                print("--- after manual reload ---")
                print("wizard visible:", "Step 1: Network connectivity" in _body(page))
                print(_body(page)[:600])
            except Exception as reload_exc:
                print(f"reload diagnostic failed: {reload_exc}")
            browser.close()
            return 1
        print("--- browser errors (informational) ---")
        print("\n".join(errors[:30]) or "(none)")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "chromium"))
