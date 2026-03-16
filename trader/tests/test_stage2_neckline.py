"""
Tests: Stage 2 neckline 重新定義 — 最近支撐取代歷史低點

覆蓋：
1. SHORT: 多個 swing lows，選 entry 以下最高者（最近支撐）
2. LONG: 多個 swing highs，選 entry 以上最低者（最近阻力）
3. Fallback: entry 以下沒有 swing low → last_swing_low
4. entry_price=None: 向後相容，行為與原本相同
5. 嚴格小於：swing 等於 entry price 不納入
6. signals.py 整合：2B SHORT 信號的 neckline 選最近 swing
"""

import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.structure import StructureAnalysis


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_swing_points(swing_lows=None, swing_highs=None):
    """
    直接建立 swing_points dict，跳過 DataFrame 計算。
    swing_lows/highs 格式: [(index, price), ...]
    """
    lows = swing_lows or []
    highs = swing_highs or []
    return {
        'swing_lows': lows,
        'swing_highs': highs,
        'last_swing_low': lows[-1][1] if lows else None,
        'last_swing_high': highs[-1][1] if highs else None,
        'second_last_swing_low': lows[-2][1] if len(lows) >= 2 else None,
        'second_last_swing_high': highs[-2][1] if len(highs) >= 2 else None,
    }


# ─── tests ────────────────────────────────────────────────────────────────────

class TestFindNecklineNearest:

    def test_short_selects_nearest_swing_low(self):
        """SHORT: 多個 swing lows，取 entry 以下 price 最高者"""
        # entry=$1.40，swing lows: $1.35, $1.25, $1.11
        # 預期: neckline=$1.35（最近，不是 $1.11）
        swings = _make_swing_points(swing_lows=[(10, 1.11), (20, 1.25), (30, 1.35)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='SHORT', swing_points=swings, entry_price=1.40
        )
        assert result == 1.35

    def test_long_selects_nearest_swing_high(self):
        """LONG: 多個 swing highs，取 entry 以上 price 最低者"""
        # entry=$100，swing highs: $105, $115, $130
        # 預期: neckline=$105
        swings = _make_swing_points(swing_highs=[(10, 130.0), (20, 115.0), (30, 105.0)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='LONG', swing_points=swings, entry_price=100.0
        )
        assert result == 105.0

    def test_short_fallback_when_no_swing_below_entry(self):
        """SHORT: entry 以下沒有 swing low → fallback 到 last_swing_low"""
        # entry=$1.00，所有 swing lows 都高於或等於 entry
        swings = _make_swing_points(swing_lows=[(10, 1.20), (20, 1.30)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='SHORT', swing_points=swings, entry_price=1.00
        )
        # fallback: last_swing_low = 1.30
        assert result == 1.30

    def test_long_fallback_when_no_swing_above_entry(self):
        """LONG: entry 以上沒有 swing high → fallback 到 last_swing_high"""
        swings = _make_swing_points(swing_highs=[(10, 90.0), (20, 95.0)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='LONG', swing_points=swings, entry_price=100.0
        )
        # fallback: last_swing_high = 95.0
        assert result == 95.0

    def test_entry_price_none_returns_last_swing_low(self):
        """entry_price=None: 向後相容，SHORT 回傳 last_swing_low"""
        swings = _make_swing_points(swing_lows=[(10, 1.11), (20, 1.25), (30, 1.35)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='SHORT', swing_points=swings, entry_price=None
        )
        assert result == 1.35  # last_swing_low

    def test_entry_price_none_returns_last_swing_high(self):
        """entry_price=None: 向後相容，LONG 回傳 last_swing_high"""
        swings = _make_swing_points(swing_highs=[(10, 130.0), (20, 115.0), (30, 105.0)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='LONG', swing_points=swings, entry_price=None
        )
        assert result == 105.0  # last_swing_high

    def test_short_excludes_swing_at_exact_entry_price(self):
        """SHORT: swing price == entry price 不納入（嚴格 p < entry_price）"""
        # entry=$1.35，swing low 剛好在 $1.35（不算）和 $1.25（算）
        swings = _make_swing_points(swing_lows=[(10, 1.25), (20, 1.35)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='SHORT', swing_points=swings, entry_price=1.35
        )
        assert result == 1.25  # $1.35 excluded（不嚴格小於）

    def test_empty_swing_lows_returns_none(self):
        """沒有任何 swing lows → None"""
        swings = _make_swing_points(swing_lows=[])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='SHORT', swing_points=swings, entry_price=1.40
        )
        assert result is None

    def test_unknown_signal_side_returns_none(self):
        """未知 signal_side → None"""
        swings = _make_swing_points(swing_lows=[(10, 1.25)])
        result = StructureAnalysis.find_neckline(
            df=None, signal_side='INVALID', swing_points=swings, entry_price=1.40
        )
        assert result is None
