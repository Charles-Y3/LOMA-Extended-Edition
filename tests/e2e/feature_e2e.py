# -*- coding: utf-8 -*-
"""Feature-level end-to-end checks of the SHIPPED app, driven through a real browser engine.

Run by .github/workflows/mac-features-e2e.yml (not pytest), one feature per app launch:
    python feature_e2e.py <feature> [engine]

Features: chat_archive, extensions_smoke, vault, voice_reply, image, news_brief, research.
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
    page.goto(URL, wait_until="domcontentloaded", timeout=120_000)
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
    # "Download complete" is a toast that vanishes as the wizard closes; the wizard closing is
    # the reliable signal (a failed download keeps it open and reports the failure).
    deadline = time.time() + 600
    while time.time() < deadline and page.locator(".loma-setup-wizard").count() > 0:
        if "Image model download failed" in body(page) or "some_failed" in body(page):
            raise AssertionError("image model download failed")
        time.sleep(3)
    if page.locator(".loma-setup-wizard").count() > 0:
        raise AssertionError("wizard never finished the image model download")
    print("tiny image model downloaded through the app's own download path; wizard closed")
    open_workspace(page)
    send_chat(page, "Generate an image of a red apple on a table.", timeout_s=600)
    # The chat shows the result as a file card (not an inline <img>); a failure shows a .txt card
    # under the same "Image ready" text, so require the .png name (the shell step also checks the file).
    deadline = time.time() + 120
    while time.time() < deadline and ".png" not in body(page):
        time.sleep(3)
    if ".png" not in body(page):
        raise AssertionError("no .png image card appeared in the chat after generation")
    print("an image (.png) was generated and shown in the chat")


def feat_news_brief(page) -> None:
    """Live web: searches news feeds, scrapes articles, streams a brief with references into
    the chat. Exercises HTTPS from the frozen app (the certificate fix) end to end."""
    open_workspace(page)
    labels = extension_labels(page)
    news = next((l for l in labels if "news" in l.lower()), None)
    if not news:
        raise AssertionError(f"no News Brief extension in {labels}")
    pick_extension(page, news)
    page.locator("button", has_text="Generate brief").first.click()
    print("brief requested; waiting for the web search")
    wait_body(page, "candidate article", 300, fail_if="No news articles could be loaded")
    print("search reached the internet and found articles")
    wait_body(page, "Loaded", 600, fail_if="No news articles could be loaded")
    print("articles were downloaded and read")
    wait_body(page, "News brief generated", 900)
    print("brief was written and streamed into the chat")


def feat_research(page) -> None:
    """Guided research: plan (LLM drafts clarify questions) -> confirm -> web + synthesis."""
    open_workspace(page)
    labels = extension_labels(page)
    res = next((l for l in labels if l.strip().lower().startswith("research")), None)
    if not res:
        raise AssertionError(f"no Research extension in {labels}")
    pick_extension(page, res)
    page.get_by_placeholder(re.compile("e.g. Impact of kindness")).fill(
        "History of the printing press and its impact on literacy in Europe"
    )
    page.locator("button", has_text="Plan research").first.click()
    wait_body(page, "Confirm or edit", 300, fail_if="Clarify plan failed")
    print("clarify questions were drafted")
    page.locator("button", has_text="Start research").first.click()
    # The runner has 3 CPUs and the test model is tiny but CPU-only, so the LLM steps (credibility
    # ranking of each source, then synthesis) are slow: allow 45 min and log progress so a stall
    # can be told apart from slowness.
    deadline, last_log = time.time() + 2700, 0.0
    while time.time() < deadline:
        text = body(page)
        if "research complete" in text.lower():
            break
        if "research failed" in text.lower():
            raise AssertionError("research reported a failure")
        if time.time() - last_log > 240:
            lines = [l for l in panel_text(page).splitlines() if l.strip()]
            print(f"  [{int(2700 - (deadline - time.time()))}s] research progress: {' | '.join(lines[-3:])[:220]}")
            last_log = time.time()
        time.sleep(5)
    else:
        raise AssertionError("research did not complete within 45 minutes")
    print("research ran (web search + synthesis) and completed")
    wait_body(page, "Regenerate", 60)
    print("results screen is showing")


FEATURES = {
    "chat_archive": feat_chat_archive,
    "extensions_smoke": feat_extensions_smoke,
    "vault": feat_vault,
    "voice_reply": feat_voice_reply,
    "image": feat_image,
    "news_brief": feat_news_brief,
    "research": feat_research,
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
