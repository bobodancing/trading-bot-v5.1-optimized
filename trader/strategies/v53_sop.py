"""
V7 P2: V5.3 SOP 策略實作

邏輯完整搬移自 positions.py _get_exit_decision() V5.3 路徑：
- 結構破壞（冷卻 3 cycle）
- 時間退出（TIME_EXIT）
- 2.0R 減倉（PARTIAL_CLOSE close_pct）
- 1.5R 減倉（PARTIAL_CLOSE close_pct）
- 1.0R 移損（V53_1R_PROTECT）
- ATR trailing

V53 內部狀態（is_1r_protected 等）獨立於 PositionManager，透過 get_state/load_state 持久化。
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


class V53SopStrategy(TradingStrategy):
    """V5.3 統一出場 SOP 策略"""

    def __init__(self):
        self.is_1r_protected = False
        self.is_first_partial = False
        self.is_second_partial = False
        self.is_trailing_active = False

    def get_state(self) -> dict:
        return {
            'is_1r_protected': self.is_1r_protected,
            'is_first_partial': self.is_first_partial,
            'is_second_partial': self.is_second_partial,
            'is_trailing_active': self.is_trailing_active,
        }

    def load_state(self, state: dict):
        self.is_1r_protected = state.get('is_1r_protected', False)
        self.is_first_partial = state.get('is_first_partial', False)
        self.is_second_partial = state.get('is_second_partial', False)
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
        V5.3 出場決策：

        優先級（從高到低）：
        1. 共同前處理（SL hit / Early Stop）
        2. 結構破壞（冷卻 3 cycle，連續 2 根 1H close 確認）
        3. 時間退出（STAGE1_MAX_HOURS 且未觸發 first partial）
        4. 2.0R 減倉
        5. 1.5R 減倉
        6. 1.0R 移損
        7. ATR trailing 移損
        8. HOLD（持倉中）
        """
        from trader.config import Config as Cfg
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
                    pm.exit_reason = 'v53_structure_break'
                    return {**result, "action": Action.CLOSE}
            if pm.side == 'SHORT' and swings['last_swing_high'] is not None:
                threshold = swings['last_swing_high'] * (1 + Cfg.STRUCTURE_BREAK_TOLERANCE)
                if close_prev > threshold and close_curr > threshold:
                    pm.exit_reason = 'v53_structure_break'
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
        if hours_held >= Cfg.STAGE1_MAX_HOURS and not self.is_first_partial:
            logger.warning(
                f"[V53] {pm.symbol} Time exit: "
                f"{hours_held:.1f}h >= {Cfg.STAGE1_MAX_HOURS}h"
            )
            pm.exit_reason = 'stage1_timeout'
            return {**result, "action": Action.CLOSE, "reason": "TIME_EXIT"}

        # === 2.0R 減倉 ===
        if not self.is_second_partial and current_r >= 2.0:
            self.is_second_partial = True
            self.is_first_partial = True
            self.is_1r_protected = True
            if pm.side == 'LONG':
                new_sl = pm.avg_entry + (r_unit * 1.5)
            else:
                new_sl = pm.avg_entry - (r_unit * 1.5)
            pm.current_sl = new_sl
            self.is_trailing_active = True
            return {
                **result,
                "action": Action.PARTIAL_CLOSE,
                "reason": "V53_REDUCE_20R",
                "new_sl": new_sl,
                "close_pct": Cfg.SECOND_PARTIAL_PCT / 100.0,
            }

        # === 1.5R 減倉 ===
        elif not self.is_first_partial and current_r >= 1.5:
            self.is_first_partial = True
            self.is_1r_protected = True
            if pm.side == 'LONG':
                new_sl = pm.avg_entry + (r_unit * 1.0)
            else:
                new_sl = pm.avg_entry - (r_unit * 1.0)
            pm.current_sl = new_sl
            self.is_trailing_active = True
            return {
                **result,
                "action": Action.PARTIAL_CLOSE,
                "reason": "V53_REDUCE_15R",
                "new_sl": new_sl,
                "close_pct": Cfg.FIRST_PARTIAL_PCT / 100.0,
            }

        # === 1.0R 移損 ===
        elif not self.is_1r_protected and current_r >= 1.0:
            self.is_1r_protected = True
            if pm.side == 'LONG':
                new_sl = pm.avg_entry + (r_unit * 0.3)
            else:
                new_sl = pm.avg_entry - (r_unit * 0.3)
            pm.current_sl = new_sl
            result = {**result, "reason": "V53_1R_PROTECT", "new_sl": new_sl}

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
StrategyFactory.register("v53_sop", V53SopStrategy)
