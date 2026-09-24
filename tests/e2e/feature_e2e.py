# -*- coding: utf-8 -*-
"""Feature-level end-to-end checks of the SHIPPED app, driven through a real browser engine.

Run by .github/workflows/mac-features-e2e.yml (not pytest), one feature per app launch:
    python feature_e2e.py <feature> [engine]

Features: chat_archive, extensions_smoke, vault, voice_reply, image.
The app is started by the workflow (from a read-only location, fresh user data, settings
pre-seeded to a tiny local Ollama model) with whatever test hooks that feature needs.
"""
from __future__ import annotations

import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

URL = f"http://127.0.0.1:{os.environ.get('LOMA_PORT', '8765')}/"
ERROR_MARKERS = ("Traceback", "Unknown extension", "failed to load", "Internal Server Error")


def body(page) -> str:
    try:
        return page.inner_text("body")
    except Exception:
        return ""


def wait_body(page, needle: str, timeout_s: float, fail_if: str | None = None) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        text = body(page)
        if needle.lower() in text.lower():
            return
        if fail_if and fail_if.lower() in text.lower():
            i = text.lower().index(fail_if.lower())
            raise AssertionError(f"saw {fail_if!r} while waiting for {needle!r}: {text[i:i + 300]!r}")
        time.sleep(2)
    raise AssertionError(f"timed out after {timeout_s}s waiting for {needle!r}")


def click_language_gate(page) -> None:
    page.wait_for_selector(".loma-language-gate", timeout=120_000)
    page.locator(".loma-language-gate button").click()


def open_workspace(page) -> None:
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
    page.locator(".loma-chat-input textarea").wait_for(timeout=240_000)
    try:
        page.wait_for_selector(".loma-startup-overlay", state="detached", timeout=240_000)
    except Exception:
        pass


def send_chat(page, text: str, timeout_s: float = 300) -> str:
    """Send a chat message and return the reply once the UI is idle again."""
    box = page.locator(".loma-chat-input textarea")
    box.fill(text)
    box.press("Enter")
    deadline = time.time() + 60
    while time.time() < deadline and text not in body(page):
        time.sleep(1)
    reply, last, stable_since = "", "", time.time()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        t = body(page)
        reply = t.split(text, 1)[1].strip() if text in t else ""
        busy = "thinking or processing" in reply or "Preparing output" in reply or reply.rstrip().endswith("stop")
        if reply != last:
            last, stable_since = reply, time.time()
        elif not busy and len(reply) > 5 and time.time() - stable_since > 6:
            return reply
        time.sleep(1)
    raise AssertionError(f"no complete chat reply within {timeout_s}s (last {reply[:200]!r})")


def extension_labels(page) -> list[str]:
    page.locator(".loma-extension-select").first.click()
    page.wait_for_selector(".q-menu .q-item", timeout=15_000)
    labels = [s.strip() for s in page.locator(".q-menu .q-item").all_inner_texts() if s.strip()]
    page.keyboard.press("Escape")
    time.sleep(0.5)
    return labels


def pick_extension(page, label: str) -> None:
    page.locator(".loma-extension-select").first.click()
    page.wait_for_selector(".q-menu .q-item", timeout=15_000)
    page.locator(".q-menu .q-item", has_text=label).first.click()
    time.sleep(1.5)


def panel_text(page) -> str:
    try:
        return page.inner_text("#loma-extension-panel")
    except Exception:
        return ""


# ---------------------------------------------------------------------------- features

def feat_chat_archive(page) -> None:
    open_workspace(page)
    reply = send_chat(page, "Reply with exactly three words.")
    print(f"chat reply: {reply[:80]!r}")
    page.locator("button", has=page.locator("text=bookmark_add")).first.click()
    wait_body(page, "Save chat freeze", 30)
    page.get_by_label("Title").fill("E2E archive test")
    page.locator(".q-dialog button", has_text="Save").last.click()
    wait_body(page, "Saved: E2E archive test", 30, fail_if="Save failed")
    print("chat saved to the archive")
    labels = extension_labels(page)
    archive = next((l for l in labels if "archive" in l.lower()), None)
    if not archive:
        raise AssertionError(f"no Chat Archive extension in {labels}")
    pick_extension(page, archive)
    wait_body(page, "E2E archive test", 30)
    print("saved chat is listed in the Chat Archive panel")


