"""
Tests for V53 structure break N-bar confirmation:
- 連續 2 根 1H close 均破結構才出場
- 單根破結構不出場
- LONG / SHORT 鏡像邏輯
- df 不夠長時不崩潰
"""
import pytest
import pandas as pd
from unittest.mock import patch

from trader.positions import PositionManager
from trader.strategies.v53_sop import V53SopStrategy
from trader.structure import StructureAnalysis


def _make_pm(side='LONG', entry_price=100.0, stop_loss=None):
    """建立基本的 V53 PositionManager（monitor_count=5 已過冷卻期）

    stop_loss 預設：LONG=80.0（遠低於測試價格），SHORT=120.0（遠高於測試價格），
    避免 _apply_common_pre 的 SL check 在到達結構破壞邏輯前觸發。
    """
    if stop_loss is None:
        stop_loss = 80.0 if side == 'LONG' else 120.0
    pm = PositionManager(
        symbol='TEST/USDT',
        side=side,
        entry_price=entry_price,
        stop_loss=stop_loss,
        position_size=1.0,
        is_v6_pyramid=False,
        initial_r=abs(entry_price - stop_loss),
    )
    pm.monitor_count = 5  # 已超過冷卻期（> 3）
    return pm


def _make_df(closes, swing_low=95.0, swing_high=105.0, n_prefix=10):
    """
    建立測試用 DataFrame。
    closes: list of close prices，最後兩根是 [-2] 和 [-1]。
    """
    n = n_prefix + len(closes)
    bg_close = closes[0] if closes else 100.0
    data = {
        'open':   [bg_close] * n,
        'high':   [bg_close + 0.5] * n,
        'low':    [bg_close - 0.5] * n,
        'close':  [bg_close] * (n_prefix) + list(closes),
        'volume': [1000.0] * n,
    }
    df = pd.DataFrame(data)
    df['atr'] = 1.0
    df['vol_ma'] = 1000.0
    return df


def _mock_swings(swing_low=95.0, swing_high=105.0):
    return {
        'swing_lows': [(5, swing_low)],
        'swing_highs': [(5, swing_high)],
        'last_swing_low': swing_low,
        'last_swing_high': swing_high,
        'second_last_swing_low': None,
        'second_last_swing_high': None,
    }


class TestLongStructureBreak:
    """LONG 方向結構破壞確認測試"""

    def test_single_bar_break_no_exit(self):
        """LONG: 只有 iloc[-1] 破結構，iloc[-2] 未破 → 不出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('LONG', 100.0)
        swing_low = 95.0

        # iloc[-2] close = 95.5（未破），iloc[-1] close = 94.0（已破）
        df = _make_df([95.5, 94.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_low=swing_low)):
            decision = strategy.get_decision(pm, 94.0, df)
        assert decision['action'] != 'CLOSE', \
            "Single bar break should NOT trigger v53_structure_break"

    def test_two_bars_break_triggers_exit(self):
        """LONG: iloc[-2] 和 iloc[-1] 均低於 swing_low * 0.995 → 出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('LONG', 100.0)
        swing_low = 95.0

        # 兩根 close 都在 94.0（< threshold 94.5275）
        df = _make_df([94.0, 94.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_low=swing_low)):
            decision = strategy.get_decision(pm, 94.0, df)
        assert decision['action'] == 'CLOSE', \
            "Two consecutive bars below swing_low threshold should trigger exit"
        assert pm.exit_reason == 'v53_structure_break'

    def test_prev_break_curr_recover_no_exit(self):
        """LONG: iloc[-2] 破結構，iloc[-1] 回收 → 不出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('LONG', 100.0)
        swing_low = 95.0

        # iloc[-2] close = 94.0（破），iloc[-1] close = 96.0（回收）
        df = _make_df([94.0, 96.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_low=swing_low)):
            decision = strategy.get_decision(pm, 96.0, df)
        assert decision['action'] != 'CLOSE', \
            "Recovery on second bar should NOT trigger exit"

    def test_no_break_no_exit(self):
        """LONG: 兩根都在 swing low 上方 → 不出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('LONG', 100.0)

        df = _make_df([96.0, 97.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_low=95.0)):
            decision = strategy.get_decision(pm, 97.0, df)
        assert decision['action'] != 'CLOSE' or pm.exit_reason != 'v53_structure_break'


class TestShortStructureBreak:
    """SHORT 方向結構破壞確認測試"""

    def test_single_bar_break_no_exit(self):
        """SHORT: 只有 iloc[-1] 破結構，iloc[-2] 未破 → 不出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('SHORT', 100.0)
        swing_high = 105.0

        # iloc[-2] close = 104.0（未破），iloc[-1] close = 106.0（已破）
        df = _make_df([104.0, 106.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_high=swing_high)):
            decision = strategy.get_decision(pm, 106.0, df)
        assert decision['action'] != 'CLOSE', \
            "SHORT: single bar break should NOT trigger"

    def test_two_bars_break_triggers_exit(self):
        """SHORT: 連續 2 根 close 均高於 swing_high * 1.005 → 出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('SHORT', 100.0)
        swing_high = 105.0

        # 兩根都在 106.0（> 105.525）
        df = _make_df([106.0, 106.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_high=swing_high)):
            decision = strategy.get_decision(pm, 106.0, df)
        assert decision['action'] == 'CLOSE', \
            "SHORT: two consecutive bars above threshold should trigger exit"
        assert pm.exit_reason == 'v53_structure_break'

    def test_prev_break_curr_recover_no_exit(self):
        """SHORT: iloc[-2] 破結構，iloc[-1] 回收 → 不出場"""
        strategy = V53SopStrategy()
        pm = _make_pm('SHORT', 100.0)
        swing_high = 105.0

        # iloc[-2] close = 106.0（破），iloc[-1] close = 104.0（回收）
        df = _make_df([106.0, 104.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_high=swing_high)):
            decision = strategy.get_decision(pm, 104.0, df)
        assert decision['action'] != 'CLOSE', \
            "SHORT: recovery on second bar should NOT trigger"


class TestEdgeCases:
    """邊界情況"""

    def test_df_too_short_no_crash(self):
        """df 只有 1 根 K 線時不崩潰（len < 2）"""
        strategy = V53SopStrategy()
        pm = _make_pm('LONG', 100.0)

        df = pd.DataFrame({
            'open': [94.0], 'high': [94.5], 'low': [93.5],
            'close': [94.0], 'volume': [1000.0], 'atr': [1.0], 'vol_ma': [1000.0]
        })

        # 不應拋出 IndexError
        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings()):
            try:
                decision = strategy.get_decision(pm, 94.0, df)
                assert decision['action'] != 'CLOSE' or pm.exit_reason != 'v53_structure_break'
            except IndexError:
                pytest.fail("Should not raise IndexError when df has < 2 rows")

    def test_cooldown_period_respected(self):
        """冷卻期（monitor_count <= 3）內不檢查結構破壞"""
        strategy = V53SopStrategy()
        pm = _make_pm('LONG', 100.0)
        pm.monitor_count = 0  # 剛進場，在冷卻期

        # 兩根都破結構
        df = _make_df([93.0, 93.0])

        with patch.object(StructureAnalysis, 'find_swing_points', return_value=_mock_swings(swing_low=95.0)):
            decision = strategy.get_decision(pm, 93.0, df)
        # 冷卻期內不應出場（monitor_count 會在 _apply_common_pre 中遞增到 1，仍 <= 3）
        assert pm.exit_reason != 'v53_structure_break', \
            "Should not trigger during cooldown period"
