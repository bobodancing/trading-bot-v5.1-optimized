"""
V5.3 Dead Zone Fix 測試

修正 1.5R ~ 2.5R 死區：
- 1.5R reduce: SL +0.5R → +1.0R，並立即啟動 ATR trailing
- 第二次減倉: 2.5R → 2.0R
"""

import pytest
import pandas as pd
from datetime import datetime, timezone, timedelta
from trader.positions import PositionManager


def _make_df_flat(n=20, close=100.0, atr=1.0, vol_ma=1000.0):
    """V53 測試用平坦 OHLC"""
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


def _make_pm_v53(side='LONG', entry=100.0, sl=90.0, size=1.0, hours_ago=1):
    """V5.3 PositionManager（is_v6_pyramid=False）"""
    pm = PositionManager(
        symbol='TEST/USDT', side=side,
        entry_price=entry, stop_loss=sl,
        position_size=size, is_v6_pyramid=False,
        initial_r=abs(entry - sl),
    )
    pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return pm


class TestV53DeadzoneFix:
    """V5.3 死區修補驗證"""

    def test_15r_reduce_sl_and_trailing_long(self):
        """LONG: 1.5R reduce -> SL=+1.0R (was +0.5R), trailing active"""
        pm = _make_pm_v53(side='LONG', entry=100, sl=90, size=1.0)
        # risk_dist=10, 1.5R -> price=115
        df = _make_df_flat(close=115.0, atr=2.0)
        d = pm.monitor(115.0, df)
        assert d['action'] == 'PARTIAL_CLOSE'
        assert d['new_sl'] == pytest.approx(110.0)  # entry + 1.0R (was +0.5R = 105)
        assert pm.is_trailing_active is True

    def test_15r_reduce_sl_and_trailing_short(self):
        """SHORT: 1.5R reduce -> SL=-1.0R, trailing active"""
        pm = _make_pm_v53(side='SHORT', entry=100, sl=110, size=1.0)
        # risk_dist=10, 1.5R -> price=85
        df = _make_df_flat(close=85.0, atr=2.0)
        d = pm.monitor(85.0, df)
        assert d['action'] == 'PARTIAL_CLOSE'
        assert d['new_sl'] == pytest.approx(90.0)  # entry - 1.0R (was -0.5R = 95)
        assert pm.is_trailing_active is True

    def test_20r_triggers_second_reduce(self):
        """2.0R triggers second reduce (was 2.5R)"""
        pm = _make_pm_v53(side='LONG', entry=100, sl=90, size=1.0)
        pm.is_first_partial = True
        pm.is_1r_protected = True
        pm.is_trailing_active = True
        # risk_dist=10, 2.0R -> price=120
        df = _make_df_flat(close=120.0, atr=2.0)
        d = pm.monitor(120.0, df)
        assert d['action'] == 'PARTIAL_CLOSE'
        assert d['new_sl'] == pytest.approx(115.0)  # entry + 1.5R

    def test_trailing_works_after_15r(self):
        """1.5R reduce 後 ATR trailing 在下一 cycle 正常推進 SL"""
        pm = _make_pm_v53(side='LONG', entry=100, sl=90, size=1.0)
        # 模擬 1.5R reduce 之後的狀態
        pm.is_first_partial = True
        pm.is_1r_protected = True
        pm.is_trailing_active = True
        pm.current_sl = 110.0       # SL at +1.0R (post 1.5R reduce)
        pm.highest_price = 118.0    # MFE reached 1.8R
        # 當前價 117 (1.7R，未到 2.0R 不觸發二減)
        df = _make_df_flat(close=117.0, atr=2.0)
        d = pm.monitor(117.0, df)
        assert d['action'] == 'HOLD'
        # ATR trailing: highest(118) - atr(2.0) * ATR_MULT(1.5) = 115
        # 115 > current_sl(110) -> trailed
        assert pm.current_sl == pytest.approx(115.0, abs=0.5)
