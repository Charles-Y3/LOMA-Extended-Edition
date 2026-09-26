# -*- coding: utf-8 -*-
"""Make matplotlib able to draw Chinese (and other CJK) text.

Matplotlib's default font has no CJK glyphs, so a chart with a Chinese title or Chinese column names
came out as rows of empty boxes. Pick the first installed CJK-capable font (per OS) once."""
from __future__ import annotations

_CANDIDATES = (
    # Windows
    "Microsoft JhengHei", "Microsoft YaHei", "SimHei", "MingLiU", "SimSun",
    # macOS
    "PingFang TC", "PingFang SC", "Heiti TC", "Hiragino Sans GB", "STHeiti", "Arial Unicode MS",
    # Linux
    "Noto Sans CJK TC", "Noto Sans CJK SC", "Noto Sans CJK JP", "WenQuanYi Micro Hei", "Droid Sans Fallback",
)
_configured = False


def configure_chart_fonts() -> str | None:
    """Prepend the first available CJK font to matplotlib's sans-serif list. Returns its name (or None)."""
    global _configured
    try:
        import matplotlib
        from matplotlib import font_manager
    except ImportError:
        return None
    installed = {f.name for f in font_manager.fontManager.ttflist}
    chosen = next((c for c in _CANDIDATES if c in installed), None)
    if chosen and not _configured:
        current = list(matplotlib.rcParams.get("font.sans-serif", []))
        matplotlib.rcParams["font.sans-serif"] = [chosen] + [f for f in current if f != chosen]
        matplotlib.rcParams["font.family"] = "sans-serif"
        matplotlib.rcParams["axes.unicode_minus"] = False  # the CJK fonts lack the unicode minus glyph
        _configured = True
    return chosen
