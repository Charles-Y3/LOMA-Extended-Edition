# -*- coding: utf-8 -*-
"""
Theme tokens — semantic colors for Pure Light, Deep Obsidian, Terminal Green.
Optimized to resolve Quasar dropdown contrast bugs and aesthetic consistency.
"""
from __future__ import annotations

PANEL_HEADER_ROW = "w-full flex items-start shrink-0 h-7 mb-3 pr-8"
PANEL_HEADER_ROW_WORKSPACE = "w-full flex items-start justify-between shrink-0 h-7 mb-3"
PANEL_HEADING = "text-[11px] font-bold tracking-widest leading-none"
# Top LOMA bar (~40px content → min 60px = +50% vertical room)
TOPBAR_ROW = "w-full grid grid-cols-3 items-center shrink-0 min-h-[3.75rem] py-1.5"

THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#050505",
        "sidebar": "rgba(20, 20, 25, 0.7)",
        "border": "rgba(255, 255, 255, 0.08)",
        "text": "text-slate-200",
        "card": "bg-[#1a1a1e]",
        "emboss_bg": "bg-[#1a1a1e]",
        "emboss_shadow": "inset 0 1px 4px rgba(0,0,0,0.4), inset 0 -1px 0 rgba(255,255,255,0.05)",
        "panel_shadow": "0 8px 24px rgba(0,0,0,0.35)",
        "body_class": "loma-theme-dark",
        "chat_user": "bg-blue-600 text-white border border-blue-500/30 shadow-md rounded-2xl px-4 py-3",
        "chat_assistant": "bg-[#1a1a1e] text-slate-200 border border-white/10 shadow-sm rounded-2xl px-4 py-3",
        "input_row": "bg-[#0d0d0d] border border-white/10 shadow-2xl",
        "input_text": "text-gray-300",
        "input_props": "dark borderless shadow-none",
        "select_props": "dark standout dense",
        "upload_props": "dark flat multiple",
        "progress_wrap": "bg-[#101013] border border-white/5 shadow-inner",
        "progress_divider": "border-white/5",
        "progress_label": "text-gray-500",
        "dropzone": "border-white/10 hover:border-blue-500/40 bg-white/[0.02]",
        "dropzone_text": "text-gray-500",
        "dropzone_icon": "text-gray-500",
        "muted": "text-gray-500",
        "separator": "bg-white/5",
        "menu_bg": "p-3 border border-white/10 bg-[#121216] rounded-xl",
        "menu_label": "text-gray-400",
        "console_log": "text-gray-500",
        "settings_card": "bg-[#0f0f12] border border-white/10",
        "file_card": "bg-[#1c1c22] hover:bg-[#22222a] border border-white/[0.06]",
        "file_card_text": "text-slate-100",
        "link_card": "bg-[#1a2028] hover:bg-[#1e2834] border border-cyan-500/10",
        "icon_btn": "text-gray-500",
        "icon_btn_hover": "hover:bg-white/5",
        "tab_active": "text-blue-400",
    },
    "light": {
        "bg": "#e8eef5",  # Cool slate ground so white cards/panels read clearly
        "sidebar": "rgba(255, 255, 255, 0.98)",
        # A soft shadow (panel_shadow) now does the elevation work a heavier border
        # used to — this can stay light without the panel reading as flat.
        "border": "rgba(15, 23, 42, 0.10)",
        "text": "text-slate-900",
        "card": "bg-white border border-slate-300 shadow-sm",
        "emboss_bg": "bg-slate-100",
        # Light-surface neumorphism: a soft cool-gray shadow (not black — black inset
        # shadows read as a harsh dark smudge/border on a light background) plus a
        # crisp white highlight for a gently pressed-in card, matching the dark
        # theme's card depth instead of looking flat.
        "emboss_shadow": (
            "inset 1px 1px 3px rgba(100,116,139,0.30), "
            "inset -1px -1px 0 rgba(255,255,255,0.9)"
        ),
        "panel_shadow": "0 4px 16px rgba(15,23,42,0.08)",
        "body_class": "loma-theme-light",

        # High-Contrast matching chat styles
        "chat_user": "bg-blue-600 text-white shadow-sm font-medium rounded-2xl px-4 py-3",
        "chat_assistant": "bg-white text-slate-900 border border-slate-300 shadow-sm rounded-2xl px-4 py-3",

        "input_row": "bg-white border border-slate-300 shadow-sm focus-within:ring-2 focus-within:ring-blue-500/20",
        "input_text": "text-slate-900",

        # Cleaned layout properties: borderless and shadow-none are passed without the class="..." overrides.
        # This allows the input container to stretch to 100% width and positions the button on the far right.
        "input_props": "borderless shadow-none",
        "select_props": 'standout dense color=blue-600 bg-white text-color=slate-900 label-color=slate-700 popup-content-class="bg-white text-slate-900 shadow-xl border border-slate-300"',

        "upload_props": "flat multiple color=blue-600",
        "progress_wrap": "bg-slate-100 border border-slate-300 shadow-inner",
        "progress_divider": "border-slate-300",
        "progress_label": "text-slate-600 font-medium",
        "dropzone": "border-dashed border-slate-400 hover:border-blue-500 hover:bg-blue-50/40 bg-slate-50",
        "dropzone_text": "text-slate-600 font-medium",
        "dropzone_icon": "text-slate-500",
        "muted": "text-slate-600",
        "separator": "bg-slate-300",
        "menu_bg": "p-3 border border-slate-300 bg-white rounded-xl shadow-xl",
        "menu_label": "text-slate-600",
        "console_log": "text-slate-700 font-mono text-xs",
        "settings_card": "bg-white border border-slate-300 shadow-sm",
        "file_card": "bg-slate-50 hover:bg-slate-100 border border-slate-300",
        "file_card_text": "text-slate-900 font-medium",
        "link_card": "bg-blue-50 hover:bg-blue-100/80 border border-blue-300",
        "icon_btn": "text-slate-600 hover:text-slate-900",
        "icon_btn_hover": "hover:bg-slate-200/80",
        "tab_active": "text-blue-700 font-bold border-b-2 border-blue-600",
    },
    "mainframe": {
        "bg": "#010401",  # Pitch black void terminal base
        "sidebar": "rgba(2, 12, 4, 0.96)",
        "border": "rgba(52, 211, 153, 0.42)",  # Brighter CRT edges (was ~0.25)
        "text": "text-[#6ee7b7]",  # Brighter body text for WCAG-ish contrast on black
        "card": "bg-[#031003] border border-[#34d399]/45",
        "emboss_bg": "bg-[#031003]",
        # Same dark inset depth as the "dark" theme, but with a green CRT highlight
        # and ambient glow instead of a plain white sliver — matches the glow already
        # used on input_row/dropzone elsewhere in this theme instead of reading flat.
        "emboss_shadow": (
            "inset 0 1px 4px rgba(0,0,0,0.5), "
            "inset 0 -1px 0 rgba(52,211,153,0.18), "
            "0 0 14px rgba(16,185,129,0.10)"
        ),
        "panel_shadow": "0 0 20px rgba(16,185,129,0.06)",
        "body_class": "loma-theme-mainframe",

        # Pure Terminal Console Output theme matching
        "chat_user": "bg-[#10b981] text-[#010401] border border-[#34d399] font-mono font-bold uppercase tracking-wide rounded-2xl px-4 py-3",
        "chat_assistant": "bg-[#041404] text-[#a7f3d0] border border-[#34d399]/50 font-mono text-sm leading-relaxed rounded-2xl px-4 py-3",

        "input_row": "bg-[#020802] border border-[#34d399]/55 shadow-[0_0_10px_rgba(16,185,129,0.08)]",
        "input_text": "text-[#a7f3d0] font-mono",

        # Injects direct positive indicators so Quasar renders control toggles in default matrix green
        "input_props": "dark borderless shadow-none color=positive label-color=positive text-color=positive",
        "select_props": 'dark standout dense color=positive label-color=positive text-color=positive popup-content-class="bg-[#041404] border border-[#34d399]/45 text-[#a7f3d0]"',

        "upload_props": "dark flat multiple color=positive",
        "progress_wrap": "bg-[#020802] border border-[#34d399]/40 shadow-inner",
        "progress_divider": "border-[#34d399]/40",
        "progress_label": "text-[#6ee7b7] font-mono",
        "dropzone": "border-dashed border-[#34d399]/55 hover:border-[#6ee7b7] bg-[#031003]",
        "dropzone_text": "text-[#6ee7b7] font-mono text-xs",
        "dropzone_icon": "text-[#34d399]",
        "muted": "text-[#34d399] font-mono text-xs",
        "separator": "border-t border-[#34d399]/40",
        "menu_bg": "p-3 border border-[#34d399]/45 bg-[#020802] rounded-xl shadow-[0_0_15px_rgba(0,0,0,0.8)]",
        "menu_label": "text-[#6ee7b7] font-mono uppercase tracking-wider text-[10px]",
        "console_log": "text-[#a7f3d0] font-mono text-xs",
        "settings_card": "bg-[#020802] border border-[#34d399]/40",
        "file_card": "bg-[#041404] hover:bg-[#062006] border border-[#34d399]/45",
        "file_card_text": "text-[#a7f3d0] font-mono text-xs",
        "link_card": "bg-[#020802] hover:bg-[#041404] border border-[#34d399]/50",
        "icon_btn": "text-[#34d399] hover:text-[#a7f3d0]",
        "icon_btn_hover": "hover:bg-[#10b981]/20",
        "tab_active": "text-[#a7f3d0] font-mono font-bold tracking-tight border-b border-[#6ee7b7]",
    },
}

ROLE_SUGGESTIONS = {
    "Orchestrator": ["llama3:70b", "command-r", "qwen2:72b"],
    "Specialist": ["llama3:8b", "mistral:7b", "gemma2:9b"],
    "General": ["phi3:mini", "llama3:8b", "gemma2:2b"],
    "Verifier": ["llama3:8b", "phi3:medium"],
}


def get_theme(theme_id: str | None = None) -> dict[str, str]:
    from services.session import state

    tid = theme_id or state.current_settings.get("theme", "dark")
    if tid == "system":
        # Resolved once per client connection (see ui/layouts/main_layout.py's
        # _resolve_system_theme) via the browser's prefers-color-scheme — defaults to
        # dark if that hasn't run yet (e.g. called before the client ever connects).
        is_dark = state.system_theme_is_dark
        tid = "light" if is_dark is False else "dark"
    return THEMES.get(tid, THEMES["dark"])