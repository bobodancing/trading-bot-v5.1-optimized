"""
V54 NoScale 策略測試

核心驗證：
- 不加倉、不減倉（action 永不為 PARTIAL_CLOSE / ADD）
- 1.0R → breakeven +0.1R（非 V53 的 +0.3R）
- 1.5R / 2.5R → SL 移損鎖利（無 partial close）
- Structure break / timeout / ATR trailing
- State persistence round-trip
"""

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from trader.positions import PositionManager
from trader.strategies.base import Action


def _make_df(n=20, close=100.0, atr=1.0, vol_ma=1000.0):
    """V54 測試用平坦 OHLC"""
    return pd.DataFrame({
        'open':     [close] * n,
        'high':     [close + 1.0] * n,
        'low':      [close - 1.0] * n,
        'close':    [close] * n,
        'volume':   [vol_ma] * n,
        'vol_ma':   [vol_ma] * n,
        'atr':      [atr] * n,
        'ema_slow': [close] * n,
        'ema_fast': [close] * n,
    })


def _make_pm(side='LONG', entry=100.0, sl=90.0, size=1.0, hours_ago=1):
    """V54 PositionManager"""
    pm = PositionManager(
        symbol='TEST/USDT', side=side,
        entry_price=entry, stop_loss=sl,
        position_size=size, strategy_name='v54_noscale',
        initial_r=abs(entry - sl),
    )
    pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return pm


class TestV54BreakevenProtection:
    """1.0R → breakeven +0.1R"""

    def test_1r_breakeven_long(self):
        """LONG: 1.0R → SL = entry + 0.1R"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        # risk_dist=10, 1.0R → price=110
        df = _make_df(close=110.0, atr=2.0)
        d = pm.monitor(110.0, df)
        assert d['new_sl'] == pytest.approx(101.0)  # 100 + 10*0.1
        assert d['action'] != 'PARTIAL_CLOSE'

    def test_1r_breakeven_short(self):
        """SHORT: 1.0R → SL = entry - 0.1R"""
        pm = _make_pm(side='SHORT', entry=100, sl=110)
        # risk_dist=10, 1.0R → price=90
        df = _make_df(close=90.0, atr=2.0)
        d = pm.monitor(90.0, df)
        assert d['new_sl'] == pytest.approx(99.0)  # 100 - 10*0.1
        assert d['action'] != 'PARTIAL_CLOSE'

    def test_buffer_is_01r_not_03r(self):
        """確認是 +0.1R（V54）不是 +0.3R（V53）"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=110.0, atr=2.0)
        d = pm.monitor(110.0, df)
        assert d['new_sl'] != pytest.approx(103.0)  # V53 would be +0.3R = 103
        assert d['new_sl'] == pytest.approx(101.0)  # V54 is +0.1R = 101

    def test_below_1r_no_protect(self):
        """current_r < 1.0 → no SL change"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=105.0, atr=2.0)
        d = pm.monitor(105.0, df)
        assert d['new_sl'] is None


class TestV54SLLocking:
    """1.5R / 2.5R → SL 移損鎖利（無 partial close）"""

    def test_15r_sl_lock_long(self):
        """LONG: 1.5R → SL ≥ entry + 1.0R（ATR trailing 可能更高）"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        # atr=20 → trailing dist=30, wider than lock → lock SL wins
        df = _make_df(close=115.0, atr=20.0)
        d = pm.monitor(115.0, df)
        assert d['new_sl'] == pytest.approx(110.0)  # 100 + 10*1.0
        assert d['action'] != 'PARTIAL_CLOSE'

    def test_15r_sl_lock_short(self):
        """SHORT: 1.5R → SL ≤ entry - 1.0R"""
        pm = _make_pm(side='SHORT', entry=100, sl=110)
        df = _make_df(close=85.0, atr=20.0)
        d = pm.monitor(85.0, df)
        assert d['new_sl'] == pytest.approx(90.0)  # 100 - 10*1.0
        assert d['action'] != 'PARTIAL_CLOSE'

    def test_25r_sl_lock_long(self):
        """LONG: 2.5R → SL ≥ entry + 1.5R"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=120.0, atr=20.0)
        d = pm.monitor(120.0, df)
        assert d['new_sl'] == pytest.approx(115.0)  # 100 + 10*1.5
        assert d['action'] != 'PARTIAL_CLOSE'

    def test_25r_sl_lock_short(self):
        """SHORT: 2.5R → SL ≤ entry - 1.5R"""
        pm = _make_pm(side='SHORT', entry=100, sl=110)
        df = _make_df(close=80.0, atr=20.0)
        d = pm.monitor(80.0, df)
        assert d['new_sl'] == pytest.approx(85.0)  # 100 - 10*1.5
        assert d['action'] != 'PARTIAL_CLOSE'


class TestV54NoPartialClose:
    """action 永不為 PARTIAL_CLOSE"""

    def test_15r_returns_hold_not_partial(self):
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=115.0, atr=2.0)
        d = pm.monitor(115.0, df)
        assert d['action'] != 'PARTIAL_CLOSE'
        assert d['close_pct'] is None

    def test_25r_returns_hold_not_partial(self):
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=120.0, atr=2.0)
        d = pm.monitor(120.0, df)
        assert d['action'] != 'PARTIAL_CLOSE'
        assert d['close_pct'] is None

    def test_no_add_action(self):
        """V54 never returns ADD"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=115.0, atr=2.0)
        d = pm.monitor(115.0, df)
        assert d['action'] != 'ADD'
        assert d['add_stage'] is None


