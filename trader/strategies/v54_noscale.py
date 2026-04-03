"""
V54 NoScale 策略

基於 V53 SOP，核心差異：
- 不加倉、不減倉（進場多少出場多少）
- 1.0R → breakeven 保護（+0.1R buffer）
- 1.5R / 2.5R → SL 移損鎖利（無 partial close）
- Structure break / stage1_timeout / ATR trailing 出場
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd
    from trader.positions import PositionManager

from trader.strategies.base import Action, TradingStrategy, DecisionDict, _apply_common_pre

logger = logging.getLogger(__name__)


class V54NoScaleStrategy(TradingStrategy):
    """V54 純移損策略 — 不加倉不減倉"""

    def __init__(self):
        self.is_breakeven_protected = False
        self.is_15r_locked = False
        self.is_25r_locked = False
        self.is_trailing_active = False

    def get_state(self) -> dict:
        return {
            'is_breakeven_protected': self.is_breakeven_protected,
            'is_15r_locked': self.is_15r_locked,
            'is_25r_locked': self.is_25r_locked,
            'is_trailing_active': self.is_trailing_active,
        }

    def load_state(self, state: dict):
        self.is_breakeven_protected = state.get('is_breakeven_protected', False)
        self.is_15r_locked = state.get('is_15r_locked', False)
        self.is_25r_locked = state.get('is_25r_locked', False)
        self.is_trailing_active = state.get('is_trailing_active', False)

    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
        **kwargs,
    ) -> DecisionDict:
        """
        V54 出場決策（優先級從高到低）：

        1. 共同前處理（SL hit / Early Stop）
        2. 結構破壞（冷卻 3 cycle，連續 2 根 1H close 確認）
        3. 時間退出（STAGE1_MAX_HOURS 且未達 1.5R）
        4. 2.5R SL 鎖利（移損至 +1.5R）
        5. 1.5R SL 鎖利（移損至 +1.0R）
        6. 1.0R breakeven（移損至 +0.1R）
        7. ATR trailing
        8. HOLD
        """
        from trader.config import ConfigV6 as Cfg
        from trader.structure import StructureAnalysis

        result: DecisionDict = {
            "action": Action.HOLD,
            "reason": "NONE",
            "new_sl": None,
            "close_pct": None,
            "add_stage": None,
        }

        # === 共同前處理（SL / Early Stop）===
        early = _apply_common_pre(pm, current_price, df_1h)
        if early is not None:
            return early

        # === 結構破壞（冷卻 3 cycle，連續 2 根 1H close 確認）===
        if df_1h is not None and len(df_1h) >= 2 and pm.monitor_count > 3:
            swings = StructureAnalysis.find_swing_points(
                df_1h, left_bars=Cfg.SWING_LEFT_BARS, right_bars=Cfg.SWING_RIGHT_BARS
            )
            close_curr = df_1h['close'].iloc[-1]
            close_prev = df_1h['close'].iloc[-2]
            if pm.side == 'LONG' and swings['last_swing_low'] is not None:
                threshold = swings['last_swing_low'] * (1 - Cfg.STRUCTURE_BREAK_TOLERANCE)
                if close_prev < threshold and close_curr < threshold:
                    pm.exit_reason = 'v54_structure_break'
                    return {**result, "action": Action.CLOSE}
            if pm.side == 'SHORT' and swings['last_swing_high'] is not None:
                threshold = swings['last_swing_high'] * (1 + Cfg.STRUCTURE_BREAK_TOLERANCE)
                if close_prev > threshold and close_curr > threshold:
                    pm.exit_reason = 'v54_structure_break'
                    return {**result, "action": Action.CLOSE}

        r_unit = pm.risk_dist
        if r_unit == 0:
            return result

        if pm.side == 'LONG':
            current_r = (current_price - pm.avg_entry) / r_unit
        else:
            current_r = (pm.avg_entry - current_price) / r_unit

        # === 時間退出 ===
        hours_held = (datetime.now(timezone.utc) - pm.entry_time).total_seconds() / 3600
        if hours_held >= Cfg.STAGE1_MAX_HOURS and not self.is_15r_locked:
            logger.warning(
                f"[V54] {pm.symbol} Time exit: "
                f"{hours_held:.1f}h >= {Cfg.STAGE1_MAX_HOURS}h"
            )
            pm.exit_reason = 'stage1_timeout'
            return {**result, "action": Action.CLOSE, "reason": "TIME_EXIT"}

        # === 2.5R SL 鎖利（移損至 +1.5R）===
        if not self.is_25r_locked and current_r >= 2.0:
            self.is_25r_locked = True
            self.is_15r_locked = True
            self.is_breakeven_protected = True
            self.is_trailing_active = True
            if pm.side == 'LONG':
                new_sl = pm.avg_entry + (r_unit * 1.5)
            else:
                new_sl = pm.avg_entry - (r_unit * 1.5)
            pm.current_sl = new_sl
            result = {**result, "reason": "V54_LOCK_25R", "new_sl": new_sl}

        # === 1.5R SL 鎖利（移損至 +1.0R）===
        elif not self.is_15r_locked and current_r >= 1.5:
            self.is_15r_locked = True
            self.is_breakeven_protected = True
            self.is_trailing_active = True
            if pm.side == 'LONG':
                new_sl = pm.avg_entry + (r_unit * 1.0)
            else:
                new_sl = pm.avg_entry - (r_unit * 1.0)
            pm.current_sl = new_sl
            result = {**result, "reason": "V54_LOCK_15R", "new_sl": new_sl}

        # === 1.0R breakeven 保護（+0.1R buffer）===
        elif not self.is_breakeven_protected and current_r >= 1.0:
            self.is_breakeven_protected = True
            if pm.side == 'LONG':
                new_sl = pm.avg_entry + (r_unit * 0.1)
            else:
                new_sl = pm.avg_entry - (r_unit * 0.1)
            pm.current_sl = new_sl
            result = {**result, "reason": "V54_BREAKEVEN", "new_sl": new_sl}

        # === ATR trailing ===
        if self.is_trailing_active and pm.atr is not None:
            trailing_dist = pm.atr * Cfg.APLUS_TRAILING_ATR_MULT
            if pm.side == 'LONG':
                new_sl = pm.highest_price - trailing_dist
                if new_sl > pm.current_sl:
                    pm.current_sl = new_sl
                    result = {**result, "new_sl": new_sl}
            else:
                new_sl = pm.lowest_price + trailing_dist
                if new_sl < pm.current_sl:
                    pm.current_sl = new_sl
                    result = {**result, "new_sl": new_sl}

        return result


# 自動註冊至 StrategyFactory
from trader.strategies.base import StrategyFactory
StrategyFactory.register("v54_noscale", V54NoScaleStrategy)
