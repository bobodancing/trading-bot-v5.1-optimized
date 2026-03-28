"""Test: 2B Signal Quality Filters (explosive volume, ADX cap, fakeout range)"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
from trader.signals import detect_2b_with_pivots
from trader.config import Config


def _make_df(highs, lows, closes, opens=None, volumes=None,
             vol_ma_val=100.0, atr_val=5.0, adx_val=None):
    n = len(highs)
    if opens is None:
        opens = [(h + l) / 2 for h, l in zip(highs, lows)]
    if volumes is None:
        volumes = [vol_ma_val * 1.5] * n
    data = {
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
        'vol_ma': [vol_ma_val] * n,
        'atr': [atr_val] * n,
    }
    if adx_val is not None:
        data['adx'] = [adx_val] * n
    return pd.DataFrame(data)


# Bullish: swing low at bar 5 (100) + swing high at bar 9 (120)
# Last bar: low=98 (penetrates swing low by 2 = 0.4x ATR@5), close=103 (recovers)
BULLISH_HIGHS =  [120, 118, 115, 112, 108, 105, 110, 115, 118, 120, 119, 118, 108]
BULLISH_LOWS =   [115, 112, 108, 105, 102, 100, 104, 108, 112, 115, 114, 113,  98]
BULLISH_CLOSES = [118, 115, 110, 107, 104, 102, 108, 113, 116, 118, 117, 116, 103]


class TestExplosiveVolumeFilter:
    """Explosive volume 2B signals should be filtered (likely genuine breakout)"""

    def test_explosive_volume_filtered(self):
        # vol_ratio = 3.0x > explosive threshold 2.5x → should be filtered
        volumes = [150.0] * 12 + [300.0]  # last bar: 3x vol_ma
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES,
                      volumes=volumes, vol_ma_val=100.0, atr_val=5.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2,
                                          vol_explosive_threshold=2.5)
        assert has == False
        assert det is None

    def test_strong_volume_passes(self):
        # vol_ratio = 2.0x (strong but not explosive) → should pass
        volumes = [150.0] * 12 + [200.0]  # last bar: 2x vol_ma
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES,
                      volumes=volumes, vol_ma_val=100.0, atr_val=5.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2,
                                          vol_explosive_threshold=2.5)
        assert has == True
        assert det['side'] == 'LONG'


class TestADXMaxFilter:
    """ADX > 50 2B signals should be filtered (trend too strong for reversal)"""

    def test_high_adx_filtered(self):
        # ADX = 55 > ADX_MAX_2B (50) → should be filtered
        volumes = [150.0] * 12 + [200.0]
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES,
                      volumes=volumes, vol_ma_val=100.0, atr_val=5.0, adx_val=55.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == False
        assert det is None

    def test_adx_at_limit_passes(self):
        # ADX = 50 (exactly at limit) → should pass (not strictly greater)
        volumes = [150.0] * 12 + [200.0]
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES,
                      volumes=volumes, vol_ma_val=100.0, atr_val=5.0, adx_val=50.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == True

    def test_no_adx_passes(self):
        # No ADX column → adx defaults to 0 → should pass
        volumes = [150.0] * 12 + [200.0]
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES,
                      volumes=volumes, vol_ma_val=100.0, atr_val=5.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == True


class TestFakeoutDepthRange:
    """Fakeout depth must be 0.6–1.5 ATR"""

    def test_min_fakeout_config_updated(self):
        assert Config.MIN_FAKEOUT_ATR == 0.6

    def test_max_fakeout_config_updated(self):
        assert Config.MAX_FAKEOUT_ATR == 1.5

    def test_adx_max_2b_config_exists(self):
        assert Config.ADX_MAX_2B == 50

    def test_shallow_penetration_filtered(self):
        # Penetration = 2 units, atr = 5 → 0.4x ATR < MIN_FAKEOUT_ATR (0.6) → filtered
        # Last bar low = 98 (swing_low=100, penetration=2, atr=5, ratio=0.4)
        volumes = [150.0] * 12 + [200.0]
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES,
                      volumes=volumes, vol_ma_val=100.0, atr_val=5.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2,
                                          min_fakeout_atr=0.6)
        assert has == False
        assert det is None

    def test_deep_penetration_filtered(self):
        # Penetration > 1.5 ATR → filtered (MAX_FAKEOUT_ATR=1.5)
        # Make last bar low very deep: swing_low=100, low=90, penetration=10, atr=5 → 2.0x ATR
        highs  = BULLISH_HIGHS[:-1] + [108]
        lows   = BULLISH_LOWS[:-1]  + [90]   # penetration = 10 = 2.0x ATR@5
        closes = BULLISH_CLOSES[:-1] + [103]
        volumes = [150.0] * 12 + [200.0]
        df = _make_df(highs, lows, closes, volumes=volumes, vol_ma_val=100.0, atr_val=5.0)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == False
        assert det is None
