# -*- coding: utf-8 -*-
import asyncio

from nicegui import background_tasks, ui

from services.session import state
from ui.themes import registry

_upload_refresh_tasks: dict[int, asyncio.Task] = {}

CHAT_SCROLL_ID = "loma-chat-scroll"
CONSOLE_SCROLL_ID = "loma-console-scroll"


def scroll_chat() -> None:
    """Retries are done with plain JS setTimeout/rAF, not ui.timer — a ui.timer
    created here runs later on the server, and the chat container rebuilds
    from scratch on every message, which can delete that timer's parent slot
    before it fires (raising "parent slot ... has been deleted" and silently
    dropping the correction). Pure client-side retries have no such lifecycle
    to outlive."""
    try:
        ui.run_javascript(
            f"""
            (function() {{
                const area = document.getElementById("{CHAT_SCROLL_ID}");
                if (!area) return;
                function doScroll() {{
                    const container = area.querySelector('.q-scrollarea__container')
                        || area.querySelector('.scroll')
                        || area;
                    container.scrollTop = container.scrollHeight;
                }}
                doScroll();
                requestAnimationFrame(doScroll);
                setTimeout(doScroll, 60);
                setTimeout(doScroll, 180);
            }})();
            """
        )
    except Exception:
        pass


def schedule_scroll_chat(delay: float = 0.05) -> None:
    scroll_chat()


def scroll_chat_to_id_prefix(id_prefix: str) -> None:
    """Scroll so the latest element whose id starts with `id_prefix` sits near
    the top of the chat viewport, instead of the usual scroll-to-bottom. Same
    client-side-only retry approach as scroll_chat() — see its docstring."""
    try:
        ui.run_javascript(
            f"""
            (function() {{
                const area = document.getElementById("{CHAT_SCROLL_ID}");
                if (!area) return;
                function doScroll() {{
                    const matches = area.querySelectorAll('[id^="{id_prefix}"]');
                    if (!matches.length) return;
                    matches[matches.length - 1].scrollIntoView({{block: 'start', behavior: 'auto'}});
                }}
                doScroll();
                requestAnimationFrame(doScroll);
                setTimeout(doScroll, 60);
                setTimeout(doScroll, 180);
            }})();
            """
        )
    except Exception:
        pass


def schedule_scroll_chat_to_id_prefix(id_prefix: str, delay: float = 0.05) -> None:
    scroll_chat_to_id_prefix(id_prefix)


def scroll_preview_to_bottom(_view_mode: str | None = None) -> None:
    """Scroll the shared Preview scroll area to the latest content."""
    try:
        ui.run_javascript(
            """
            (function() {
                function scrollPreview() {
                    const wrap = document.querySelector('.loma-preview-scroll-wrap');
                    if (!wrap) return;
                    const textarea = wrap.querySelector('.loma-preview-editor textarea');
                    const container = wrap.querySelector('.q-scrollarea__container');
                    const scroller = textarea || container;
                    if (!scroller) return;
                    scroller.scrollTop = scroller.scrollHeight;
                    const anchor = wrap.querySelector('.loma-markup-canvas > *:last-child')
                        || textarea;
                    if (anchor && anchor !== scroller) {
                        anchor.scrollIntoView({ block: 'end', behavior: 'instant' });
                    }
                }
                scrollPreview();
                requestAnimationFrame(function() {
                    requestAnimationFrame(scrollPreview);
                });
            })();
            """
        )
    except Exception:
        pass


def schedule_scroll_preview(_view_mode: str | None = None, delay: float = 0.05) -> None:
    scroll_preview_to_bottom()
    try:
        ui.timer(delay, scroll_preview_to_bottom, once=True)
        ui.timer(delay + 0.12, scroll_preview_to_bottom, once=True)
        ui.timer(delay + 0.28, scroll_preview_to_bottom, once=True)
    except Exception:
        pass


def scroll_console() -> None:
    try:
        ui.run_javascript(
            f"""
            (function() {{
                const area = document.getElementById("{CONSOLE_SCROLL_ID}");
                if (!area) return;
                const container = area.querySelector('.q-scrollarea__container')
                    || area.querySelector('.scroll')
                    || area;
                if (!container) return;
                const gap = container.scrollHeight - container.scrollTop - container.clientHeight;
                if (gap < 48) {{
                    container.scrollTop = container.scrollHeight;
                    requestAnimationFrame(function() {{
                        container.scrollTop = container.scrollHeight;
                    }});
                }}
            }})();
            """
        )
    except Exception:
        pass


def schedule_scroll_console(delay: float = 0.05) -> None:
    try:
        ui.timer(delay, scroll_console, once=True)
    except Exception:
        scroll_console()