def feat_extensions_smoke(page) -> None:
    open_workspace(page)
    labels = [l for l in extension_labels(page) if l.strip().lower() not in ("none", "无", "無")]
    print(f"extensions offered: {labels}")
    if len(labels) < 8:
        raise AssertionError(f"expected 8 bundled extensions, saw {len(labels)}: {labels}")
    problems = []
    for label in labels:
        pick_extension(page, label)
        time.sleep(2)
        text = panel_text(page)
        bad = [m for m in ERROR_MARKERS if m.lower() in text.lower()]
        ok = len(text.strip()) > 20 and not bad
        print(f"  {'ok ' if ok else 'BAD'} {label}: {text.strip()[:70]!r}{' markers=' + str(bad) if bad else ''}")
        if not ok:
            problems.append(label)
    if problems:
        raise AssertionError(f"extensions that did not open cleanly: {problems}")


def feat_vault(page) -> None:
    open_workspace(page)
    labels = extension_labels(page)
    vault = next((l for l in labels if "vault" in l.lower() and "translation" not in l.lower()), None)
    if not vault:
        raise AssertionError(f"no Knowledge Vault extension in {labels}")
    pick_extension(page, vault)
    page.get_by_role("tab", name=re.compile("Session", re.I)).click()
    time.sleep(1)
    page.locator("button", has_text="Add folder").first.click()
    print("folder added (native picker bypassed by test hook); indexing…")
    wait_body(page, "Ready —", 300, fail_if="Error")
    print("index ready")
    page.get_by_placeholder(re.compile("Enter your query")).fill("Zorblax Protocol purple bananas")
    page.locator("button", has_text="Run").first.click()
    wait_body(page, "Zorblax", 180)
    print("search result from the indexed folder appeared in chat")


def feat_voice_reply(page) -> None:
    click_language_gate(page)
    wait_body(page, "Step 5: Voice reply", 240)
    print("voice reply step shown")
    page.locator("button", has_text=re.compile(r"\((male|female), ~")).first.click()
    page.locator("button", has_text="Download & continue").click()
    wait_body(page, "Step 6:", 600, fail_if="Voice input install failed")
    print("Piper voice installed (pip package + voice files), wizard moved on")


def feat_image(page) -> None:
    model_label = "E2E tiny test model"
    click_language_gate(page)
    wait_body(page, "Step 6: Image generation model", 240)
    page.locator(".q-checkbox", has_text=model_label).first.click()
    page.locator("button", has_text="Download & Finish").click()
    wait_body(page, "Download complete", 600, fail_if="Image model download failed")
    print("tiny image model downloaded through the app's own download path")
    open_workspace(page)
    before = page.locator("img").count()
    send_chat(page, "Generate an image of a red apple on a table.", timeout_s=600)
    deadline = time.time() + 300
    while time.time() < deadline and page.locator("img").count() <= before:
        time.sleep(3)
    if page.locator("img").count() <= before:
        raise AssertionError("no image appeared in the chat after generation")
    print("an image was generated and shown in the chat")


FEATURES = {
    "chat_archive": feat_chat_archive,
    "extensions_smoke": feat_extensions_smoke,
    "vault": feat_vault,
    "voice_reply": feat_voice_reply,
    "image": feat_image,
}


def main(feature: str, engine: str) -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = getattr(p, engine).launch()
        page = browser.new_page()
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        try:
            FEATURES[feature](page)
        except Exception as exc:
            print(f"[{engine}] {feature} FAILED: {exc}")
            print("--- page text ---")
            print(body(page)[:2500])
            print("--- extension panel ---")
            print(panel_text(page)[:1500])
            print("--- page errors ---")
            print("\n".join(errors[:20]) or "(none)")
            browser.close()
            return 1
        print(f"[{engine}] {feature} PASSED")
        if errors:
            print("--- page errors (informational) ---")
            print("\n".join(errors[:20]))
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "webkit"))
