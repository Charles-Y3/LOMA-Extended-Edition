# -*- coding: utf-8 -*-
"""Shared Ludicity extension panel chrome."""

from __future__ import annotations

INTRO_CLS = "text-[12px] text-gray-400 shrink-0 leading-relaxed pt-1 whitespace-pre-line"
PANEL_CLS = "w-full flex-1 min-h-0 flex flex-col gap-2 min-w-0 overflow-hidden"
BODY_CLS = "w-full flex-1 min-h-0 flex flex-col overflow-hidden gap-2"
SCROLL_CLS = "w-full flex-1 loma-scroll min-h-0 overflow-y-auto overflow-x-hidden pr-1"
FOOTER_CLS = "w-full shrink-0 flex-nowrap justify-end gap-2 pt-2 mt-auto border-t border-white/10"

# Canonical props for the action buttons in the bottom-right panel footer.
# Reference: the Arena extension. All extensions must match this size + font —
# `flat dense no-caps` with NO `size=` and NO text-size class override.
# Append ` color=<name>` per button as needed.
FOOTER_BTN_PROPS = "flat dense no-caps"
