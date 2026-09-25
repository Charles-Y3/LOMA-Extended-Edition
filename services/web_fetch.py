# tools/web_parser.py
# -*- coding: utf-8 -*-

import asyncio
import os
import re
import threading
from playwright.sync_api import sync_playwright
import trafilatura

from services.web_fetch_policy import responsible_use_notice, check_fetch_allowed, user_agent

def _scraper_log(msg: str) -> None:
    try:
        from services.session import state

        state.add_log(msg)
    except Exception:
        pass


_browser_ready_cache: bool | None = None


def _probe_browser_sync() -> bool:
    ready = False
    try:
        with sync_playwright() as p:
            exe = p.chromium.executable_path
            if exe and os.path.isfile(exe):
                ready = True
            else:
                for channel in ("msedge", "chrome"):
                    try:
                        browser = p.chromium.launch(headless=True, channel=channel)
                        browser.close()
                        ready = True
                        break
                    except Exception:
                        continue
    except Exception as exc:
        _scraper_log(f"Scraper: browser_automation_ready probe failed: {exc}")
        ready = False
    return ready


def browser_automation_ready(*, force_refresh: bool = False) -> bool:
    """True if web_fetch can actually launch a browser: a system Chrome/Edge Playwright
    can attach to via `channel=`, or a Playwright-managed Chromium already downloaded
    via `playwright install chromium`. The `playwright` pip package itself is a core,
    always-installed dependency (requirements.txt) — this checks the separate browser
    *binary*, which pip does not install and must be fetched once via the CLI/installer."""
    global _browser_ready_cache
    if _browser_ready_cache is not None and not force_refresh:
        return _browser_ready_cache

    # Playwright's sync API refuses to run on a thread with an active asyncio event
    # loop (NiceGUI's UI-construction thread, e.g. building the Settings panel). This
    # status check can be called from that thread, unlike the rest of this module's
    # scraping code which always runs inside a plain worker thread — so run the actual
    # probe on a dedicated thread every time, never directly on the caller's thread.
    try:
        asyncio.get_running_loop()
        on_event_loop_thread = True
    except RuntimeError:
        on_event_loop_thread = False

    if on_event_loop_thread:
        result: dict[str, bool] = {}
        t = threading.Thread(target=lambda: result.__setitem__("ready", _probe_browser_sync()), daemon=True)
        t.start()
        t.join(timeout=15)
        ready = result.get("ready", False)
    else:
        ready = _probe_browser_sync()

    _browser_ready_cache = ready
    return ready



def convert_styled_to_llm_markdown(styled_text: str) -> str:
    """
    Translates verbose custom style blocks into basic Markdown headers
    and structured indicators optimized for local LLM attention windows.
    """
    lines = []
    pattern = re.compile(r'<text-block([^>]*)>(.*?)</text-block>', re.DOTALL)

    for match in pattern.finditer(styled_text):
        attrs, content = match.group(1), match.group(2).strip()
        if not content:
            continue

        is_bold = 'weight="bold"' in attrs
        is_italic = 'slant="italic"' in attrs

        # Pull visual dimensions to deduce clear header markers
        size_match = re.search(r'size="(\d+)', attrs)
        font_size = int(size_match.group(1)) if size_match else 12

        # Normalize hierarchy weights down into native markdown bounds
        if font_size >= 24:
            content = f"\n# {content}\n"
        elif font_size >= 18:
            content = f"\n## {content}\n"
        elif font_size >= 14:
            content = f"\n### {content}\n"
        else:
            if is_bold:
                content = f"**{content}**"
            if is_italic:
                content = f"*{content}*"

        lines.append(content)

    return "\n".join(lines)


