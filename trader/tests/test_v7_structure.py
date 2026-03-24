"""V7 StructureStrategy unit tests"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from trader.strategies.base import Action


# ==================== Helpers ====================

def make_df(rows, columns=('open', 'high', 'low', 'close', 'volume', 'vol_ma', 'atr')):
    return pd.DataFrame(rows, columns=columns)


def make_pm(
    symbol='BTCUSDT', side='LONG', entry_price=100.0, stop_loss=95.0,
    stage=1, atr=2.0, entry_time=None, monitor_count=5,
):
    pm = MagicMock()
    pm.symbol = symbol
    pm.side = side
    pm.avg_entry = entry_price
    pm.current_sl = stop_loss
    pm.initial_sl = stop_loss
    pm.stage = stage
    pm.atr = atr
    pm.highest_price = entry_price
    pm.lowest_price = entry_price
    pm.risk_dist = abs(entry_price - stop_loss)
    pm.initial_r = 170.0  # 10000 * 0.017
    pm.is_v6_pyramid = False
    pm.strategy_name = 'v7_structure'
    pm.monitor_count = monitor_count
    pm.entry_time = entry_time or datetime.now(timezone.utc)
    pm.entries = [MagicMock(price=entry_price)]
    pm.exit_reason = None
    pm.reverse_2b_depth_atr = None
    pm.is_closed = False
    pm.total_size = 1.0
    pm.equity_base = 10000.0
    pm.signal_tier = 'B'
    return pm


def _make_swing_df_long_hl(n=25):
    """DF with 2 confirmed higher lows: index 7 (90) and index 17 (92). Last candle = bullish + volume."""
    rows = []
    for i in range(n):
        base = 100.0
        if i == 7:
            rows.append((base, base+2, 90.0, base, 150, 100, 2.0))
        elif i == 17:
            rows.append((base, base+2, 92.0, base, 150, 100, 2.0))
        elif i == n - 1:
            rows.append((99.0, 103.0, 98.0, 102.0, 120, 100, 2.0))  # bullish + vol
        else:
            rows.append((base, base+2, base-1, base+1, 100, 100, 2.0))
    return make_df(rows)


def _make_swing_df_short_lh(n=25):
    """DF with 2 confirmed lower highs: index 7 (110) and index 17 (108). Last candle = bearish + volume."""
    rows = []
    for i in range(n):
        base = 100.0
        if i == 7:
            rows.append((base, 110.0, base-2, base, 150, 100, 2.0))
        elif i == 17:
            rows.append((base, 108.0, base-2, base, 150, 100, 2.0))
        elif i == n - 1:
            rows.append((101.0, 102.0, 97.0, 98.0, 120, 100, 2.0))  # bearish + vol
        else:
            rows.append((base, base+1, base-2, base, 100, 100, 2.0))
    return make_df(rows)


# ==================== Tests ====================

class TestV7AddTrigger:
    """加倉觸發（三條件 AND）"""

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_stage1_to_stage2_long(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1)
        df = _make_swing_df_long_hl()
        decision = strategy.get_decision(pm, 102.0, df)
        assert decision['action'] == Action.ADD
        assert decision['add_stage'] == 2
        assert decision['reason'] == 'V7_STRUCTURE_ADD'

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_stage2_to_stage3_long(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=2)
        df = _make_swing_df_long_hl()
        decision = strategy.get_decision(pm, 102.0, df)
        assert decision['action'] == Action.ADD
        assert decision['add_stage'] == 3

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_stage1_to_stage2_short(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='SHORT', entry_price=100.0, stop_loss=112.0, stage=1)
        df = _make_swing_df_short_lh()
        decision = strategy.get_decision(pm, 98.0, df)
        assert decision['action'] == Action.ADD
        assert decision['add_stage'] == 2

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_no_trigger_without_volume(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1)
        df = _make_swing_df_long_hl()
        df.iloc[-1, df.columns.get_loc('volume')] = 50  # below vol_ma
        decision = strategy.get_decision(pm, 102.0, df)
        assert decision['action'] == Action.HOLD

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_no_trigger_without_reversal_candle(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1)
        df = _make_swing_df_long_hl()
        df.iloc[-1, df.columns.get_loc('close')] = 97.0  # bearish candle
        df.iloc[-1, df.columns.get_loc('open')] = 99.0
        decision = strategy.get_decision(pm, 97.0, df)
        assert decision['action'] == Action.HOLD

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_stage3_no_further_add(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=3)
        df = _make_swing_df_long_hl()
        decision = strategy.get_decision(pm, 102.0, df)
        assert decision['action'] != Action.ADD

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_no_trigger_doji_candle(self, mock_pre):
        """body/range < 0.3 → 不觸發"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1)
        df = _make_swing_df_long_hl()
        # doji: close barely above open, body/range < 0.3
        df.iloc[-1, df.columns.get_loc('open')] = 100.0
        df.iloc[-1, df.columns.get_loc('close')] = 100.1  # body = 0.1
        df.iloc[-1, df.columns.get_loc('high')] = 103.0   # range = 5.0
        df.iloc[-1, df.columns.get_loc('low')] = 98.0
        decision = strategy.get_decision(pm, 100.1, df)
        assert decision['action'] == Action.HOLD

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_no_trigger_empty_swings(self, mock_pre):
        """Insufficient data → no swing points → no trigger"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1)
        df = make_df([(100, 102, 98, 101, 120, 100, 2.0)] * 3)  # too few bars
        decision = strategy.get_decision(pm, 101.0, df)
        assert decision['action'] == Action.HOLD


class TestV7Reverse2B:
    """反向 2B 全平"""

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_reverse_2b_long_close(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=95.0, stage=1, atr=2.0)

        rows = []
        for i in range(20):
            if i == 7:
                rows.append((100, 110.0, 98, 100, 100, 100, 2.0))  # swing high = 110
            elif i == 18:
                rows.append((109, 111.0, 108, 109.0, 100, 100, 2.0))  # pierce: high>110, close<110
            elif i == 19:
                rows.append((109, 109.5, 107, 108.0, 100, 100, 2.0))  # confirm: close<110
            else:
                rows.append((100, 101, 98, 100, 100, 100, 2.0))
        df = make_df(rows)
        decision = strategy.get_decision(pm, 108.0, df)
        assert decision['action'] == Action.CLOSE
        assert decision['reason'] == 'REVERSE_2B'

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_reverse_2b_short_close(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='SHORT', entry_price=100.0, stop_loss=105.0, stage=1, atr=2.0)

        rows = []
        for i in range(20):
            if i == 7:
                rows.append((100, 102, 90.0, 100, 100, 100, 2.0))  # swing low = 90
            elif i == 18:
                rows.append((91, 92, 89.0, 91.0, 100, 100, 2.0))  # pierce: low<90, close>90
            elif i == 19:
                rows.append((91, 93, 90.5, 92.0, 100, 100, 2.0))  # confirm: close>90
            else:
                rows.append((100, 102, 99, 100, 100, 100, 2.0))
        df = make_df(rows)
        decision = strategy.get_decision(pm, 92.0, df)
        assert decision['action'] == Action.CLOSE
        assert decision['reason'] == 'REVERSE_2B'


class TestV7Timeout:
    """Stage 1 超時退出"""

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_stage1_timeout(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        old_time = datetime.now(timezone.utc) - timedelta(hours=40)
        pm = make_pm(stage=1, entry_time=old_time)
        df = make_df([(100, 102, 98, 101, 100, 100, 2.0)] * 5)
        decision = strategy.get_decision(pm, 101.0, df)
        assert decision['action'] == Action.CLOSE
        assert decision['reason'] == 'TIME_EXIT'

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_stage2_no_timeout(self, mock_pre):
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        old_time = datetime.now(timezone.utc) - timedelta(hours=40)
        pm = make_pm(stage=2, entry_time=old_time)
        df = make_df([(100, 102, 98, 101, 100, 100, 2.0)] * 5)
        decision = strategy.get_decision(pm, 101.0, df)
        assert decision['action'] != Action.CLOSE or decision['reason'] != 'TIME_EXIT'


class TestV7SLRatchet:
    """SL 棘輪 + 結構 Trailing"""

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_trailing_sl_long_ratchet_up(self, mock_pre):
        """LONG Stage 3: swing low 上移 → SL 上移 (stage=3 so ADD won't fire)"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=3, atr=2.0)
        df = _make_swing_df_long_hl()
        # swing low at 92, SL_ATR_BUFFER=0.8, new_sl = 92 - 1.6 = 90.4 > 88
        decision = strategy.get_decision(pm, 102.0, df)
        # Stage 3 can't add further, so trailing SL is the only path
        assert decision['action'] == Action.UPDATE_SL
        assert decision['new_sl'] is not None and decision['new_sl'] > 88.0

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_trailing_sl_short_ratchet_down(self, mock_pre):
        """SHORT Stage 3: swing high 下移 → SL 下移"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='SHORT', entry_price=100.0, stop_loss=112.0, stage=3, atr=2.0)
        df = _make_swing_df_short_lh()
        # swing high at 108, new_sl = 108 + 1.6 = 109.6 < 112
        decision = strategy.get_decision(pm, 98.0, df)
        assert decision['action'] == Action.UPDATE_SL
        assert decision['new_sl'] is not None and decision['new_sl'] < 112.0

    def test_sl_no_retreat_long(self):
        """LONG: new SL < current SL → no update"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=95.0, stage=2, atr=2.0)
        from trader.config import ConfigV6 as Cfg
        # swing low at 92, new_sl = 92-1.6 = 90.4 < 95 → don't move
        result = strategy._structure_trailing_sl(pm, _make_swing_df_long_hl(), Cfg)
        assert result is None

    def test_sl_no_retreat_short(self):
        """SHORT: new SL > current SL → no update"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()
        pm = make_pm(side='SHORT', entry_price=100.0, stop_loss=105.0, stage=2, atr=2.0)
        from trader.config import ConfigV6 as Cfg
        # swing high at 108, new_sl = 108+1.6 = 109.6 > 105 → don't move
        result = strategy._structure_trailing_sl(pm, _make_swing_df_short_lh(), Cfg)
        assert result is None


class TestV7PositionSizing:
    """加倉倉位計算"""

    def test_basic_sizing(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        size = V7StructureStrategy.calculate_add_size(
            balance=10000, risk_per_trade=0.017,
            entry_price=100.0, new_sl=95.0,  # 5% dist
        )
        # risk=170, sl_pct=0.05, val=3400, size=34
        assert abs(size - 34.0) < 0.01

    def test_max_position_cap(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        size = V7StructureStrategy.calculate_add_size(
            balance=10000, risk_per_trade=0.017,
            entry_price=100.0, new_sl=99.9,  # 0.1% dist → huge
            max_position_percent=0.146,
        )
        assert abs(size - 14.6) < 0.01

    def test_max_total_risk_cap(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        size = V7StructureStrategy.calculate_add_size(
            balance=10000, risk_per_trade=0.017,
            entry_price=100.0, new_sl=95.0,
            max_total_risk=0.05, current_total_risk_pct=0.04,
        )
        assert size < 34.0

    def test_zero_budget_returns_zero(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        size = V7StructureStrategy.calculate_add_size(
            balance=10000, risk_per_trade=0.017,
            entry_price=100.0, new_sl=95.0,
            max_total_risk=0.05, current_total_risk_pct=0.05,
        )
        assert size == 0.0

    def test_zero_sl_distance(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        size = V7StructureStrategy.calculate_add_size(
            balance=10000, risk_per_trade=0.017,
            entry_price=100.0, new_sl=100.0,
        )
        assert size == 0.0


class TestV7StatePersistence:
    """State round-trip"""

    def test_round_trip(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        s = V7StructureStrategy()
        s.last_structure_swing = 92.5
        s.add_trigger_swings = [90.0, 92.5]
        state = s.get_state()
        s2 = V7StructureStrategy()
        s2.load_state(state)
        assert s2.last_structure_swing == 92.5
        assert s2.add_trigger_swings == [90.0, 92.5]

    def test_load_empty_state(self):
        from trader.strategies.v7_structure import V7StructureStrategy
        s = V7StructureStrategy()
        s.load_state({})
        assert s.last_structure_swing is None
        assert s.add_trigger_swings == []


class TestV7Registration:
    """StrategyFactory 註冊"""

    def test_factory_create_v7(self):
        from trader.strategies.base import StrategyFactory
        from trader.strategies.v7_structure import V7StructureStrategy
        s = StrategyFactory.create("v7_structure")
        assert isinstance(s, V7StructureStrategy)

    def test_factory_legacy_v7(self):
        from trader.strategies.base import StrategyFactory
        from trader.strategies.v7_structure import V7StructureStrategy
        s = StrategyFactory.create_strategy("v7")
        assert isinstance(s, V7StructureStrategy)

    def test_factory_legacy_V7(self):
        from trader.strategies.base import StrategyFactory
        from trader.strategies.v7_structure import V7StructureStrategy
        s = StrategyFactory.create_strategy("V7")
        assert isinstance(s, V7StructureStrategy)


class TestV7Integration:
    """V7 full lifecycle + calculate_add_size integration"""

    @patch('trader.strategies.v7_structure._apply_common_pre', return_value=None)
    def test_full_cycle_long(self, mock_pre):
        """LONG: Stage 1 HOLD -> ADD Stage 2 -> ADD Stage 3 -> no more adds"""
        from trader.strategies.v7_structure import V7StructureStrategy
        strategy = V7StructureStrategy()

        # Stage 1: HOLD (no swing structure yet)
        pm = make_pm(side='LONG', entry_price=100.0, stop_loss=88.0, stage=1, atr=2.0)
        df_hold = make_df([(100, 102, 98, 101, 80, 100, 2.0)] * 10)
        decision = strategy.get_decision(pm, 101.0, df_hold)
        assert decision['action'] == Action.HOLD

        # Stage 1 -> 2: HL formed
        df_add = _make_swing_df_long_hl()
        decision = strategy.get_decision(pm, 102.0, df_add)
        assert decision['action'] == Action.ADD
        assert decision['add_stage'] == 2
        assert decision['new_sl'] is not None

        # Simulate stage transition
        pm.stage = 2
        pm.current_sl = decision['new_sl']

        # Stage 2 -> 3 (may or may not trigger depending on swing state)
        decision = strategy.get_decision(pm, 105.0, df_add)
        if decision['action'] == Action.ADD:
            assert decision['add_stage'] == 3
            pm.stage = 3
            pm.current_sl = decision['new_sl']

        # Stage 3: no more adds
        pm.stage = 3
        decision = strategy.get_decision(pm, 108.0, df_add)
        assert decision['action'] in (Action.UPDATE_SL, Action.HOLD)

        # State persistence
        state = strategy.get_state()
        assert state['last_structure_swing'] is not None
        assert len(state['add_trigger_swings']) >= 1

    def test_add_size_within_total_risk(self):
        """三段加倉後 total risk 不超過 max_total_risk"""
        from trader.strategies.v7_structure import V7StructureStrategy

        balance = 10000.0
        risk_per_trade = 0.017
        max_total_risk = 0.0642

        s1_size = V7StructureStrategy.calculate_add_size(
            balance=balance, risk_per_trade=risk_per_trade,
            entry_price=100.0, new_sl=95.0,
            max_total_risk=max_total_risk, current_total_risk_pct=0.0,
        )
        s1_risk_pct = abs(100.0 - 95.0) / 100.0 * s1_size * 100.0 / balance

        s2_size = V7StructureStrategy.calculate_add_size(
            balance=balance, risk_per_trade=risk_per_trade,
            entry_price=105.0, new_sl=98.0,
            max_total_risk=max_total_risk, current_total_risk_pct=s1_risk_pct,
        )
        s2_risk_pct = abs(105.0 - 98.0) / 105.0 * s2_size * 105.0 / balance

        s3_size = V7StructureStrategy.calculate_add_size(
            balance=balance, risk_per_trade=risk_per_trade,
            entry_price=110.0, new_sl=103.0,
            max_total_risk=max_total_risk, current_total_risk_pct=s1_risk_pct + s2_risk_pct,
        )
        s3_risk_pct = abs(110.0 - 103.0) / 110.0 * s3_size * 110.0 / balance

        total_risk = s1_risk_pct + s2_risk_pct + s3_risk_pct
        assert total_risk <= max_total_risk + 0.001, f"Total risk {total_risk:.4f} > {max_total_risk}"
        assert s1_size > 0 and s2_size > 0 and s3_size > 0
