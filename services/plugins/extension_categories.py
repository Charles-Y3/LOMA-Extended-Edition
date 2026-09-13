# -*- coding: utf-8 -*-
"""Extension library category labels."""
from __future__ import annotations

LIBRARY_CATEGORIES: tuple[str, ...] = ("utility", "productivity", "ludicity")

CATEGORY_LABELS: dict[str, str] = {
    "utility": "Utility",
    "productivity": "Productivity",
    "creativity": "Creativity",
    "ludicity": "Ludicity",
}

# Utility uses cyan-400 — same accent as TOKEN USAGE / panel cyan headers.
# !text-* so Quasar .q-btn does not force primary blue on filter chips.
CATEGORY_CHIP_CLASS: dict[str, str] = {
    "utility": "!text-cyan-400 border-cyan-400/55 bg-cyan-500/15",
    "productivity": "!text-purple-300 border-purple-400/50 bg-purple-500/15",
    "creativity": "!text-orange-300 border-orange-400/50 bg-orange-500/15",
    "ludicity": "!text-rose-300 border-rose-400/50 bg-rose-500/15",
}

# Inline styles beat Quasar's .q-btn { color: primary } on filter chips.
CATEGORY_CHIP_STYLE: dict[str, str] = {
    "utility": "color:#22d3ee !important; border-color:rgba(34,211,238,0.55) !important;",
    "productivity": "color:#c084fc !important; border-color:rgba(192,132,252,0.5) !important;",
    "creativity": "color:#fb923c !important; border-color:rgba(251,146,60,0.5) !important;",
    "ludicity": "color:#fb7185 !important; border-color:rgba(251,113,133,0.5) !important;",
}

CATEGORY_PILL_CLASS: dict[str, str] = {
    "utility": "text-cyan-400 border-cyan-400/50 bg-cyan-500/10",
    "productivity": "text-purple-300 border-purple-500/45 bg-purple-500/10",
    "creativity": "text-orange-300 border-orange-500/45 bg-orange-500/10",
    "ludicity": "text-rose-300 border-rose-500/45 bg-rose-500/10",
}

CATEGORY_TEXT_CLASS: dict[str, str] = {
    "utility": "text-cyan-400",
    "productivity": "text-purple-300",
    "creativity": "text-orange-300",
    "ludicity": "text-rose-300",
}

CATEGORY_ICON: dict[str, str] = {
    "utility": "handyman",
    "productivity": "work_outline",
    "creativity": "palette",
    "ludicity": "sports_esports",
}

CATEGORY_ICON_CLASS: dict[str, str] = {
    "utility": "text-cyan-400",
    "productivity": "text-purple-300",
    "creativity": "text-orange-300",
    "ludicity": "text-rose-300",
}

# Legacy catalog values → library filter bucket
CATEGORY_ALIASES: dict[str, str] = {
    "viewers": "productivity",
    "translation": "productivity",
    "system": "utility",
    "productivity": "productivity",
    "utility": "utility",
    "creativity": "creativity",
    "ludicity": "ludicity",
}


def normalize_library_category(raw: str) -> str:
    key = (raw or "").strip().lower()
    return CATEGORY_ALIASES.get(key, key if key in LIBRARY_CATEGORIES else "productivity")


def category_display(raw: str) -> str:
    from pipeline.i18n import t

    key = normalize_library_category(raw)
    label = t(f"library.category_{key}")
    if label != f"library.category_{key}":
        return label
    return CATEGORY_LABELS.get(key, (raw or "Other").title())


def category_chip_classes(raw: str | None, *, selected: bool = False) -> str:
    if raw is None:
        from ui.themes.tokens import get_theme

        muted = " ".join(f"!{cls}" for cls in get_theme()["muted"].split())
        base = f"{muted} border-white/25 bg-white/5"
    else:
        base = CATEGORY_CHIP_CLASS.get(
            normalize_library_category(raw), CATEGORY_CHIP_CLASS["utility"]
        )
    sel = " ring-1 ring-white/30 font-semibold" if selected else ""
    return f"text-[10px] min-h-0 px-2 py-0.5 rounded border {base}{sel}"


_UNCATEGORIZED_CHIP_COLOR: dict[str, str] = {
    "dark": "#d1d5db",
    "light": "#475569",
    "mainframe": "#6ee7b7",
}


def category_chip_style(raw: str | None) -> str:
    if raw is None:
        from ui.themes.tokens import THEMES, get_theme

        theme = get_theme()
        tid = next((k for k, v in THEMES.items() if v is theme), "dark")
        color = _UNCATEGORIZED_CHIP_COLOR.get(tid, _UNCATEGORIZED_CHIP_COLOR["dark"])
        return f"color:{color} !important; border-color:rgba(255,255,255,0.25) !important;"
    return CATEGORY_CHIP_STYLE.get(
        normalize_library_category(raw), CATEGORY_CHIP_STYLE["utility"]
    )


def category_pill_class(raw: str) -> str:
    return CATEGORY_PILL_CLASS.get(
        normalize_library_category(raw), CATEGORY_PILL_CLASS["utility"]
    )


def category_icon(raw: str) -> str:
    return CATEGORY_ICON.get(
        normalize_library_category(raw), CATEGORY_ICON["utility"]
    )


def category_icon_class(raw: str) -> str:
    return CATEGORY_ICON_CLASS.get(
        normalize_library_category(raw), CATEGORY_ICON_CLASS["utility"]
    )
