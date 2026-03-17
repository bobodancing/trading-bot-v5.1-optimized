"""
EMA_PULLBACK + VOLUME_BREAKOUT 信號偵測 unit tests
"""

import pytest
import pandas as pd
import numpy as np

from trader.signals import detect_ema_pullback, detect_volume_breakout


def _make_df(rows: int = 40, **overrides) -> pd.DataFrame:
    """建立基本 DataFrame（含 ema_fast, ema_slow, atr, vol_ma）"""
    base = {
        'open': [100.0] * rows,
        'high': [102.0] * rows,
        'low': [98.0] * rows,
        'close': [101.0] * rows,
        'volume': [1000.0] * rows,
        'ema_fast': [100.0] * rows,
        'ema_slow': [99.0] * rows,
        'atr': [1.0] * rows,
        'vol_ma': [1000.0] * rows,
    }
    base.update(overrides)
    return pd.DataFrame(base)


class TestEMAPullback:
    """EMA 回撤信號"""

    def test_01_long_signal_bullish_trend(self):
        """多頭趨勢 + 回撤觸及 EMA → LONG"""
        df = _make_df()
        # 前一根低點觸及 ema_fast (100.0)
        df.loc[df.index[-2], 'low'] = 100.0
        # 當前收盤在 ema_fast 上方
        df.loc[df.index[-1], 'close'] = 101.5
        df.loc[df.index[-1], 'ema_fast'] = 100.0
        df.loc[df.index[-1], 'ema_slow'] = 99.0

        has_signal, details = detect_ema_pullback(df)
        assert has_signal is True
        assert details['side'] == 'LONG'
        assert details['detection_method'] == 'ema_pullback'

    def test_02_short_signal_bearish_trend(self):
        """空頭趨勢 + 反彈觸及 EMA → SHORT"""
        df = _make_df()
        # 空頭：ema_fast < ema_slow
        for i in range(len(df)):
            df.loc[df.index[i], 'ema_fast'] = 100.0
            df.loc[df.index[i], 'ema_slow'] = 101.0
            df.loc[df.index[i], 'close'] = 99.0
        # 前一根高點觸及 ema_fast
        df.loc[df.index[-2], 'high'] = 100.0
        # 當前收盤在 ema_fast 下方
        df.loc[df.index[-1], 'close'] = 99.5

        has_signal, details = detect_ema_pullback(df)
        assert has_signal is True
        assert details['side'] == 'SHORT'

    def test_03_no_signal_flat_ema(self):
        """EMA 平坦（ema_fast == ema_slow）→ 無信號"""
        df = _make_df()
        for i in range(len(df)):
            df.loc[df.index[i], 'ema_fast'] = 100.0
            df.loc[df.index[i], 'ema_slow'] = 100.0

        has_signal, _ = detect_ema_pullback(df)
        assert has_signal is False

    def test_04_no_signal_insufficient_data(self):
        """資料不足 → 無信號"""
        df = _make_df(rows=10)
        has_signal, _ = detect_ema_pullback(df)
        assert has_signal is False

    def test_05_volume_filter(self):
        """量能不足（< 0.6x）→ 過濾"""
        df = _make_df()
        df.loc[df.index[-2], 'low'] = 100.0
        df.loc[df.index[-1], 'close'] = 101.5
        # 量能極低（0.1x < 0.6 門檻）
        df.loc[df.index[-1], 'volume'] = 100.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, _ = detect_ema_pullback(df)
        assert has_signal is False

    def test_06_details_has_required_fields(self):
        """signal_details 包含 _execute_trade V5.3 路徑所需的所有欄位"""
        df = _make_df()
        df.loc[df.index[-2], 'low'] = 100.0
        df.loc[df.index[-1], 'close'] = 101.5

        has_signal, details = detect_ema_pullback(df)
        if has_signal:
            # _execute_trade V5.3 路徑需要 lowest_point/highest_point
            for key in ['side', 'entry_price', 'lowest_point', 'atr',
                        'vol_ratio', 'signal_strength',
                        'neckline', 'fakeout_depth_atr']:
                assert key in details, f"Missing key: {key}"
            # signal_strength 應固定為 moderate
            assert details['signal_strength'] == 'moderate'


class TestVolumeBreakout:
    """量能突破信號"""

    def test_01_long_breakout(self):
        """價格突破高點 + 爆量 + 陽線 → LONG"""
        df = _make_df()
        # 近 10 根高點 = 102，需要陽線（close > open）
        df.loc[df.index[-1], 'open'] = 101.0
        df.loc[df.index[-1], 'close'] = 103.0
        df.loc[df.index[-1], 'volume'] = 3000.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, details = detect_volume_breakout(df, volume_breakout_mult=2.0)
        assert has_signal is True
        assert details['side'] == 'LONG'
        assert details['vol_ratio'] >= 2.0
        assert 'lowest_point' in details  # _execute_trade V5.3 路徑需要

    def test_02_short_breakout(self):
        """價格跌破低點 + 爆量 + 陰線 → SHORT"""
        df = _make_df()
        # 近 10 根低點 = 98，需要陰線（close < open）
        df.loc[df.index[-1], 'open'] = 99.0
        df.loc[df.index[-1], 'close'] = 97.0
        df.loc[df.index[-1], 'volume'] = 2500.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, details = detect_volume_breakout(df, volume_breakout_mult=2.0)
        assert has_signal is True
        assert details['side'] == 'SHORT'
        assert 'highest_point' in details  # _execute_trade V5.3 路徑需要

    def test_03_no_signal_low_volume(self):
        """量能不足 → 無信號"""
        df = _make_df()
        df.loc[df.index[-1], 'open'] = 101.0
        df.loc[df.index[-1], 'close'] = 103.0
        df.loc[df.index[-1], 'volume'] = 1500.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, _ = detect_volume_breakout(df, volume_breakout_mult=2.0)
        assert has_signal is False

    def test_04_no_signal_price_in_range(self):
        """價格在區間內（未突破）→ 無信號"""
        df = _make_df()
        df.loc[df.index[-1], 'close'] = 100.5
        df.loc[df.index[-1], 'volume'] = 3000.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, _ = detect_volume_breakout(df, volume_breakout_mult=2.0)
        assert has_signal is False

    def test_05_no_signal_wrong_candle(self):
        """突破高點但陰線（close < open）→ 無信號"""
        df = _make_df()
        df.loc[df.index[-1], 'open'] = 104.0  # 陰線
        df.loc[df.index[-1], 'close'] = 103.0
        df.loc[df.index[-1], 'volume'] = 3000.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, _ = detect_volume_breakout(df, volume_breakout_mult=2.0)
        assert has_signal is False

    def test_06_details_has_required_fields(self):
        """signal_details 包含 _execute_trade V5.3 路徑所需的所有欄位"""
        df = _make_df()
        df.loc[df.index[-1], 'open'] = 101.0
        df.loc[df.index[-1], 'close'] = 103.0
        df.loc[df.index[-1], 'volume'] = 3000.0
        df.loc[df.index[-1], 'vol_ma'] = 1000.0

        has_signal, details = detect_volume_breakout(df, volume_breakout_mult=2.0)
        if has_signal:
            # _execute_trade V5.3 路徑需要 lowest_point
            for key in ['side', 'entry_price', 'lowest_point', 'atr',
                        'vol_ratio', 'signal_strength',
                        'neckline', 'fakeout_depth_atr']:
                assert key in details, f"Missing key: {key}"
            # signal_strength 應固定為 strong
            assert details['signal_strength'] == 'strong'
