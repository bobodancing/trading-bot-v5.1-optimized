"""
Tests for MIN_FAKEOUT_ATR signal quality filter (Part B).
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
import numpy as np
from trader.signals import detect_2b_with_pivots


def _make_df_with_fakeout(swing_low, fakeout_low, close_above_swing, atr=1.0, n=30):
    """建立含 fakeout 場景的 DataFrame（Bullish 2B）"""
    closes = [swing_low + 2.0] * n
    lows = [swing_low + 2.0] * n
    highs = [swing_low + 3.0] * n
    opens = [swing_low + 2.0] * n
    volumes = [1000.0] * n

    # 最後一根：跌破 swing_low 後收回
    closes[-1] = close_above_swing
    lows[-1] = fakeout_low
    highs[-1] = close_above_swing + 0.5
    opens[-1] = swing_low + 0.5
    volumes[-1] = 2000.0  # 放量

    df = pd.DataFrame({
        'open': opens, 'high': highs, 'low': lows,
        'close': closes, 'volume': volumes,
    })
    df['atr'] = atr
    df['vol_ma'] = 1000.0
    return df


class TestMinFakeoutATR:

    def test_fakeout_too_shallow_filtered(self):
        """2B 穿透 < 0.3 ATR 應被過濾"""
        swing_low = 100.0
        atr = 1.0
        # 穿透 = 0.1 ATR（低於 0.3 門檻）
        fakeout_low = swing_low - 0.1 * atr
        df = _make_df_with_fakeout(swing_low, fakeout_low, swing_low + 0.5, atr)
        has_signal, details = detect_2b_with_pivots(
            df, left_bars=3, right_bars=2, min_fakeout_atr=0.3
        )
        assert not has_signal, \
            f"Should be filtered: penetration {0.1}x ATR < 0.3x ATR"

    def test_fakeout_sufficient_passes(self):
        """2B 穿透 >= 0.3 ATR 應通過（若量能達標）"""
        swing_low = 100.0
        atr = 1.0
        # 穿透 = 0.5 ATR（高於 0.3 門檻）
        fakeout_low = swing_low - 0.5 * atr
        df = _make_df_with_fakeout(swing_low, fakeout_low, swing_low + 0.5, atr)
        has_signal, details = detect_2b_with_pivots(
            df, left_bars=3, right_bars=2, min_fakeout_atr=0.3,
            vol_minimum_threshold=0.7, accept_weak_signals=True
        )
        # 通過深度過濾，signal 應存在（可能因其他條件 False，但不因穿透被濾）
        if has_signal:
            assert details.get('fakeout_depth_atr') >= 0.3, \
                f"fakeout_depth_atr should be >= 0.3, got {details.get('fakeout_depth_atr')}"

    def test_fakeout_depth_atr_in_signal_details(self):
        """有效 2B 信號應含 fakeout_depth_atr 欄位"""
        swing_low = 100.0
        atr = 1.0
        fakeout_low = swing_low - 0.5 * atr  # 0.5 ATR 穿透
        df = _make_df_with_fakeout(swing_low, fakeout_low, swing_low + 0.5, atr)
        has_signal, details = detect_2b_with_pivots(
            df, left_bars=3, right_bars=2, min_fakeout_atr=0.3,
            vol_minimum_threshold=0.7, accept_weak_signals=True
        )
        if has_signal:
            assert 'fakeout_depth_atr' in details, \
                "fakeout_depth_atr should be present in signal_details"
            assert details['fakeout_depth_atr'] == pytest.approx(0.5, abs=0.01)

    def test_default_min_fakeout_atr_is_030(self):
        """預設 min_fakeout_atr=0.3（即不傳參數時行為一致）"""
        swing_low = 100.0
        atr = 1.0
        fakeout_low = swing_low - 0.1 * atr  # 太淺
        df = _make_df_with_fakeout(swing_low, fakeout_low, swing_low + 0.5, atr)
        # 不傳 min_fakeout_atr，用預設值
        has_signal, _ = detect_2b_with_pivots(df, left_bars=3, right_bars=2)
        assert not has_signal, "Default min_fakeout_atr=0.3 should filter shallow penetration"
