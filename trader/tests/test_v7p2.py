"""Test: V7 P2 — Strategy Pattern 重構驗收測試"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import MagicMock
import pandas as pd

from trader.positions import PositionManager
from trader.strategies import StrategyFactory, TradingStrategy, V6PyramidStrategy, V53SopStrategy


# ──────────────────────────────────────────────
# 輔助：建立最小 PositionManager
# ──────────────────────────────────────────────

def _make_pm(is_v6_pyramid=True, **kwargs) -> PositionManager:
    defaults = dict(
        symbol='BTC/USDT',
        side='LONG',
        entry_price=50000.0,
        stop_loss=48000.0,
        position_size=0.01,
        is_v6_pyramid=is_v6_pyramid,
    )
    defaults.update(kwargs)
    return PositionManager(**defaults)


def _make_df_1h_flat(n=20, close=50500.0) -> pd.DataFrame:
    return pd.DataFrame({
        'open':     [close] * n,
        'high':     [close + 100.0] * n,
        'low':      [close - 100.0] * n,
        'close':    [close] * n,
        'volume':   [1000.0] * n,
        'vol_ma':   [1000.0] * n,
        'atr':      [200.0] * n,
        'ema_slow': [close] * n,
        'ema_fast': [close] * n,
    })


# ──────────────────────────────────────────────
# 1. StrategyFactory — V6
# ──────────────────────────────────────────────

class TestStrategyFactory:

    def test_strategy_factory_v6(self):
        """StrategyFactory.create_strategy('v6') 應回傳 V6PyramidStrategy instance"""
        strategy = StrategyFactory.create_strategy("v6")
        assert isinstance(strategy, V6PyramidStrategy)

    def test_strategy_factory_v53(self):
        """StrategyFactory.create_strategy('v53') 應回傳 V53SopStrategy instance"""
        strategy = StrategyFactory.create_strategy("v53")
        assert isinstance(strategy, V53SopStrategy)

    def test_strategy_factory_invalid(self):
        """不合法名稱應 raise ValueError"""
        with pytest.raises(ValueError):
            StrategyFactory.create_strategy("unknown_strategy_xyz")

    def test_strategy_factory_case_insensitive(self):
        """名稱大小寫不影響結果"""
        assert isinstance(StrategyFactory.create_strategy("V6"), V6PyramidStrategy)
        assert isinstance(StrategyFactory.create_strategy("V6_PYRAMID"), V6PyramidStrategy)
        assert isinstance(StrategyFactory.create_strategy("V53"), V53SopStrategy)
        assert isinstance(StrategyFactory.create_strategy("V5.3"), V53SopStrategy)


# ──────────────────────────────────────────────
# 2. PositionManager strategy injection
# ──────────────────────────────────────────────

class TestPositionManagerStrategyInjection:

    def test_position_manager_strategy_injection_v6(self):
        """建立 PositionManager(is_v6_pyramid=True)，pm.strategy 應是 V6PyramidStrategy"""
        pm = _make_pm(is_v6_pyramid=True)
        assert isinstance(pm.strategy, V6PyramidStrategy)

    def test_position_manager_strategy_injection_v53(self):
        """建立 PositionManager(is_v6_pyramid=False)，pm.strategy 應是 V53SopStrategy"""
        pm = _make_pm(is_v6_pyramid=False)
        assert isinstance(pm.strategy, V53SopStrategy)

    def test_position_manager_explicit_strategy_injection(self):
        """明確傳入 strategy 應覆蓋自動建立的 strategy"""
        custom_strategy = V53SopStrategy()
        pm = _make_pm(is_v6_pyramid=True, strategy=custom_strategy)
        assert pm.strategy is custom_strategy
        assert isinstance(pm.strategy, V53SopStrategy)


# ──────────────────────────────────────────────
# 3. PositionManager strategy serialization
# ──────────────────────────────────────────────

class TestPositionManagerStrategySerialization:

    def test_to_dict_contains_strategy_type(self):
        """to_dict() 應含 strategy_type 欄位"""
        pm_v6 = _make_pm(is_v6_pyramid=True)
        pm_v53 = _make_pm(is_v6_pyramid=False)
        assert pm_v6.to_dict()['strategy_type'] == 'v6'
        assert pm_v53.to_dict()['strategy_type'] == 'v53'

    def test_from_dict_restores_strategy(self):
        """from_dict() 應正確還原 strategy 型別"""
        pm_v6 = _make_pm(is_v6_pyramid=True)
        pm_v53 = _make_pm(is_v6_pyramid=False)

        restored_v6 = PositionManager.from_dict(pm_v6.to_dict())
        restored_v53 = PositionManager.from_dict(pm_v53.to_dict())

        assert isinstance(restored_v6.strategy, V6PyramidStrategy)
        assert isinstance(restored_v53.strategy, V53SopStrategy)

    def test_from_dict_backward_compat_no_strategy_type(self):
        """舊格式 dict（不含 strategy_type）應依 is_v6_pyramid 自動判斷"""
        pm = _make_pm(is_v6_pyramid=True)
        d = pm.to_dict()
        d.pop('strategy_type', None)

        restored = PositionManager.from_dict(d)
        assert isinstance(restored.strategy, V6PyramidStrategy)


# ──────────────────────────────────────────────
# 4. Deprecated monitor_v6 backward compat
# ──────────────────────────────────────────────

class TestDeprecatedMonitorBackwardCompat:

    def test_deprecated_monitor_v6_backward_compat(self):
        """pm.monitor_v6() 仍可呼叫，應回傳 str"""
        pm = _make_pm(is_v6_pyramid=True)
        df_1h = _make_df_1h_flat()
        result = pm.monitor_v6(50500.0, df_1h)
        assert isinstance(result, str)

    def test_deprecated_monitor_v53_backward_compat(self):
        """pm.monitor_v53() 仍可呼叫，應回傳 str"""
        pm = _make_pm(is_v6_pyramid=False)
        df_1h = _make_df_1h_flat()
        result = pm.monitor_v53(50500.0, df_1h)
        assert isinstance(result, str)


# ──────────────────────────────────────────────
# 5. monitor() returns Dict
# ──────────────────────────────────────────────

class TestMonitorReturnsDict:

    def test_monitor_returns_dict(self):
        """pm.monitor() 應回傳 dict，含 action / reason / new_sl / close_pct"""
        pm = _make_pm(is_v6_pyramid=True)
        df_1h = _make_df_1h_flat()
        decision = pm.monitor(50500.0, df_1h)

        assert isinstance(decision, dict)
        assert 'action' in decision
        assert 'reason' in decision
        assert 'new_sl' in decision
        assert 'close_pct' in decision

    def test_monitor_v53_returns_dict(self):
        """V53 pm.monitor() 也應回傳 dict"""
        pm = _make_pm(is_v6_pyramid=False)
        df_1h = _make_df_1h_flat()
        decision = pm.monitor(50500.0, df_1h)

        assert isinstance(decision, dict)
        assert 'action' in decision

    def test_monitor_action_is_string(self):
        """decision['action'] 應是 str"""
        pm = _make_pm(is_v6_pyramid=True)
        df_1h = _make_df_1h_flat()
        decision = pm.monitor(50500.0, df_1h)
        assert isinstance(decision['action'], str)

    def test_monitor_closed_pm_returns_dict(self):
        """is_closed=True 的 pm.monitor() 也應回傳 dict"""
        pm = _make_pm(is_v6_pyramid=True)
        pm.is_closed = True
        df_1h = _make_df_1h_flat()
        decision = pm.monitor(50500.0, df_1h)
        assert isinstance(decision, dict)
        assert 'action' in decision
