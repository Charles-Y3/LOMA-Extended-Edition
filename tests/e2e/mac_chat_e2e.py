# -*- coding: utf-8 -*-
"""End-to-end chat check of the shipped macOS app: with settings pre-seeded to a tiny local
Ollama model, open the workspace in a real browser engine (chromium or webkit == Safari's
engine), send a message, and require a real reply to stream back.

Run by .github/workflows/mac-e2e.yml, not by pytest. Usage: python mac_chat_e2e.py <engine>
"""
from __future__ import annotations

import os
import sys
import time

from playwright.sync_api import sync_playwright

URL = f"http://127.0.0.1:{os.environ.get('LOMA_PORT', '8765')}/"
PROMPT = "In one short sentence, what is the capital of France?"
ERROR_MARKERS = ("Traceback", "not reachable", "Connection refused", "No model", "not installed")


def _body(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def main(engine: str) -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console: {m.text}") if m.type == "error" else None)
        try:
            page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
            box = page.locator(".loma-chat-input textarea")
            box.wait_for(timeout=240_000)
            print(f"[{engine}] workspace chat input shown (wizard skipped via seeded settings)")

            try:
                page.wait_for_selector(".loma-startup-overlay", state="detached", timeout=240_000)
            except Exception:
                print(f"[{engine}] startup overlay never detached (continuing)")
            print(f"[{engine}] startup overlay gone")

            box.fill(PROMPT)
            box.press("Enter")
            print(f"[{engine}] message sent")

            deadline = time.time() + 60
            while time.time() < deadline and PROMPT not in _body(page):
                time.sleep(1)
            if PROMPT not in _body(page):
                raise AssertionError("sent message never appeared in the chat")

            reply, last, stable_since = "", "", time.time()
            deadline = time.time() + 300
            while time.time() < deadline:
                text = _body(page)
                reply = text.split(PROMPT, 1)[1].strip() if PROMPT in text else ""
                for marker in ERROR_MARKERS:
                    if marker.lower() in reply.lower():
                        raise AssertionError(f"error text in the reply area: {marker!r}")
                if reply != last:
                    last, stable_since = reply, time.time()
                elif len(reply) > 15 and time.time() - stable_since > 8:
                    break
                time.sleep(1)
            else:
                raise AssertionError(f"no complete reply within 300s (last text: {reply[:200]!r})")

            print(f"[{engine}] reply received ({len(reply)} chars): {reply[:300]!r}")
            print(f"[{engine}] mentions Paris: {'paris' in reply.lower()} (informational)")
        except Exception as exc:
            print(f"[{engine}] FAILED: {exc}")
            print("--- page text ---")
            print(_body(page)[:3000])
            print("--- browser errors ---")
            print("\n".join(errors[:30]))
            browser.close()
            return 1
        print("--- browser errors (informational) ---")
        print("\n".join(errors[:30]) or "(none)")
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "chromium"))
