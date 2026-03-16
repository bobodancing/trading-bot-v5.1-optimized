"""Test: V6.0 2B 偵測（swing pivot + neckline + volume filter）"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
from trader.signals import detect_2b_with_pivots


def _make_df(highs, lows, closes, opens=None, volumes=None, vol_ma_val=100.0, atr_val=5.0):
    n = len(highs)
    if opens is None:
        opens = [(h + l) / 2 for h, l in zip(highs, lows)]
    if volumes is None:
        volumes = [vol_ma_val * 1.5] * n
    return pd.DataFrame({
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
        'vol_ma': [vol_ma_val] * n,
        'atr': [atr_val] * n,
    })


# Data fixtures: clear swing low at bar 5 (100) + swing high at bar 9 (120)
BULLISH_HIGHS =  [120, 118, 115, 112, 108, 105, 110, 115, 118, 120, 119, 118, 108]
BULLISH_LOWS =   [115, 112, 108, 105, 102, 100, 104, 108, 112, 115, 114, 113,  98]
BULLISH_CLOSES = [118, 115, 110, 107, 104, 102, 108, 113, 116, 118, 117, 116, 103]

# Data fixtures: swing high at bar 5 (200) + swing low at bar 9 (177)
BEARISH_HIGHS =  [180, 182, 185, 188, 192, 200, 195, 190, 185, 182, 183, 184, 205]
BEARISH_LOWS =   [175, 177, 180, 183, 187, 194, 190, 185, 180, 177, 178, 179, 192]
BEARISH_CLOSES = [178, 180, 183, 186, 190, 197, 192, 187, 182, 179, 180, 181, 196]


class TestBullish2B:
    """Bullish 2B detection"""

    def test_detected(self):
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == True
        assert det['side'] == 'LONG'

    def test_swing_point_stop(self):
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert det['stop_level'] == 100.0

    def test_neckline_is_swing_high(self):
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert det['neckline'] == 120.0

    def test_stop_loss_with_buffer(self):
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES, atr_val=5.0)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        # SL = swing low - 0.5 * ATR = 100 - 2.5 = 97.5
        assert det['stop_loss'] == pytest.approx(97.5, abs=0.01)

    def test_detection_method(self):
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert det['detection_method'] == 'swing_pivot'


class TestBearish2B:
    """Bearish 2B detection"""

    def test_detected(self):
        df = _make_df(BEARISH_HIGHS, BEARISH_LOWS, BEARISH_CLOSES)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == True
        assert det['side'] == 'SHORT'

    def test_swing_point_stop(self):
        df = _make_df(BEARISH_HIGHS, BEARISH_LOWS, BEARISH_CLOSES)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert det['stop_level'] == 200.0

    def test_neckline_is_swing_low(self):
        df = _make_df(BEARISH_HIGHS, BEARISH_LOWS, BEARISH_CLOSES)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert det['neckline'] == 177.0

    def test_stop_loss_with_buffer(self):
        df = _make_df(BEARISH_HIGHS, BEARISH_LOWS, BEARISH_CLOSES, atr_val=5.0)
        _, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        # SL = swing high + 0.5 * ATR = 200 + 2.5 = 202.5
        assert det['stop_loss'] == pytest.approx(202.5, abs=0.01)


class TestFilters:
    """Volume / Depth filters"""

    def test_low_volume_filtered(self):
        volumes = [100] * 12 + [30]
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES, volumes=volumes)
        has, _ = detect_2b_with_pivots(df, left_bars=5, right_bars=2, vol_minimum_threshold=0.7)
        assert has == False

    def test_depth_filtered(self):
        deep_lows = list(BULLISH_LOWS)
        deep_lows[-1] = 50  # fakeout depth = 50, atr*3 = 15
        df = _make_df(BULLISH_HIGHS, deep_lows, BULLISH_CLOSES)
        has, _ = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == False

    def test_weak_signal_accepted(self):
        volumes = [100] * 12 + [80]  # vol_ratio = 0.8, weak
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES, volumes=volumes)
        has, det = detect_2b_with_pivots(
            df, left_bars=5, right_bars=2,
            accept_weak_signals=True, vol_minimum_threshold=0.7
        )
        assert has == True
        assert det['signal_strength'] == 'weak'

    def test_weak_signal_rejected(self):
        volumes = [100] * 12 + [80]
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES, volumes=volumes)
        has, _ = detect_2b_with_pivots(
            df, left_bars=5, right_bars=2,
            accept_weak_signals=False, vol_minimum_threshold=0.7
        )
        assert has == False


class TestEdgeCases:
    """Edge cases"""

    def test_insufficient_data(self):
        df = _make_df([100, 101], [99, 100], [100, 100])
        has, _ = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == False

    def test_flat_data_no_signal(self):
        df = _make_df([110]*13, [100]*13, [105]*13)
        has, _ = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == False

    def test_neckline_none(self):
        # Monotonically decreasing highs: swing low exists, no swing high
        highs =  [130, 128, 126, 124, 122, 120, 118, 116, 114, 112, 110, 108, 107]
        lows =   [125, 123, 121, 119, 117, 100, 113, 111, 109, 107, 105, 103,  98]
        closes = [128, 126, 124, 122, 120, 115, 116, 114, 112, 110, 108, 106, 103]
        df = _make_df(highs, lows, closes)

        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == True
        assert det['neckline'] is None

    def test_signal_completeness(self):
        """All required fields present"""
        df = _make_df(BULLISH_HIGHS, BULLISH_LOWS, BULLISH_CLOSES)
        has, det = detect_2b_with_pivots(df, left_bars=5, right_bars=2)
        assert has == True

        required = [
            'side', 'entry_price', 'stop_level', 'target_ref',
            'prev_low', 'prev_high', 'neckline', 'atr', 'volume',
            'vol_ma', 'candle_confirmed', 'detection_method',
            'vol_ratio', 'signal_strength', 'stop_loss',
        ]
        for f in required:
            assert f in det, f"Missing: {f}"
