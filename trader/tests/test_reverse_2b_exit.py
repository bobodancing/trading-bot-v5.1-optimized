"""
Tests for Reverse 2B exit upgrade:
- 穿透深度過濾（MIN_FAKEOUT_ATR）
- 下一根 K 線確認
- LONG / SHORT 鏡像邏輯
- perf_db 欄位持久化
"""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock

from trader.positions import PositionManager
from trader.strategies.v6_pyramid import V6PyramidStrategy
from trader.structure import StructureAnalysis


def _make_2bar_df(
    prev_open, prev_high, prev_low, prev_close,
    curr_open, curr_high, curr_low, curr_close,
    atr=1.0, n_prefix=30
):
    """
    建立 DataFrame：前面 n_prefix 根背景 K 線 + prev + curr（共 n_prefix+2 根）。
    背景 K 線用 prev 附近的值填充，確保 swing point 偵測能找到 pivot。
    """
    bg_close = prev_close
    bg = {
        'open':   [bg_close] * n_prefix,
        'high':   [bg_close + 0.5] * n_prefix,
        'low':    [bg_close - 0.5] * n_prefix,
        'close':  [bg_close] * n_prefix,
        'volume': [1000.0] * n_prefix,
    }
    # 製造一個 swing high（讓 find_swing_points 找得到）
    # 放在 index=10，需要 left > neighbors 且 right > neighbors
    peak = prev_high + 2.0
    bg['high'][10] = peak
    bg['close'][10] = peak - 0.2

    # 製造一個 swing low（SHORT 測試用）
    trough = prev_low - 2.0
    bg['low'][15] = trough
    bg['close'][15] = trough + 0.2

    # Append prev + curr
    for key, vals in [
        ('open',   [prev_open, curr_open]),
        ('high',   [prev_high, curr_high]),
        ('low',    [prev_low,  curr_low]),
        ('close',  [prev_close, curr_close]),
        ('volume', [1000.0, 1000.0]),
    ]:
        bg[key].extend(vals)

    df = pd.DataFrame(bg)
    df['atr'] = atr
    df['vol_ma'] = 1000.0
    return df


def _make_pm(side='LONG', entry_price=100.0, stop_loss=98.0, atr=1.0):
    """建立基本的 V6 PositionManager"""
    pm = PositionManager(
        symbol='TEST/USDT',
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        position_size=1.0,
        is_v6_pyramid=True,
        initial_r=abs(entry_price - stop_loss),
    )
    pm.atr = atr
    return pm


class TestReverse2BDepthFilter:
    """穿透深度過濾測試"""

    def test_long_shallow_wick_no_trigger(self):
        """LONG: wick 穿過 swing high 但深度 < 0.3 ATR → 不觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('LONG', 100.0, 98.0, atr=1.0)

        # 用 mock 控制 swing high = 105.0
        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [], 'swing_highs': [(10, 105.0)],
            'last_swing_low': None, 'last_swing_high': 105.0,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: high=105.1（穿 0.1 ATR < 0.3 門檻），close=104.8
            # curr: close=104.7（確認在 SH 下方）
            df = _make_2bar_df(
                prev_open=104.5, prev_high=105.1, prev_low=104.3, prev_close=104.8,
                curr_open=104.7, curr_high=104.9, curr_low=104.5, curr_close=104.7,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 104.7, df)
            assert decision['action'] != 'CLOSE' or decision['reason'] != 'REVERSE_2B', \
                "Shallow wick (0.1 ATR) should NOT trigger reverse 2B"

    def test_long_deep_wick_triggers(self):
        """LONG: wick 穿過 swing high 深度 >= 0.3 ATR + 下一根確認 → 觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('LONG', 100.0, 98.0, atr=1.0)

        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [], 'swing_highs': [(10, 105.0)],
            'last_swing_low': None, 'last_swing_high': 105.0,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: high=105.5（穿 0.5 ATR >= 0.3），close=104.8
            # curr: close=104.6（確認在 SH 下方）
            df = _make_2bar_df(
                prev_open=104.5, prev_high=105.5, prev_low=104.3, prev_close=104.8,
                curr_open=104.7, curr_high=104.9, curr_low=104.4, curr_close=104.6,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 104.6, df)
            assert decision['action'] == 'CLOSE' and decision['reason'] == 'REVERSE_2B', \
                f"Deep wick (0.5 ATR) + confirmation should trigger, got {decision}"

    def test_short_shallow_wick_no_trigger(self):
        """SHORT: wick 穿過 swing low 但深度 < 0.3 ATR → 不觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('SHORT', 100.0, 102.0, atr=1.0)

        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [(15, 95.0)], 'swing_highs': [],
            'last_swing_low': 95.0, 'last_swing_high': None,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: low=94.9（穿 0.1 ATR < 0.3），close=95.3
            # curr: close=95.4
            df = _make_2bar_df(
                prev_open=95.5, prev_high=95.7, prev_low=94.9, prev_close=95.3,
                curr_open=95.3, curr_high=95.5, curr_low=95.2, curr_close=95.4,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 95.4, df)
            assert decision['action'] != 'CLOSE' or decision['reason'] != 'REVERSE_2B', \
                "SHORT: shallow wick should NOT trigger reverse 2B"

    def test_short_deep_wick_triggers(self):
        """SHORT: wick 穿過 swing low 深度 >= 0.3 ATR + 下一根確認 → 觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('SHORT', 100.0, 102.0, atr=1.0)

        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [(15, 95.0)], 'swing_highs': [],
            'last_swing_low': 95.0, 'last_swing_high': None,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: low=94.5（穿 0.5 ATR >= 0.3），close=95.3
            # curr: close=95.5（確認在 SL 上方）
            df = _make_2bar_df(
                prev_open=95.5, prev_high=95.7, prev_low=94.5, prev_close=95.3,
                curr_open=95.3, curr_high=95.6, curr_low=95.2, curr_close=95.5,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 95.5, df)
            assert decision['action'] == 'CLOSE' and decision['reason'] == 'REVERSE_2B', \
                f"SHORT: deep wick + confirmation should trigger, got {decision}"


