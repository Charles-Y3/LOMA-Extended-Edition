# -*- coding: utf-8 -*-
"""Auto-generated charts follow the UI language, and Chinese text actually renders (matplotlib's default
font has no CJK glyphs, which used to draw rows of empty boxes)."""
from __future__ import annotations

import re
import warnings

import pytest

pytest.importorskip("pipeline.workflow")
pd = pytest.importorskip("pandas")
pytest.importorskip("matplotlib")

from pipeline import i18n  # noqa: E402
from pipeline.i18n_messages import MESSAGE_STRINGS  # noqa: E402
from services.graph_generation import fonts  # noqa: E402
from services.session import state  # noqa: E402


@pytest.mark.parametrize("loc", i18n.SUPPORTED_LOCALES)
def test_chart_strings_complete_and_consistent(loc):
    ph = re.compile(r"\{(\w+)\}")
    keys = [k for k in MESSAGE_STRINGS["en"] if k.startswith("chart.")]
    assert len(keys) >= 12
    for key in keys:
        text = MESSAGE_STRINGS[loc][key]
        assert set(ph.findall(text)) == set(ph.findall(MESSAGE_STRINGS["en"][key])), (loc, key)
        assert i18n.TRANSLATIONS[loc][key] == text


def _has_cjk_font() -> bool:
    return fonts.configure_chart_fonts() is not None


@pytest.mark.skipif(not _has_cjk_font(), reason="no CJK-capable font installed on this machine")
def test_chinese_chart_renders_without_missing_glyphs(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "current_settings", {"language": "zh_tw"})
    csv = tmp_path / "data.csv"
    pd.DataFrame(
        {
            "平台": ["臉書", "推特", "抖音"] * 3,
            "觀看數": [1200, 800, 3000, 1500, 700, 2800, 1100, 900, 3200],
            "按讚數": [120, 60, 400, 150, 55, 380, 110, 70, 420],
        }
    ).to_csv(csv, index=False, encoding="utf-8")
    from services.graph_generation.plot import render_charts_for_dataset

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        arts = render_charts_for_dataset(str(csv), name="data.csv", output_dir=str(tmp_path / "out"), max_charts=4)
    assert arts, "no charts produced"
    assert not [w for w in caught if "missing from font" in str(w.message) or "Glyph" in str(w.message)]
    captions = " ".join(a.caption for a in arts)
    assert "Spread and outliers" not in captions and "Relationship between" not in captions
    assert re.search(r"[一-鿿]", captions), captions


def test_font_setup_prefers_a_cjk_font_and_keeps_minus_signs():
    import matplotlib

    chosen = fonts.configure_chart_fonts()
    if chosen:
        assert matplotlib.rcParams["font.sans-serif"][0] == chosen
        assert matplotlib.rcParams["axes.unicode_minus"] is False