def inject_custom_assets() -> None:
    from ui.branding import LOMA_ICON_URL, favicon_for_nicegui

    icon = favicon_for_nicegui()
    ui.add_head_html(
        f'<link rel="icon" type="image/png" href="{icon}">'
        f'<link rel="shortcut icon" type="image/png" href="{icon}">'
        f'<link rel="apple-touch-icon" href="{LOMA_ICON_URL}">'
    )
    ui.add_head_html("""
        <style>
            .no-scrollbar::-webkit-scrollbar { display: none; }
            .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }

            /* Native placeholder text should read as a hint, not real content — some
               dark-theme Quasar inputs leave it at near-full text brightness otherwise.
               currentColor (not a hardcoded white) is what makes this theme-aware — it
               inherits whichever text color the active theme already set on the input,
               so the dimmed hint stays visible against Pure Light/system-light too
               instead of rendering as near-invisible white-on-white. */
            .loma-hint-placeholder textarea::placeholder,
            .loma-hint-placeholder input::placeholder {
                color: currentColor;
                opacity: 0.38;
            }

            html, body.loma-app, body.loma-app #q-app {
                overflow: hidden !important;
                margin: 0 !important;
                padding: 0 !important;
                width: 100% !important;
                max-width: 100% !important;
                height: 100% !important;
            }
            body.loma-app .nicegui-layout,
            body.loma-app .q-page-container,
            body.loma-app .q-page,
            body.loma-app .nicegui-content {
                padding: 0 !important;
                margin: 0 !important;
                gap: 0 !important;
                width: 100% !important;
                max-width: 100% !important;
                min-height: 100vh !important;
                min-height: 100dvh !important;
                height: 100% !important;
                overflow: hidden !important;
                box-sizing: border-box !important;
                align-items: stretch !important;
            }
            /* Edge insets live only on #loma-shell: 8px sides/top (panel gap), 4px bottom (half). */
            /* Do not force height/display on panels — breaks NiceGUI hidden/collapse. */
            #loma-shell {
                box-sizing: border-box !important;
                display: flex !important;
                flex-direction: column !important;
                flex-wrap: nowrap !important;
                flex: 1 1 auto !important;
                align-self: stretch !important;
                width: 100% !important;
                max-width: 100% !important;
                min-height: 100% !important;
                height: 100% !important;
                padding: 8px 8px 4px 8px !important;
                gap: 8px !important;
                overflow: hidden !important;
            }
            #loma-panels-row {
                flex: 1 1 0 !important;
                min-height: 0 !important;
                min-width: 0 !important;
                width: 100% !important;
                max-width: 100% !important;
                align-items: stretch !important;
                gap: 8px !important;
                overflow: hidden !important;
            }
            #loma-input-panel.hidden,
            #loma-output-panel.hidden,
            #loma-extension-panel.hidden {
                display: none !important;
            }
            #loma-input-panel,
            #loma-output-panel,
            #loma-extension-panel,
            #loma-workspace-panel {
                align-self: stretch !important;
                min-height: 0 !important;
            }
            #loma-workspace-panel {
                min-width: 0 !important;
            }

            .q-notifications__list--top {
                top: 12px !important;
                bottom: auto !important;
            }
            .q-notification {
                max-width: min(420px, 92vw);
            }

            .loma-scroll .q-scrollarea__container,
            .loma-scroll {
                scrollbar-width: thin;
                scrollbar-color: rgba(96, 165, 250, 0.35) transparent;
            }
            .loma-scroll .q-scrollarea__container {
                overflow-x: hidden !important;
            }
            .loma-scroll .q-scrollarea__content {
                width: 100% !important;
                max-width: 100% !important;
            }
            .loma-scroll .q-scrollarea__container::-webkit-scrollbar,
            .loma-scroll::-webkit-scrollbar { width: 6px; }
            .loma-scroll .q-scrollarea__container::-webkit-scrollbar-thumb,
            .loma-scroll::-webkit-scrollbar-thumb {
                background: rgba(96, 165, 250, 0.35);
                border-radius: 4px;
            }
            #loma-console-scroll,
            .loma-console-scroll {
                overflow-y: auto !important;
                overflow-x: hidden !important;
                min-height: 0 !important;
                flex: 1 1 auto !important;
                height: 0 !important;
            }
            .loma-sources-scroll {
                position: relative;
                z-index: 12;
                overflow-y: auto !important;
                overflow-x: hidden;
                scrollbar-width: thin;
                scrollbar-color: rgba(96, 165, 250, 0.45) rgba(255, 255, 255, 0.06);
            }
            .loma-sources-scroll::-webkit-scrollbar { width: 8px; }
            .loma-sources-scroll::-webkit-scrollbar-track {
                background: rgba(255, 255, 255, 0.06);
                border-radius: 4px;
            }
            .loma-sources-scroll::-webkit-scrollbar-thumb {
                background: rgba(96, 165, 250, 0.45);
                border-radius: 4px;
            }
            .loma-sources-uploader-overlay {
                pointer-events: none !important;
            }

            .hidden-uploader .q-uploader__list,
            .hidden-uploader .q-uploader__header { display: none !important; }
            .hidden-uploader {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                min-height: fit-content !important;
            }
            .hidden-uploader .q-uploader__input { display: none !important; }

            .loma-doc-upload .q-uploader__list,
            .loma-doc-upload .q-uploader__header,
            .loma-doc-upload .q-uploader__subtitle,
            .loma-doc-upload .q-linear-progress { display: none !important; }
            .loma-doc-upload .q-uploader {
                background: transparent !important;
                border: none !important;
                box-shadow: none !important;
                padding: 0 !important;
                min-height: 0 !important;
            }

            /* Document Editor — source textarea fills pane; footer stays outside */
            .loma-doc-editor-root {
                display: flex;
                flex-direction: column;
                flex: 1 1 auto;
                min-height: 0;
                height: 100%;
                overflow: hidden;
            }
            .loma-doc-editor-root .loma-doc-editor-body {
                flex: 1 1 auto;
                min-height: 0;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            .loma-doc-editor-root .loma-doc-editor-wrap {
                flex: 1 1 auto;
                min-height: 0;
                height: 100%;
                display: flex;
                flex-direction: column;
            }
            .loma-doc-editor-root .loma-doc-editor-field,
            .loma-doc-editor-root .loma-doc-editor-field .q-field,
            .loma-doc-editor-root .loma-doc-editor-field .q-field__inner,
            .loma-doc-editor-root .loma-doc-editor-field .q-field__control,
            .loma-doc-editor-root .loma-doc-editor-field .q-field__control-container {
                flex: 1 1 auto !important;
                min-height: 0 !important;
                height: 100% !important;
                max-height: 100% !important;
                display: flex !important;
                flex-direction: column !important;
            }
            .loma-doc-editor-root .loma-doc-editor-field .q-field--borderless .q-field__control:before,
            .loma-doc-editor-root .loma-doc-editor-field .q-field--borderless .q-field__control:after {
                border: none !important;
            }
            .loma-doc-editor-root .loma-doc-editor-field textarea {
                flex: 1 1 auto !important;
                min-height: 0 !important;
                height: 100% !important;
                max-height: 100% !important;
                resize: none !important;
                overflow-y: auto !important;
            }
            .loma-doc-editor-root.loma-doc-source-outer-scroll {
                overflow-y: auto !important;
                overflow-x: hidden !important;
            }
            .loma-doc-editor-root.loma-doc-source-outer-scroll .loma-doc-editor-body,
            .loma-doc-editor-root.loma-doc-source-outer-scroll .loma-doc-editor-wrap {
                overflow: visible !important;
                flex: 0 0 auto !important;
                height: auto !important;
                min-height: 0 !important;
            }
            .loma-doc-editor-root.loma-doc-source-outer-scroll .loma-markdown-source {
                overflow: visible !important;
            }

            .loma-preview-root {
                display: flex;
                flex-direction: column;
                height: 100%;
                min-height: 0;
            }
            .loma-preview-root .loma-preview-content-column {
                flex: 1 1 auto;
                min-height: 0;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            .loma-preview-root .loma-preview-editor-wrap {
                flex: 1 1 auto;
                min-height: 0;
                overflow: hidden;
                display: flex;
                flex-direction: column;
            }
            .loma-preview-root .loma-preview-scroll-wrap {
                flex: 1 1 auto;
                min-height: 0;
                max-height: 100%;
                height: 100%;
                min-width: 0;
                width: 100%;
            }
            .loma-preview-root .loma-preview-scroll-wrap .q-scrollarea__container {
                max-height: 100%;
            }
            .loma-preview-root .loma-preview-pane-body {
                width: 100%;
                min-width: 0;
            }
            .loma-preview-root .loma-preview-editor .q-field__control,
            .loma-preview-root .loma-preview-editor .q-field__control-container,
            .loma-preview-root .loma-preview-editor .q-field__inner {
                background: transparent !important;
                box-shadow: none !important;
                min-height: 0 !important;
                height: auto !important;
            }
            .loma-preview-scroll-source.loma-preview-source-pane {
                display: flex;
                flex-direction: column;
                height: 100%;
                min-height: 0;
            }
            .loma-preview-scroll-source .loma-preview-pane-body {
                display: flex;
                flex-direction: column;
                flex: 1 1 auto;
                min-height: 0;
                height: 100%;
            }
            .loma-preview-scroll-source .loma-preview-source-field,
            .loma-preview-scroll-source .loma-preview-source-field .q-field,
            .loma-preview-scroll-source .loma-preview-source-field .q-field__inner,
            .loma-preview-scroll-source .loma-preview-source-field .q-field__control,
            .loma-preview-scroll-source .loma-preview-source-field .q-field__control-container {
                flex: 1 1 auto !important;
                min-height: 0 !important;
                height: 100% !important;
                max-height: 100% !important;
                display: flex !important;
                flex-direction: column !important;
            }
            .loma-preview-scroll-source .loma-preview-source-field textarea {
                flex: 1 1 auto !important;
                min-height: 0 !important;
                height: 100% !important;
                max-height: 100% !important;
                overflow-y: auto !important;
                resize: none !important;
            }
            .loma-preview-scroll-source .loma-markdown-source:not(textarea) {
                overflow-y: auto !important;
                resize: none !important;
                min-height: 2rem;
                max-height: 100%;
            }
            .loma-preview-root .loma-preview-editor textarea {
                overflow: visible !important;
                resize: none !important;
                min-height: 8rem;
                height: auto !important;
                max-height: none !important;
            }
            .loma-preview-root .loma-preview-hint {
                flex-shrink: 0;
                margin-top: 0.35rem;
            }

            /* Preview Source — outer border; text flush top-left inside */
            .loma-preview-scroll-source {
                box-sizing: border-box;
            }
            .loma-preview-scroll-source .q-scrollarea__content {
                padding: 0 !important;
            }
            .loma-preview-scroll-source .loma-preview-pane-body {
                box-sizing: border-box;
            }
            .loma-preview-scroll-source .loma-preview-editor .q-field__control,
            .loma-preview-scroll-source .loma-preview-editor .q-field__control-container,
            .loma-preview-scroll-source .loma-preview-editor .q-field__inner {
                padding: 0 !important;
            }
            .loma-preview-scroll-source .loma-preview-editor textarea {
                padding: 0 !important;
                min-height: 2rem;
                color: #d1d5db;
            }

            /* Preview Viewer — white page fills scroll area, minimal top gap */
            .loma-preview-scroll-viewer .q-scrollarea__container,
            .loma-preview-scroll-viewer .q-scrollarea__content,
            .loma-preview-scroll-viewer .loma-preview-pane-body-viewer {
                background: #faf8f5;
                min-height: 100%;
            }
            .loma-preview-scroll-viewer .loma-markup-host,
            .loma-preview-scroll-viewer .loma-markup-root {
                background: #faf8f5;
                min-height: 100%;
            }
            .loma-preview-scroll-viewer .loma-markup-canvas {
                align-items: stretch;
                padding: 0.75rem 0 1.5rem;
                gap: 1.5rem;
                min-height: min-content;
            }
            .loma-preview-scroll-viewer .loma-slide-deck {
                padding: 0 0.75rem 1rem;
            }
            .loma-preview-scroll-viewer .loma-paper-sheet {
                width: 100%;
                max-width: 100%;
                min-height: 100%;
                margin: 0;
                padding: 0.65rem 0.85rem 1rem;
                border: none;
                border-radius: 0;
                box-shadow: none;
            }
            .loma-preview-scroll-viewer .loma-paper-inner > :first-child {
                margin-top: 0 !important;
            }
            body.loma-theme-light .loma-preview-scroll-viewer .q-scrollarea__container,
            body.loma-theme-light .loma-preview-scroll-viewer .loma-paper-sheet {
                background: #ffffff;
            }
            body.loma-theme-light .loma-preview-scroll-viewer .loma-markup-host,
            body.loma-theme-light .loma-preview-scroll-viewer .loma-markup-root {
                background: #ffffff;
            }

            #loma-input-panel,
            #loma-extension-panel,
            #loma-workspace-panel,
            #loma-output-panel {
                padding-top: 1.25rem;
            }

            .loma-chat-user,
            .loma-chat-assistant {
                border-radius: 1rem !important;
                overflow: hidden;
            }
            .loma-chat-user .q-markdown,
            .loma-chat-assistant .q-markdown,
            .loma-chat-user .q-markdown p,
            .loma-chat-assistant .q-markdown p {
                margin: 0;
            }
            #loma-chat-scroll .loma-chat-user .q-markdown,
            #loma-chat-scroll .loma-chat-assistant .q-markdown,
            #loma-chat-scroll .loma-chat-user .q-markdown p,
            #loma-chat-scroll .loma-chat-assistant .q-markdown p,
            #loma-chat-scroll .loma-chat-user .q-markdown li,
            #loma-chat-scroll .loma-chat-assistant .q-markdown li {
                font-size: var(--loma-chat-font-size, 13px) !important;
                line-height: 1.55;
            }
            a.loma-os-open {
                color: #38bdf8;
                text-decoration: underline;
                cursor: pointer;
            }
            mark.loma-search-hit {
                background: rgba(16, 185, 129, 0.35);
                color: inherit;
                padding: 0 2px;
                border-radius: 2px;
            }
            a.loma-os-open:hover {
                color: #7dd3fc;
            }

            /* Settings dialog — scrollable tab panels */
            .q-dialog .q-tab-panels {
                scrollbar-width: thin;
            }

            /* Pure Light — readable Quasar fields (continuous standout edge; no split boxes) */
            body.loma-theme-light .q-field__label,
            body.loma-theme-light .q-field__marginal,
            body.loma-theme-light .q-field__native,
            body.loma-theme-light .q-field__input,
            body.loma-theme-light .q-select__dropdown-icon {
                color: #0f172a !important;
            }
            /* Paint only the outer control — painting control-container/append splits the chevron. */
            body.loma-theme-light .q-field--standout .q-field__control {
                background: #ffffff !important;
                box-shadow: inset 0 0 0 1px #94a3b8 !important;
            }
            body.loma-theme-light .q-field--standout .q-field__control-container,
            body.loma-theme-light .q-field--standout .q-field__native,
            body.loma-theme-light .q-field--standout .q-field__append {
                background: transparent !important;
                color: #0f172a !important;
            }
            body.loma-theme-light .q-field--outlined .q-field__control,
            body.loma-theme-light .q-field--outlined .q-field__control-container {
                background: transparent !important;
            }
            body.loma-theme-light .q-field--outlined .q-field__control:before {
                border-color: #94a3b8 !important;
            }
            body.loma-theme-light .q-menu {
                background: #ffffff !important;
                color: #0f172a !important;
                border: 1px solid #cbd5e1 !important;
            }
            body.loma-theme-light .q-card {
                background: #ffffff !important;
                color: #0f172a !important;
            }
            body.loma-theme-light .q-item,
            body.loma-theme-light .q-item__label,
            body.loma-theme-light .q-expansion-item__container,
            body.loma-theme-light .q-expansion-item .q-item__label {
                color: #0f172a !important;
            }
            body.loma-theme-light .q-tab {
                color: #475569 !important;
            }
            body.loma-theme-light .q-tab--active {
                color: #1d4ed8 !important;
            }
            body.loma-theme-light .q-separator {
                background: #cbd5e1 !important;
            }
            /* Pure Light — settings dialog tab panels were rendering on the dark base;
               make them transparent so the white settings card shows through (all 4 tabs). */
            body.loma-theme-light .q-tab-panels,
            body.loma-theme-light .q-tab-panel {
                background: transparent !important;
                color: #0f172a !important;
            }
            /* Remap dark-theme Tailwind leftovers (border-white/*, text-gray-*, bg-black/*) */
            body.loma-theme-light [class*="border-white"] {
                border-color: rgba(15, 23, 42, 0.18) !important;
            }
            body.loma-theme-light [class*="bg-white/"] {
                background-color: rgba(15, 23, 42, 0.05) !important;
            }
            body.loma-theme-light [class*="bg-black/"] {
                background-color: rgba(15, 23, 42, 0.06) !important;
            }
            body.loma-theme-light .text-gray-200,
            body.loma-theme-light .text-gray-300,
            body.loma-theme-light .text-gray-400,
            body.loma-theme-light .text-gray-500,
            body.loma-theme-light .text-grey-3,
            body.loma-theme-light .text-grey-4,
            body.loma-theme-light .text-grey-5,
            body.loma-theme-light .text-grey-6 {
                color: #475569 !important;
            }
            body.loma-theme-light .text-gray-600,
            body.loma-theme-light .text-grey-7 {
                color: #334155 !important;
            }
            body.loma-theme-light a.loma-os-open {
                color: #0369a1 !important;
            }
            body.loma-theme-light a.loma-os-open:hover {
                color: #0c4a6e !important;
            }
            body.loma-theme-light .loma-chat-assistant,
            body.loma-theme-light .loma-chat-assistant p,
            body.loma-theme-light .loma-chat-assistant li {
                color: #0f172a !important;
            }

            /* Terminal Green — Quasar + leftover dark-theme utilities */
            body.loma-theme-mainframe .q-field__label,
            body.loma-theme-mainframe .q-field__marginal,
            body.loma-theme-mainframe .q-field__native,
            body.loma-theme-mainframe .q-field__input,
            body.loma-theme-mainframe .q-select__dropdown-icon {
                color: #a7f3d0 !important;
            }
            body.loma-theme-mainframe .q-field--standout .q-field__control {
                background: #041404 !important;
                box-shadow: inset 0 0 0 1px rgba(52, 211, 153, 0.45) !important;
            }
            body.loma-theme-mainframe .q-field--standout .q-field__control-container,
            body.loma-theme-mainframe .q-field--standout .q-field__native,
            body.loma-theme-mainframe .q-field--standout .q-field__append {
                background: transparent !important;
                color: #a7f3d0 !important;
            }
            body.loma-theme-mainframe .q-menu {
                background: #041404 !important;
                color: #a7f3d0 !important;
                border: 1px solid rgba(52, 211, 153, 0.42) !important;
            }
            body.loma-theme-mainframe .q-card {
                background: #041404 !important;
                color: #a7f3d0 !important;
            }
            body.loma-theme-mainframe .q-item,
            body.loma-theme-mainframe .q-item__label,
            body.loma-theme-mainframe .q-expansion-item .q-item__label {
                color: #a7f3d0 !important;
            }
            body.loma-theme-mainframe .q-tab {
                color: #34d399 !important;
            }
            body.loma-theme-mainframe .q-tab--active {
                color: #a7f3d0 !important;
            }
            body.loma-theme-mainframe .q-tab-panels,
            body.loma-theme-mainframe .q-tab-panel {
                background: transparent !important;
                color: #a7f3d0 !important;
            }
            body.loma-theme-mainframe .q-separator {
                background: rgba(52, 211, 153, 0.4) !important;
            }
            body.loma-theme-mainframe .loma-ext-library .q-card {
                background: #041404 !important;
            }
            body.loma-theme-mainframe [class*="border-white"] {
                border-color: rgba(52, 211, 153, 0.4) !important;
            }
            body.loma-theme-mainframe [class*="bg-white/"] {
                background-color: rgba(16, 185, 129, 0.1) !important;
            }
            body.loma-theme-mainframe [class*="bg-black/"] {
                background-color: rgba(16, 185, 129, 0.12) !important;
            }
            body.loma-theme-mainframe .text-gray-200,
            body.loma-theme-mainframe .text-gray-300,
            body.loma-theme-mainframe .text-gray-400,
            body.loma-theme-mainframe .text-gray-500,
            body.loma-theme-mainframe .text-gray-600,
            body.loma-theme-mainframe .text-grey-3,
            body.loma-theme-mainframe .text-grey-4,
            body.loma-theme-mainframe .text-grey-5,
            body.loma-theme-mainframe .text-grey-6,
            body.loma-theme-mainframe .text-grey-7 {
                color: #6ee7b7 !important;
            }
            body.loma-theme-mainframe a.loma-os-open {
                color: #6ee7b7 !important;
            }
            body.loma-theme-mainframe a.loma-os-open:hover {
                color: #bbf7d0 !important;
            }
            body.loma-theme-mainframe .loma-chat-assistant,
            body.loma-theme-mainframe .loma-chat-assistant p,
            body.loma-theme-mainframe .loma-chat-assistant li {
                color: #a7f3d0 !important;
            }

            /* Terminal Green — assistant bubbles vs workspace */
            body.loma-theme-mainframe #loma-workspace-panel .q-scrollarea__content {
                background: transparent;
            }

            /* Interactive markup — isolated from Quasar/Tailwind inheritance */
            .loma-markup-host {
                display: block !important;
                width: 100% !important;
                max-width: 100% !important;
                min-width: 0 !important;
                overflow-x: hidden !important;
            }
            .loma-markup-root {
                display: block;
                width: 100%;
                max-width: 100%;
                min-width: 0;
                overflow-x: hidden;
                color: #e2e8f0;
                font-size: 14px;
                line-height: 1.65;
                text-rendering: optimizeLegibility;
                -webkit-font-smoothing: antialiased;
            }
            .loma-markup-root *,
            .loma-markup-root *::before,
            .loma-markup-root *::after {
                box-sizing: border-box;
            }
            .loma-markup-canvas {
                display: flex;
                flex-direction: column;
                align-items: center;
                width: 100%;
                max-width: 100%;
                min-width: 0;
                padding: 0.75rem 0 1.5rem;
                gap: 1.5rem;
            }
            .loma-markup-root p,
            .loma-markup-root li,
            .loma-markup-root td,
            .loma-markup-root th,
            .loma-markup-root h1,
            .loma-markup-root h2,
            .loma-markup-root h3,
            .loma-markup-root h4,
            .loma-markup-root span {
                overflow-wrap: anywhere;
                word-break: break-word;
                hyphens: auto;
            }
            .loma-markup-root img {
                height: auto !important;
                max-width: 100% !important;
            }
            .loma-markup-root pre,
            .loma-markup-root code {
                white-space: pre-wrap !important;
                overflow-wrap: anywhere;
                word-break: break-word;
                max-width: 100%;
            }
            .loma-markup-root pre {
                overflow-x: auto;
                padding: 0.75rem;
                border-radius: 6px;
                background: rgba(0, 0, 0, 0.25);
            }
            .loma-markup-root table:not(.loma-grid-table) {
                table-layout: fixed !important;
                width: 100% !important;
                max-width: 100% !important;
                border-collapse: collapse;
                margin: 0.75em 0;
            }
            .loma-markup-root table:not(.loma-grid-table) th,
            .loma-markup-root table:not(.loma-grid-table) td {
                overflow-wrap: anywhere;
                word-break: break-word;
                vertical-align: top;
            }

            /* A4 paper mockups (.docx, .pdf) */
            .loma-markup-root .loma-paper-sheet {
                width: min(100%, 680px);
                max-width: 680px;
                min-height: 880px;
                margin: 0 auto;
                padding: 56px 48px 64px;
                background: #faf8f5;
                color: #1a1a1a;
                border: 1px solid #d4cfc7;
                border-radius: 2px;
                box-shadow:
                    0 1px 0 rgba(255, 255, 255, 0.06) inset,
                    0 12px 40px rgba(0, 0, 0, 0.35),
                    0 2px 8px rgba(0, 0, 0, 0.2);
                font-family: "Palatino Linotype", Palatino, Georgia, "Times New Roman", serif;
                font-size: var(--loma-view-font-size, 11.5pt);
                line-height: 1.65;
                position: relative;
            }
            .loma-markup-root .loma-paper-sheet + .loma-paper-sheet {
                margin-top: 0.5rem;
            }
            .loma-markup-root .loma-paper-inner {
                width: 100%;
                min-width: 0;
            }
            .loma-markup-root .loma-paper-inner p {
                margin: 0 0 0.85em;
                text-align: justify;
                text-justify: inter-word;
            }
            .loma-markup-root .loma-paper-inner h1,
            .loma-markup-root .loma-paper-inner h2,
            .loma-markup-root .loma-paper-inner h3 {
                font-family: "Segoe UI", system-ui, sans-serif;
                font-weight: 700;
                color: #0f172a;
                margin: 1.25em 0 0.5em;
                text-align: left;
            }
            .loma-markup-root .loma-paper-inner h1 { font-size: 1.75rem; }
            .loma-markup-root .loma-paper-inner h2 { font-size: 1.4rem; }
            .loma-markup-root .loma-paper-inner h3 { font-size: 1.2rem; }
            .loma-markup-root .loma-paper-inner h4 { font-size: 1.08rem; font-weight: 600; }
            .loma-markup-root .loma-paper-inner h5 { font-size: 1rem; font-weight: 600; }
            .loma-markup-root .loma-paper-inner h6 { font-size: 0.95rem; font-weight: 600; }
            .loma-markup-root .loma-paper-inner strong,
            .loma-markup-root .loma-paper-inner b {
                font-weight: 700;
                color: #0f172a;
            }
            .loma-markup-root .loma-paper-inner em,
            .loma-markup-root .loma-paper-inner i {
                font-style: italic;
            }
            .loma-markup-root .loma-paper-inner ul,
            .loma-markup-root .loma-paper-inner ol {
                margin: 0 0 0.85em 1.25em;
                padding-left: 0.5em;
            }
            .loma-markup-root .loma-paper-inner li {
                margin-bottom: 0.35em;
            }
            .loma-markup-root .loma-paper-inner blockquote {
                margin: 0.75em 0;
                padding-left: 1em;
                border-left: 3px solid #cbd5e1;
                color: #334155;
            }
            .loma-markup-root .loma-paper-inner .loma-image-placeholder {
                display: block;
                margin: 1em 0;
                padding: 0.75em 1em;
                border: 1px dashed #94a3b8;
                border-radius: 6px;
                background: #f1f5f9;
                color: #475569;
                font-style: italic;
                font-size: 0.95em;
            }
            .loma-markup-root .loma-page-number {
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 9px;
                font-weight: 600;
                color: #64748b;
                text-align: center;
                margin-top: 2rem;
                padding-top: 0.75rem;
                border-top: 1px solid #e2e8f0;
                letter-spacing: 0.12em;
                text-transform: uppercase;
            }
            .loma-markup-root .loma-paper-sheet .loma-page-number {
                position: absolute;
                bottom: 28px;
                left: 48px;
                right: 48px;
                margin-top: 0;
            }

            /* Slide decks (.pptx preview + markdown draft) */
            @keyframes loma-progress-bulb-pulse {
                0%, 100% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.45; transform: scale(1.12); }
            }
            .loma-progress-bulb-active {
                display: inline-block;
                animation: loma-progress-bulb-pulse 1.15s ease-in-out infinite;
            }

            .loma-markup-root .loma-slide-deck {
                display: flex;
                flex-direction: column;
                align-items: center;
                width: 100%;
                max-width: 100%;
                min-width: 0;
                gap: 1.25rem;
            }
            .loma-markup-root .loma-slide-deck-pptx .loma-slide-frame-pptx {
                width: min(100%, 640px);
                max-width: 640px;
                aspect-ratio: var(--slide-aspect, 4 / 3);
                margin: 0 auto;
                padding: 0;
                border: 1px solid rgba(15, 23, 42, 0.1);
                border-radius: 6px;
                box-shadow: 0 6px 22px rgba(15, 23, 42, 0.14);
                box-sizing: border-box;
                position: relative;
                overflow: hidden;
                container-type: size;
                background: transparent;
            }
            .loma-markup-root .loma-slide-deck-pptx .loma-slide-inner {
                position: absolute;
                inset: 0;
                width: 100%;
                height: 100%;
                min-width: 0;
                overflow: hidden;
            }
            .loma-markup-root .loma-slide-deck-pptx .loma-slide-shape {
                position: absolute;
                box-sizing: border-box;
                overflow: hidden;
                line-height: 1.3;
            }
            .loma-markup-root .loma-slide-deck-pptx .loma-slide-decor {
                position: absolute;
                box-sizing: border-box;
                pointer-events: none;
            }
            .loma-markup-root .loma-slide-deck-pptx .loma-slide-shape p {
                margin: 0 0 0.2em;
            }
            .loma-markup-root .loma-slide-deck-draft .loma-slide-frame-draft {
                width: min(100%, 640px);
                max-width: 640px;
                aspect-ratio: 4 / 3;
                margin: 0 auto;
                padding: clamp(0.85rem, 2.5cqw, 1.35rem) clamp(1rem, 3cqw, 1.6rem);
                background: #fffaf5;
                border: 1px solid rgba(15, 23, 42, 0.1);
                border-radius: 6px;
                box-shadow: 0 6px 22px rgba(15, 23, 42, 0.14);
                box-sizing: border-box;
                position: relative;
                overflow: hidden;
                container-type: size;
            }
            .loma-markup-root .loma-slide-deck-draft .loma-slide-inner {
                position: relative;
                width: 100%;
                height: 100%;
                min-width: 0;
                overflow: hidden;
                display: flex;
                flex-direction: column;
                gap: 0.35rem;
            }
            .loma-markup-root .loma-slide-title {
                font-family: "Segoe UI", system-ui, sans-serif;
                font-weight: 800;
                color: #3e2723;
                margin: 0;
                line-height: 1.2;
                letter-spacing: -0.02em;
            }
            .loma-markup-root .loma-slide-deck-draft .loma-slide-title {
                font-size: clamp(1rem, 5.5cqh, 1.65rem);
            }
            .loma-markup-root .loma-slide-body,
            .loma-markup-root .loma-slide-bullet {
                font-family: "Segoe UI", system-ui, sans-serif;
                color: #4e342e;
                margin: 0;
                line-height: 1.35;
            }
            .loma-markup-root .loma-slide-inner span {
                font-family: inherit;
            }
            .loma-markup-root .loma-slide-bullets {
                margin: 0;
                padding-left: 1.1rem;
                color: #4e342e;
                line-height: 1.4;
            }
            .loma-markup-root .loma-slide-deck-draft .loma-slide-bullets {
                font-size: clamp(0.72rem, 2.9cqh, 0.95rem);
                flex: 1 1 auto;
                overflow: hidden;
            }
            .loma-markup-root .loma-slide-bullets li {
                margin-bottom: 0.35em;
            }
            .loma-ext-tier-free { color: #9ca3af !important; font-size: 9px !important; text-transform: uppercase; }
            .loma-ext-tier-freemium { color: #fbbf24 !important; font-size: 9px !important; text-transform: uppercase; }
            .loma-ext-tier-premium { color: #eab308 !important; font-size: 9px !important; text-transform: uppercase; }
            .loma-markup-root .loma-slide-bullets strong,
            .loma-markup-root .loma-slide-bullets b {
                font-weight: 700;
            }
            .loma-markup-root .loma-slide-bullets em,
            .loma-markup-root .loma-slide-bullets i {
                font-style: italic;
            }

            /* Spreadsheet grids (.xlsx) */
            .loma-markup-root .loma-sheet-grid {
                width: min(100%, 100%);
                max-width: 100%;
                min-width: 0;
                overflow-x: auto;
                overflow-y: visible;
                padding: 0.25rem 0;
            }
            .loma-markup-root .loma-grid-table {
                table-layout: auto;
                width: max-content;
                min-width: 100%;
                max-width: none !important;
                border-collapse: collapse;
                font-family: "Segoe UI", system-ui, sans-serif;
                font-size: 13px;
                border: 2px solid #475569;
            }
            .loma-markup-root .loma-grid-table th,
            .loma-markup-root .loma-grid-table td {
                border: 1px solid #64748b;
                padding: 0.45em 0.75em;
                text-align: left;
                min-width: 4rem;
                max-width: 20rem;
                overflow-wrap: anywhere;
                word-break: break-word;
            }
            .loma-markup-root .loma-grid-table tr:first-child th,
            .loma-markup-root .loma-grid-table tr:first-child td {
                background: #1e3a5f;
                color: #f1f5f9;
                font-weight: 700;
            }
            .loma-markup-root .loma-grid-table tr:nth-child(even) td {
                background: rgba(30, 41, 59, 0.55);
            }
            .loma-markup-root .loma-grid-table tr:nth-child(odd) td {
                background: rgba(15, 23, 42, 0.35);
            }

            .loma-markup-root .loma-markup-plain {
                width: min(100%, 680px);
                margin: 0 auto;
                padding: 1rem;
            }

            /* Dark theme — paper stays warm off-white for print fidelity; chrome adapts */
            body.loma-theme-dark .loma-markup-root .loma-paper-sheet,
            body:not(.loma-theme-light):not(.loma-theme-mainframe) .loma-markup-root .loma-paper-sheet {
                background: #faf8f5;
                color: #1a1a1a;
            }

            /* Light theme */
            body.loma-theme-light .loma-markup-root {
                color: #1e293b;
            }
            body.loma-theme-light .loma-markup-root .loma-paper-sheet {
                background: #ffffff;
                border-color: #cbd5e1;
                box-shadow: 0 4px 24px rgba(15, 23, 42, 0.1);
            }
            body.loma-theme-light .loma-markup-root .loma-slide-deck-draft .loma-slide-frame-draft {
                background: #fffaf5;
                border-color: rgba(15, 23, 42, 0.12);
            }
            body.loma-theme-light .loma-markup-root .loma-slide-deck-draft .loma-slide-title { color: #3e2723; }
            body.loma-theme-light .loma-markup-root .loma-slide-deck-draft .loma-slide-bullets { color: #4e342e; }
            body.loma-theme-light .loma-markup-root .loma-grid-table tr:first-child th,
            body.loma-theme-light .loma-markup-root .loma-grid-table tr:first-child td {
                background: #1e40af;
            }
            body.loma-theme-light .loma-markup-root .loma-grid-table tr:nth-child(even) td {
                background: #f1f5f9;
                color: #0f172a;
            }
            body.loma-theme-light .loma-markup-root .loma-grid-table tr:nth-child(odd) td {
                background: #ffffff;
                color: #0f172a;
            }

            /* Mainframe / terminal green theme */
            body.loma-theme-mainframe .loma-markup-root {
                color: #a7f3d0;
            }
            body.loma-theme-mainframe .loma-markup-root .loma-paper-sheet {
                background: #0a120a;
                color: #d1fae5;
                border-color: rgba(52, 211, 153, 0.45);
                box-shadow: 0 12px 40px rgba(0, 0, 0, 0.6);
            }
            body.loma-theme-mainframe .loma-markup-root .loma-paper-inner h1,
            body.loma-theme-mainframe .loma-markup-root .loma-paper-inner h2,
            body.loma-theme-mainframe .loma-markup-root .loma-paper-inner h3 {
                color: #6ee7b7;
            }
            body.loma-theme-mainframe .loma-markup-root .loma-page-number {
                color: #34d399;
                border-top-color: rgba(52, 211, 153, 0.35);
            }
            body.loma-theme-mainframe .loma-markup-root .loma-slide-deck-draft .loma-slide-frame-draft {
                background: #0a120a;
                border-color: rgba(52, 211, 153, 0.45);
            }
            body.loma-theme-mainframe .loma-markup-root .loma-slide-deck-draft .loma-slide-title { color: #a7f3d0; }
            body.loma-theme-mainframe .loma-markup-root .loma-slide-deck-draft .loma-slide-bullets { color: #d1fae5; }
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table {
                border-color: #34d399;
            }
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table th,
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table td {
                border-color: rgba(52, 211, 153, 0.4);
            }
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table tr:first-child th,
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table tr:first-child td {
                background: #14532d;
                color: #a7f3d0;
            }
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table tr:nth-child(even) td {
                background: rgba(20, 83, 45, 0.4);
                color: #d1fae5;
            }
            body.loma-theme-mainframe .loma-markup-root .loma-grid-table tr:nth-child(odd) td {
                background: rgba(5, 46, 22, 0.55);
                color: #d1fae5;
            }

            /* CJK glyphs read visually smaller than Latin text at the same declared
               font-size, so bump the base size slightly when the active UI language
               is Traditional/Simplified Chinese. */
            body.loma-lang-zh {
                font-size: 1.06em;
            }

            /* LOMA brand icon — click-to-spark glow */
            @keyframes loma-icon-glow-pulse {
                0%   { filter: drop-shadow(0 0 2px rgba(96, 165, 250, 0.55)); transform: scale(1); }
                50%  { filter: drop-shadow(0 0 12px rgba(96, 165, 250, 0.95))
                               drop-shadow(0 0 20px rgba(56, 189, 248, 0.7)); transform: scale(1.1); }
                100% { filter: drop-shadow(0 0 2px rgba(96, 165, 250, 0.55)); transform: scale(1); }
            }
            .loma-icon-glow { animation: loma-icon-glow-pulse 0.85s ease-in-out infinite; }
        </style>
        <script>
        document.addEventListener('DOMContentLoaded', function() {
            if (typeof Quasar !== 'undefined' && Quasar.Notify && Quasar.Notify.setDefaults) {
                Quasar.Notify.setDefaults({ position: 'top', timeout: 3000 });
            }
            document.addEventListener('click', function(e) {
                const a = e.target.closest('a');
                if (!a) return;
                const href = a.getAttribute('href') || '';
                let path = a.getAttribute('data-path') || '';
                if (!path && href.startsWith('loma-open:')) {
                    path = decodeURIComponent(href.slice('loma-open:'.length));
                } else if (!a.classList.contains('loma-os-open')) {
                    return;
                }
                if (!path) return;
                e.preventDefault();
                e.stopPropagation();
                fetch('/loma/open-path', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: path })
                }).catch(function() {});
            }, true);
        });
        </script>
    """)