def scrape_website_text(url, *, skip_policy_check: bool = False):
    """
    Automatically detects available system browsers (Chrome -> Edge) headlessly,
    renders dynamic JavaScript configurations via Playwright, and extracts BOTH
    theme-safe adaptive styled layouts AND high-signal simple plain text via Trafilatura.

    skip_policy_check: caller already ran check_research_request/check_fetch_allowed
    for this URL (e.g. moments earlier) — avoids re-tripping our own rate limiter.

    Returns:
        dict: A triple-representation dictionary mapping 'raw_styled', 'content',
              and 'simple_text', or an error description string.
    """
    from services.web_context_cache import get_cached, set_cached

    cached = get_cached(url)
    if cached is not None:
        _scraper_log("Scraper: Using cached page content (no browser launch).")
        return cached

    if not skip_policy_check:
        allowed, reason = check_fetch_allowed(url)
        if not allowed:
            _scraper_log(f"Scraper: Fetch blocked — {reason}")
            msg = f"⚠️ **Web fetch blocked**\n\n{reason}\n\n{responsible_use_notice()}"
            return {"raw_styled": msg, "content": msg, "simple_text": msg, "error": True}

    html_content = ""
    styled_content = ""

    # Ordered list of browser execution channels to test on the host machine
    browser_channels = ["msedge", "chrome"]
    browser_launched = False
    last_error_msg = ""

    try:
        with sync_playwright() as p:
            # Iteratively attempt to leverage an available system browser channel
            for channel in browser_channels:
                try:
                    _scraper_log(f"Scraper: Attempting to launch host browser via channel: '{channel}'")
                    browser = p.chromium.launch(headless=True, channel=channel)
                    browser_launched = True
                    _scraper_log(f"Scraper: Successfully connected to system '{channel}' execution engine.")
                    break
                except Exception as channel_err:
                    last_error_msg = str(channel_err)
                    _scraper_log(f"Scraper: Channel '{channel}' not available or failed initialization.")
                    continue

            # If host channels fail, try fallback to standard local Playwright binaries (if present)
            if not browser_launched:
                try:
                    _scraper_log("Scraper: Testing fallback to standalone packaged Playwright binaries...")
                    browser = p.chromium.launch(headless=True)
                    browser_launched = True
                except Exception as fallback_err:
                    last_error_msg = str(fallback_err)

            # If completely blocked by missing execution binaries, return action guidelines to user
            if not browser_launched:
                _scraper_log("Scraper Error: No compatible local system browsers found.")
                missing_prompt = (
                    "⚠️ **Web Extraction Setup Error**\n\n"
                    "LOMA could not auto-detect a native installation of **Google Chrome** or **Microsoft Edge** "
                    "on this machine's standard path locations.\n\n"
                    "**Easiest fix:** open **Settings → Configuration → Optional add-ons** and click Install next "
                    "to \"Web page reading (Playwright)\" — one click, no terminal needed.\n\n"
                    "*If you're running from source, you can instead run this once in your project terminal:*\n"
                    "```bash\n"
                    "playwright install chromium\n"
                    "```\n"
                    "*Or install Google Chrome / Microsoft Edge to its default system directory.*"
                )
                return {"raw_styled": missing_prompt, "content": missing_prompt, "simple_text": missing_prompt,
                        "error": True}

            # Standard context preparation and navigation processing layer
            context = browser.new_context(
                user_agent=user_agent(),
                viewport={"width": 1280, "height": 720}
            )

            from services.security.url_guard import install_route_guard

            install_route_guard(context)  # redirects/sub-resources may not reach local or private hosts
            page = context.new_page()

            # Heavy news sites (e.g. abc.net.au) need longer navigation + settle time
            # before Trafilatura can see article text — short timeouts yield empty scrapes
            # that look fine in a quick preview but fail on export/recompile.
            page.goto(url, timeout=45000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector(
                    "article, [role='main'], .article-body, .Article, main",
                    timeout=8000,
                )
            except Exception:
                pass
            page.wait_for_timeout(3500)
            # `url` can be a redirect (e.g. a Google News link) — page.url after goto() is
            # where the browser actually landed, i.e. the real article URL, needed so
            # citations don't point back at the redirect.
            final_url = page.url or url

            # --- CAPTURE RAW HTML STREAM FOR TRAFILATURA ---
            html_content = page.content()

            # --- CLIENT-SIDE LAYOUT STYLE & HIGH-SIGNAL CONTENT ENGINE ---
            _scraper_log("Scraper: Evaluating DOM computed style metrics and tracking primary text nodes...")

            styled_content = page.evaluate("""
                                           () => {
                                               function parseToColorValues(colorStr) {
                                                   const rgbaValues = colorStr.match(/\\d+/g);
                                                   if (rgbaValues && rgbaValues.length >= 3) {
                                                       return {
                                                           r: parseInt(rgbaValues[0]),
                                                           g: parseInt(rgbaValues[1]),
                                                           b: parseInt(rgbaValues[2])
                                                       };
                                                   }
                                                   return null;
                                               }

                                               function extractStyledText(element, isWithinDedicatedMainElement = false) {
                                                   const forbiddenTags = [
                                                       'SCRIPT', 'STYLE', 'NOSCRIPT', 'NAV', 'FOOTER', 'IFRAME', 'HEADER',
                                                       'FORM', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'MENU'
                                                   ];
                                                   if (forbiddenTags.includes(element.tagName)) return '';

                                                   if (!isWithinDedicatedMainElement) {
                                                       const className = (element.className || '').toString().toLowerCase();
                                                       const idName = (element.id || '').toString().toLowerCase();
                                                       const noisePatterns = ['sidebar', 'widget', 'banner', 'ads', 'advertisement', 'cookie', 'popup', 'social-share'];
                                                       if (noisePatterns.some(pat => className.includes(pat) || idName.includes(pat))) {
                                                           return '';
                                                       }
                                                   }

                                                   const style = window.getComputedStyle(element);
                                                   if (style.display === 'none' || style.visibility === 'hidden' || parseFloat(style.opacity) === 0) {
                                                       return '';
                                                   }

                                                   let result = '';

                                                   if (element.childNodes.length > 0) {
                                                       let hasTextDirectly = false;
                                                       for (let node of element.childNodes) {
                                                           if (node.nodeType === Node.TEXT_NODE && node.nodeValue.trim().length > 0) {
                                                               hasTextDirectly = true;
                                                               break;
                                                           }
                                                       }

                                                       if (hasTextDirectly) {
                                                           const text = element.innerText ? element.innerText.trim() : '';
                                                           if (!text || text.length < 2) return '';

                                                           if (!isWithinDedicatedMainElement) {
                                                               const totalTextLength = text.length;
                                                               let linkTextLength = 0;
                                                               const anchorElements = element.getElementsByTagName('a');
                                                               for (let a of anchorElements) {
                                                                   linkTextLength += (a.innerText || '').trim().length;
                                                               }
                                                               if (totalTextLength > 0 && (linkTextLength / totalTextLength) > 0.75) {
                                                                   return '';
                                                               }
                                                           }

                                                           const fontFamily = style.fontFamily.replace(/["']/g, '').split(',')[0].trim();
                                                           const fontSize = style.fontSize;
                                                           const fontWeight = style.fontWeight;
                                                           let color = style.color;
                                                           const isItalic = style.fontStyle === 'italic' ? 'italic' : 'normal';
                                                           const textAlign = style.textAlign;

                                                           const colorMeta = parseToColorValues(color);
                                                           let calculatedColorAttr = color;
                                                           if (colorMeta) {
                                                               const brightness = (colorMeta.r * 299 + colorMeta.g * 587 + colorMeta.b * 114) / 1000;
                                                               if (brightness < 60) {
                                                                   calculatedColorAttr = "DOM_DARK_MUTED";
                                                               }
                                                           }

                                                           let weightLabel = 'regular';
                                                           if (parseInt(fontWeight) >= 600 || fontWeight === 'bold') weightLabel = 'bold';

                                                           return `<text-block font="${fontFamily}" size="${fontSize}" weight="${weightLabel}" slant="${isItalic}" color="${calculatedColorAttr}" align="${textAlign}">\\n${text}\\n</text-block>\\n\\n`;
                                                       } else {
                                                           for (let child of element.children) {
                                                               result += extractStyledText(child, isWithinDedicatedMainElement);
                                                           }
                                                       }
                                                   }
                                                   return result;
                                               }

                                               const mainSelectors = ['article', '[role="main"]', '.main-content', '#main-content', '.post-content', '.article-body', '#content', 'main'];
                                               let rootTarget = null;
                                               let isDedicatedMainFound = false;

                                               for (let selector of mainSelectors) {
                                                   const target = document.querySelector(selector);
                                                   if (target && target.innerText && target.innerText.trim().length > 250) {
                                                       rootTarget = target;
                                                       isDedicatedMainFound = true;
                                                       break;
                                                   }
                                               }

                                               rootTarget = rootTarget || document.body;
                                               return extractStyledText(rootTarget, isDedicatedMainFound);
                                           }
                                           """)

            browser.close()

        # --- PROCESS CLEAN HEURISTIC TEXT VIA TRAFILATURA ---
        _scraper_log("Scraper: Extracting text using trafilatura processing layout rules...")
        clean_text_fallback = ""
        if html_content:
            clean_text_fallback = trafilatura.extract(
                html_content,
                include_links=False,
                include_images=False,
                include_tables=True,
                output_format='txt'
            ) or ""

        if not styled_content or not styled_content.strip():
            if clean_text_fallback:
                # If styled calculation completely missed but trafilatura got content, synthesize dummy nodes
                styled_content = f'<text-block font="sans-serif" size="13px">{clean_text_fallback}</text-block>'
            else:
                err_msg = f"Error: Failed to isolate primary content text layers from {url}"
                return {"raw_styled": err_msg, "content": err_msg, "simple_text": err_msg, "error": True}

        _scraper_log("Scraper: Generating hybrid representation maps...")
        result = {
            "raw_styled": styled_content.strip(),
            "content": convert_styled_to_llm_markdown(styled_content),
            "simple_text": clean_text_fallback if clean_text_fallback.strip() else convert_styled_to_llm_markdown(
                styled_content),
            "final_url": final_url,
        }
        if not result.get("error"):
            set_cached(url, result)
        return result

    except Exception as e:
        error_payload = f"Web extraction pipeline failure: {str(e)}"
        _scraper_log(f"Scraper Critical Exception: {error_payload}")
        return {"raw_styled": error_payload, "content": error_payload, "simple_text": error_payload, "error": True}