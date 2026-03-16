"""
Stage 2 診斷測試

測試：
1. V6 timeout 用 V6_STAGE1_MAX_HOURS (36h) 而非 STAGE1_MAX_HOURS (24h)
2. Stage 2 trigger 診斷 logging
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
import pandas as pd

from trader.strategies.v6_pyramid import V6PyramidStrategy
from trader.config import ConfigV6 as Cfg


def make_pm(entry_time_offset_hours: float, neckline: float = 105.0,
            side: str = 'LONG', avg_entry: float = 100.0,
            stage: int = 1):
    """建立測試用 PositionManager mock"""
    pm = MagicMock()
    pm.stage = stage
    pm.side = side
    pm.avg_entry = avg_entry
    pm.risk_dist = 2.0        # $2 per 1R
    pm.initial_r = 10.0       # $10 profit target
    pm.current_sl = 98.0 if side == 'LONG' else 102.0
    pm.highest_price = avg_entry + 1.0 if side == 'LONG' else avg_entry
    pm.lowest_price = avg_entry - 1.0 if side == 'SHORT' else avg_entry
    pm.neckline = neckline
    pm.atr = 1.0
    pm.is_v6_pyramid = True
    pm.symbol = 'TEST/USDT'
    pm.monitor_count = 0
    pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=entry_time_offset_hours)
    # V53 timeout 條件：not pm.is_first_partial — 需明確設為 False 才能觸發
    pm.is_first_partial = False
    pm.is_second_partial = False
    pm.is_1r_protected = False
    pm.is_trailing_active = False
    return pm


def make_df_1h(close: float = 101.0, vol_ratio: float = 1.0):
    """建立測試用 1H DataFrame"""
    vol_ma = 1000.0
    return pd.DataFrame([{
        'open': close - 0.5,
        'high': close + 1.0,
        'low': close - 1.0,
        'close': close,
        'volume': vol_ma * vol_ratio,
        'vol_ma': vol_ma,
        'atr': 1.0,
    }])


class TestV6TimeoutConfig:
    """確認 V6 使用 V6_STAGE1_MAX_HOURS (36h)，而非 STAGE1_MAX_HOURS (24h)"""

    def test_v6_does_not_timeout_at_24h(self):
        """V6 在 24h 不應 timeout（V6_STAGE1_MAX_HOURS=36h）"""
        strategy = V6PyramidStrategy()
        pm = make_pm(entry_time_offset_hours=24.5)   # 超過 24h，但未超過 36h
        df = make_df_1h(close=101.0)  # 稍微獲利，不觸發 pullback

        # 確保 highest_price 沒超過 pullback 門檻
        pm.highest_price = 100.3   # MFE = 0.15R < MIN_MFE_R=0.3，不觸發 pullback

        with patch('trader.strategies.base._apply_common_pre', return_value=None):
            decision = strategy.get_decision(pm, 100.3, df)

        # 24.5h 不該 timeout（V6 應用 36h）
        assert decision['action'] != 'CLOSE' or decision.get('reason') != 'TIME_EXIT', \
            f"V6 不應在 24.5h timeout，但 action={decision['action']}, reason={decision.get('reason')}"

    def test_v6_timeouts_at_36h(self):
        """V6 在 36h 後應 timeout"""
        strategy = V6PyramidStrategy()
        pm = make_pm(entry_time_offset_hours=36.5)
        pm.highest_price = 100.1   # MFE 極小，不觸發 pullback

        df = make_df_1h(close=100.1)

        with patch('trader.strategies.base._apply_common_pre', return_value=None):
            decision = strategy.get_decision(pm, 100.1, df)

        assert decision['action'] == 'CLOSE'
        assert pm.exit_reason == 'stage1_timeout'

    def test_v53_uses_24h_timeout(self):
        """V53 仍用 STAGE1_MAX_HOURS (24h)"""
        from trader.strategies.v53_sop import V53SopStrategy
        strategy = V53SopStrategy()
        pm = make_pm(entry_time_offset_hours=24.5)
        pm.is_v6_pyramid = False
        pm.highest_price = 100.1

        df = make_df_1h(close=100.1)

        with patch('trader.strategies.base._apply_common_pre', return_value=None):
            decision = strategy.get_decision(pm, 100.1, df)

        # V53 在 24.5h 應該 timeout
        assert decision['action'] == 'CLOSE'


class TestStage2TriggerDiagnosis:
    """Stage 2 trigger 條件診斷"""

    def test_stage2_triggers_when_all_conditions_met(self):
        """基本 happy path：neckline 突破 + 1.2x 放量 → STAGE2_TRIGGER"""
        strategy = V6PyramidStrategy()
        pm = make_pm(entry_time_offset_hours=1.0, neckline=105.0)
        pm.highest_price = 100.1  # MFE 小，不觸發 pullback
        pm.check_stage2_trigger = MagicMock(return_value=True)

        df = make_df_1h(close=106.0, vol_ratio=1.5)  # 突破 neckline + 1.5x 量

        with patch('trader.strategies.base._apply_common_pre', return_value=None):
            decision = strategy.get_decision(pm, 106.0, df)

        assert decision['action'] == 'ADD'
        assert decision['add_stage'] == 2

    def test_stage2_not_triggered_when_volume_insufficient(self):
        """放量不足時 Stage 2 不觸發"""
        strategy = V6PyramidStrategy()
        pm = make_pm(entry_time_offset_hours=1.0, neckline=105.0)
        pm.highest_price = 100.1
        # 明確模擬 check_stage2_trigger 回傳 False（縮量 0.8x < 1.2x 不達標）
        pm.check_stage2_trigger = MagicMock(return_value=False)

        df = make_df_1h(close=106.0, vol_ratio=0.8)  # 縮量

        with patch('trader.strategies.base._apply_common_pre', return_value=None):
            decision = strategy.get_decision(pm, 106.0, df)

        assert decision['action'] != 'ADD'

    def test_profit_pullback_blocks_stage2_when_mfe_high(self):
        """驗證：MFE 夠高時 profit_pullback 先於 Stage 2 trigger 觸發的情境"""
        strategy = V6PyramidStrategy()
        pm = make_pm(entry_time_offset_hours=2.0, neckline=105.0)

        # MFE = 1.0R (price went to 102.0 from 100.0 with risk_dist=2.0)
        pm.highest_price = 102.0   # MFE = 2.0 = 1.0R
        current_price = 101.1      # pullback = 0.9 / 2.0 = 45% < 55%，不觸發

        df = make_df_1h(close=current_price, vol_ratio=1.5)
        pm.check_stage2_trigger = MagicMock(return_value=True)

        with patch('trader.strategies.base._apply_common_pre', return_value=None):
            decision = strategy.get_decision(pm, current_price, df)

        # 45% pullback < 55% threshold → profit_pullback 不觸發 → Stage 2 應觸發
        assert decision['action'] == 'ADD'
        assert decision['add_stage'] == 2