def inject_splitter_resizable_script() -> None:
    ui.run_javascript("""
        function lomaFindEl(id) {
            return document.getElementById(id)
                || document.querySelector('[id="' + id + '"]');
        }

        function wirePanelSplitter(handleId, panelId, side, minW, maxW) {
            const handle = lomaFindEl(handleId);
            const panel = lomaFindEl(panelId);
            if (!handle || !panel || handle.dataset.lomaWired === '1') return;
            handle.dataset.lomaWired = '1';
            let dragging = false;
            let startX = 0;
            let startWidth = 0;
            handle.addEventListener('mousedown', function(e) {
                e.preventDefault();
                dragging = true;
                startX = e.clientX;
                startWidth = panel.offsetWidth;
                document.body.style.cursor = 'col-resize';
                document.body.style.userSelect = 'none';
            });
            document.addEventListener('mousemove', function(e) {
                if (!dragging) return;
                const offset = e.clientX - startX;
                const calculatedWidth = side === 'left'
                    ? startWidth + offset
                    : startWidth - offset;
                if (calculatedWidth >= minW && calculatedWidth <= maxW) {
                    panel.style.width = calculatedWidth + 'px';
                    panel.style.flexShrink = '0';
                }
            });
            document.addEventListener('mouseup', function() {
                if (!dragging) return;
                dragging = false;
                panel.dataset.userResized = '1';
                document.body.style.cursor = '';
                document.body.style.userSelect = '';
            });
        }

        function syncExtensionDefaultWidth() {
            const ext = lomaFindEl('loma-extension-panel');
            const centerRow = ext ? ext.parentElement : null;
            const splitter = lomaFindEl('loma-ext-ws-splitter');
            if (!ext || !centerRow || ext.dataset.userResized === '1') return;
            requestAnimationFrame(function() {
                const splitterW = splitter ? splitter.offsetWidth : 8;
                const total = centerRow.offsetWidth;
                const half = Math.max(220, Math.min(720, Math.floor((total - splitterW) / 2)));
                ext.style.width = half + 'px';
                ext.style.flexShrink = '0';
            });
        }
        window.lomaSyncExtensionDefaultWidth = syncExtensionDefaultWidth;

        function initLomaSplitters(attempt) {
            wirePanelSplitter('loma-ext-ws-splitter', 'loma-extension-panel', 'left', 220, 720);
            wirePanelSplitter('loma-panel-splitter', 'loma-output-panel', 'right', 180, 700);
            syncExtensionDefaultWidth();
            const extHandle = lomaFindEl('loma-ext-ws-splitter');
            const outHandle = lomaFindEl('loma-panel-splitter');
            if (attempt < 12 && (!extHandle?.dataset?.lomaWired || !outHandle?.dataset?.lomaWired)) {
                setTimeout(() => initLomaSplitters(attempt + 1), 250);
            }
        }
        initLomaSplitters(0);
    """)


