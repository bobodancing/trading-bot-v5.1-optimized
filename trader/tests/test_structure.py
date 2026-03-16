"""Test: Swing Point 偵測 + Neckline 識別"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
from trader.structure import StructureAnalysis


def _make_df(highs, lows):
    """Helper: minimal DataFrame for structure tests"""
    n = len(highs)
    return pd.DataFrame({
        'open': [(h + l) / 2 for h, l in zip(highs, lows)],
        'high': highs,
        'low': lows,
        'close': [(h + l) / 2 for h, l in zip(highs, lows)],
        'volume': [100] * n,
    })


class TestSwingPoints:
    """Swing Point 偵測"""

    def test_basic_swing_low(self):
        """左右都更高 → 確認 swing low"""
        # Bar 5 = swing low at 100
        highs = [120, 118, 115, 112, 108, 105, 110, 115, 118, 120, 119, 118, 116]
        lows =  [115, 112, 108, 105, 102, 100, 104, 108, 112, 115, 114, 113, 111]
        df = _make_df(highs, lows)

        result = StructureAnalysis.find_swing_points(df, left_bars=5, right_bars=2)
        assert result['last_swing_low'] == 100.0

    def test_basic_swing_high(self):
        """左右都更低 → 確認 swing high"""
        highs = [110, 112, 115, 118, 120, 125, 122, 118, 115, 112, 110, 108, 106]
        lows =  [105, 107, 110, 113, 115, 120, 117, 113, 110, 107, 105, 103, 101]
        df = _make_df(highs, lows)

        result = StructureAnalysis.find_swing_points(df, left_bars=5, right_bars=2)
        assert result['last_swing_high'] == 125.0

    def test_no_swing_in_flat_data(self):
        """平坦數據不產生 swing"""
        highs = [110] * 13
        lows = [100] * 13
        df = _make_df(highs, lows)

        result = StructureAnalysis.find_swing_points(df, left_bars=5, right_bars=2)
        assert result['last_swing_low'] is None
        assert result['last_swing_high'] is None

    def test_insufficient_data(self):
        """數據不足"""
        df = _make_df([100, 101], [99, 100])
        result = StructureAnalysis.find_swing_points(df, left_bars=5, right_bars=2)
        assert result['swing_lows'] == []
        assert result['swing_highs'] == []

    def test_multiple_swing_lows(self):
        """多個 swing low，last 和 second_last"""
        # Two swing lows: bar 3 (low=98) and bar 9 (low=95)
        highs = [110, 108, 105, 103, 106, 108, 106, 103, 100, 98, 102, 105, 108, 110, 112]
        lows =  [105, 103, 100, 98,  101, 103, 101, 98,  95,  93, 97,  100, 103, 105, 107]
        df = _make_df(highs, lows)

        result = StructureAnalysis.find_swing_points(df, left_bars=3, right_bars=2)
        assert len(result['swing_lows']) >= 1
        assert result['last_swing_low'] is not None


class TestNeckline:
    """Neckline 識別"""

    def test_bullish_neckline(self):
        """Bullish 2B → neckline = last swing high"""
        highs = [120, 118, 115, 112, 108, 105, 110, 115, 118, 120, 119, 118, 116]
        lows =  [115, 112, 108, 105, 102, 100, 104, 108, 112, 115, 114, 113, 111]
        df = _make_df(highs, lows)

        neck = StructureAnalysis.find_neckline(df, 'LONG', left_bars=5, right_bars=2)
        assert neck is not None  # Should find swing high

    def test_bearish_neckline(self):
        """Bearish 2B → neckline = last swing low"""
        # Need both a swing high AND a swing low (for SHORT neckline)
        # Swing high at bar 5 (125), swing low at bar 9 (100)
        highs = [110, 112, 115, 118, 122, 125, 120, 115, 108, 105, 108, 112, 115]
        lows =  [105, 107, 110, 113, 117, 120, 115, 110, 103, 100, 103, 107, 110]
        df = _make_df(highs, lows)

        neck = StructureAnalysis.find_neckline(df, 'SHORT', left_bars=5, right_bars=2)
        assert neck is not None  # Should find swing low
        assert neck == 100.0

    def test_neckline_none_no_opposite(self):
        """沒有反向 swing → neckline = None"""
        # Monotonically decreasing: has swing low but no swing high
        highs = [130, 128, 126, 124, 122, 120, 118, 116, 114, 112, 110, 108, 106]
        lows =  [125, 123, 121, 119, 117, 100, 113, 111, 109, 107, 105, 103, 101]
        df = _make_df(highs, lows)

        # LONG neckline needs swing high → should be None
        neck = StructureAnalysis.find_neckline(df, 'LONG', left_bars=5, right_bars=2)
        assert neck is None


class TestConfirmedPivots:
    """get_confirmed_pivots + find_latest_confirmed_swing"""

    def test_confirmed_pivots(self):
        highs = [120, 118, 115, 112, 108, 105, 110, 115, 118, 120, 119, 118, 116]
        lows =  [115, 112, 108, 105, 102, 100, 104, 108, 112, 115, 114, 113, 111]
        df = _make_df(highs, lows)

        pivots = StructureAnalysis.get_confirmed_pivots(df, left=5, right=2)
        assert 'lows' in pivots
        assert 'highs' in pivots
        assert len(pivots['lows']) > 0

    def test_latest_swing_low(self):
        highs = [120, 118, 115, 112, 108, 105, 110, 115, 118, 120, 119, 118, 116]
        lows =  [115, 112, 108, 105, 102, 100, 104, 108, 112, 115, 114, 113, 111]
        df = _make_df(highs, lows)

        val = StructureAnalysis.find_latest_confirmed_swing(df, 'low', 5, 2)
        assert val == 100.0