class TestReverse2BConfirmation:
    """下一根確認機制測試"""

    def test_long_no_confirmation_no_trigger(self):
        """LONG: prev 穿透夠深但 curr 收回 swing high 上方 → 不觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('LONG', 100.0, 98.0, atr=1.0)

        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [], 'swing_highs': [(10, 105.0)],
            'last_swing_low': None, 'last_swing_high': 105.0,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: high=105.5（穿 0.5 ATR），close=104.8（在 SH 下方）
            # curr: close=105.2（收回 SH 上方 → 不確認）
            df = _make_2bar_df(
                prev_open=104.5, prev_high=105.5, prev_low=104.3, prev_close=104.8,
                curr_open=104.9, curr_high=105.3, curr_low=104.8, curr_close=105.2,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 105.2, df)
            assert decision['action'] != 'CLOSE' or decision['reason'] != 'REVERSE_2B', \
                "Curr candle recovered above SH → should NOT trigger"

    def test_short_no_confirmation_no_trigger(self):
        """SHORT: prev 穿透夠深但 curr 收回 swing low 下方 → 不觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('SHORT', 100.0, 102.0, atr=1.0)

        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [(15, 95.0)], 'swing_highs': [],
            'last_swing_low': 95.0, 'last_swing_high': None,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: low=94.5（穿 0.5 ATR），close=95.3（在 SL 上方）
            # curr: close=94.8（收回 SL 下方 → 不確認）
            df = _make_2bar_df(
                prev_open=95.5, prev_high=95.7, prev_low=94.5, prev_close=95.3,
                curr_open=95.2, curr_high=95.3, curr_low=94.7, curr_close=94.8,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 94.8, df)
            assert decision['action'] != 'CLOSE' or decision['reason'] != 'REVERSE_2B', \
                "SHORT: curr candle back below SL → should NOT trigger"

    def test_prev_no_fakeout_no_trigger(self):
        """prev 根本沒穿過 swing high → 不觸發"""
        strategy = V6PyramidStrategy()
        pm = _make_pm('LONG', 100.0, 98.0, atr=1.0)

        with patch.object(StructureAnalysis, 'find_swing_points', return_value={
            'swing_lows': [], 'swing_highs': [(10, 105.0)],
            'last_swing_low': None, 'last_swing_high': 105.0,
            'second_last_swing_low': None, 'second_last_swing_high': None,
        }):
            # prev: high=104.8（沒穿過 SH=105.0）
            # curr: close=104.5
            df = _make_2bar_df(
                prev_open=104.5, prev_high=104.8, prev_low=104.3, prev_close=104.6,
                curr_open=104.5, curr_high=104.7, curr_low=104.3, curr_close=104.5,
                atr=1.0,
            )
            decision = strategy.get_decision(pm, 104.5, df)
            assert decision['action'] != 'CLOSE' or decision['reason'] != 'REVERSE_2B', \
                "prev never crossed SH → should NOT trigger"


class TestReverse2BPersistence:
    """reverse_2b_depth_atr 持久化測試"""

    def test_depth_atr_roundtrip(self):
        """reverse_2b_depth_atr 設值後 to_dict/from_dict 應保留"""
        pm = _make_pm()
        pm.reverse_2b_depth_atr = 0.55
        data = pm.to_dict()
        assert data['reverse_2b_depth_atr'] == 0.55
        pm2 = PositionManager.from_dict(data)
        assert pm2.reverse_2b_depth_atr == 0.55

    def test_depth_atr_none_roundtrip(self):
        """未觸發時 reverse_2b_depth_atr = None 也應正確 round-trip"""
        pm = _make_pm()
        data = pm.to_dict()
        assert data['reverse_2b_depth_atr'] is None
        pm2 = PositionManager.from_dict(data)
        assert pm2.reverse_2b_depth_atr is None