class TestV54TrailingStop:
    """ATR trailing 棘輪"""

    def test_trailing_activates_at_15r(self):
        """1.5R 觸發 trailing"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=115.0, atr=2.0)
        pm.monitor(115.0, df)
        assert pm.strategy.is_trailing_active is True

    def test_atr_trailing_ratchets_sl(self):
        """ATR trailing 只往有利方向移動"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        # 先觸發 1.5R lock
        df1 = _make_df(close=115.0, atr=2.0)
        pm.monitor(115.0, df1)
        sl_after_lock = pm.current_sl

        # 價格繼續上漲 → trailing 應該拉高 SL
        pm.highest_price = 120.0
        pm.atr = 2.0
        df2 = _make_df(close=118.0, atr=2.0)
        pm.monitor(118.0, df2)
        assert pm.current_sl >= sl_after_lock


class TestV54TimeExit:
    """Stage1 timeout"""

    def test_timeout_before_15r(self):
        """未達 1.5R + 超時 → CLOSE"""
        from trader.config import ConfigV6 as Cfg
        pm = _make_pm(side='LONG', entry=100, sl=90, hours_ago=Cfg.STAGE1_MAX_HOURS + 1)
        df = _make_df(close=105.0, atr=2.0)
        d = pm.monitor(105.0, df)
        assert d['action'] == 'CLOSE'

    def test_no_timeout_after_15r(self):
        """已達 1.5R lock → 不觸發 timeout"""
        from trader.config import ConfigV6 as Cfg
        pm = _make_pm(side='LONG', entry=100, sl=90, hours_ago=1)
        df = _make_df(close=115.0, atr=20.0)  # wide ATR so trailing doesn't tighten too much
        pm.monitor(115.0, df)  # triggers 1.5R lock

        # Now simulate timeout — price well above SL
        pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=Cfg.STAGE1_MAX_HOURS + 1)
        df2 = _make_df(close=115.0, atr=20.0)
        d = pm.monitor(115.0, df2)
        assert d['action'] != 'CLOSE'


class TestV54Persistence:
    """State persistence round-trip"""

    def test_get_state_returns_4_fields(self):
        from trader.strategies.v54_noscale import V54NoScaleStrategy
        s = V54NoScaleStrategy()
        state = s.get_state()
        assert set(state.keys()) == {
            'is_breakeven_protected', 'is_15r_locked',
            'is_25r_locked', 'is_trailing_active',
        }

    def test_load_state_restores(self):
        from trader.strategies.v54_noscale import V54NoScaleStrategy
        s = V54NoScaleStrategy()
        s.load_state({
            'is_breakeven_protected': True,
            'is_15r_locked': True,
            'is_25r_locked': False,
            'is_trailing_active': True,
        })
        assert s.is_breakeven_protected is True
        assert s.is_15r_locked is True
        assert s.is_25r_locked is False
        assert s.is_trailing_active is True

    def test_pm_to_dict_from_dict(self):
        """PositionManager serialization round-trip"""
        pm = _make_pm(side='LONG', entry=100, sl=90)
        df = _make_df(close=115.0, atr=2.0)
        pm.monitor(115.0, df)  # triggers 1.5R lock

        data = pm.to_dict()
        assert data['strategy_name'] == 'v54_noscale'

        pm2 = PositionManager.from_dict(data)
        assert pm2.strategy_name == 'v54_noscale'
        assert pm2.strategy.is_15r_locked is True
        assert pm2.strategy.is_breakeven_protected is True