def _schedule_sources_refresh(refresh_callback, sender) -> None:
    """Refresh once all in-flight uploads finish (drag drops schedule handlers in parallel)."""
    from services.session import handlers

    key = id(refresh_callback)

    async def _wait_and_refresh() -> None:
        await asyncio.sleep(0.05)
        while handlers.active_upload_count > 0:
            await asyncio.sleep(0.05)
        if len(state.active_context_files) > 5:
            state.active_context_files[:] = state.active_context_files[:5]
            ui.notify("Document list capped at 5 max.", color="warning")
        if hasattr(sender, "reset"):
            sender.reset()
        refresh_callback.refresh()

    prior = _upload_refresh_tasks.get(key)
    if prior and not prior.done():
        prior.cancel()
    _upload_refresh_tasks[key] = background_tasks.create(_wait_and_refresh(), name="sources-upload-refresh")


async def handle_file_uploaded_pipeline(e, refresh_callback) -> None:
    from services.session import handlers

    if len(state.active_context_files) >= 5:
        ui.notify("Document context overflow! Max 5 files allowed.", color="negative")
        if handlers.active_upload_count == 0 and hasattr(e.sender, "reset"):
            e.sender.reset()
        return

    await handlers.handle_file_upload(e)

    _schedule_sources_refresh(refresh_callback, e.sender)


def handle_add_link_pipeline(input_widget, menu_container, refresh_callback) -> None:
    val = input_widget.value
    if val and val.strip():
        state.add_web_link(val.strip())
        input_widget.set_value("")
        menu_container.close()
        refresh_callback.refresh()


def sync_profile_selector(force_select_id=None) -> None:
    from pipeline.base import profile_pack as profile_manager

    if registry.profile_select:
        options_dict = profile_manager.profile_select_options()
        registry.profile_select.options = options_dict
        resolved = profile_manager.resolve_active_profile_id(
            force_select_id or registry.profile_select.value or state.current_settings.get("active_profile")
        )
        if resolved not in options_dict:
            resolved = profile_manager.PROFILE_NONE
        registry.profile_select.value = resolved
        state.current_settings["active_profile"] = resolved
        registry.profile_select.update()
