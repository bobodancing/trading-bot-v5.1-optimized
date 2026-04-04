"""
V7 結構驅動加倉策略

三階段狀態機：
- STAGE 1 (建倉) → 加倉條件 → STAGE 2 (1st加倉) → 加倉條件 → STAGE 3 (2nd加倉)
- 加倉觸發：Swing Point 確認 + 順勢 K 線(body/range>=0.3) + 量能確認（三條件 AND）
- SL 棘輪：只能往有利方向移動
- 出場：SL hit / 反向 2B / Stage 1 超時 / 結構 Trailing
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    import pandas as pd
    from trader.positions import PositionManager

from trader.strategies.base import Action, TradingStrategy, DecisionDict, _apply_common_pre

logger = logging.getLogger(__name__)

# body/range minimum ratio for candle confirmation
MIN_BODY_RATIO = 0.3


class V7StructureStrategy(TradingStrategy):
    """V7 結構驅動三段加倉策略"""

    def __init__(self):
        self.last_structure_swing: Optional[float] = None
        self.add_trigger_swings: List[float] = []

    def get_state(self) -> dict:
        return {
            'last_structure_swing': self.last_structure_swing,
            'add_trigger_swings': self.add_trigger_swings,
        }

    def load_state(self, state: dict):
        self.last_structure_swing = state.get('last_structure_swing')
        self.add_trigger_swings = state.get('add_trigger_swings', [])

    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
        **kwargs,
    ) -> DecisionDict:
        from trader.config import Config as Cfg

        result: DecisionDict = {
            "action": Action.HOLD,
            "reason": "NONE",
            "new_sl": None,
            "close_pct": None,
            "add_stage": None,
        }

        # 1. 共同前處理（SL / Early Stop）
        early = _apply_common_pre(pm, current_price, df_1h)
        if early is not None:
            return early

        # 2. 反向 2B
        if df_1h is not None and len(df_1h) >= 2:
            reverse_2b = self._check_reverse_2b(pm, df_1h, Cfg)
            if reverse_2b:
                return reverse_2b

        # 3. Stage 1 超時
        if pm.stage == 1:
            hours_held = (datetime.now(timezone.utc) - pm.entry_time).total_seconds() / 3600
            v7_timeout = getattr(Cfg, 'V7_STAGE1_MAX_HOURS', Cfg.V6_STAGE1_MAX_HOURS)
            if hours_held >= v7_timeout:
                logger.warning(
                    f"[V7] {pm.symbol} Stage 1 timeout: {hours_held:.1f}h >= {v7_timeout}h"
                )
                pm.exit_reason = 'stage1_timeout'
                return {**result, "action": Action.CLOSE, "reason": "TIME_EXIT"}

        # 4. 加倉觸發
        if pm.stage < 3 and df_1h is not None and len(df_1h) >= 2:
            add_result = self._check_add_trigger(pm, current_price, df_1h, Cfg)
            if add_result is not None:
                return add_result

        # 5. 結構 Trailing SL（Stage 2/3）
        if pm.stage >= 2 and df_1h is not None and len(df_1h) > 0:
            trailing = self._structure_trailing_sl(pm, df_1h, Cfg)
            if trailing is not None:
                return {**result, "action": Action.UPDATE_SL, "reason": "V7_STRUCTURE_TRAIL_SL", "new_sl": trailing}

        return result

    def _check_add_trigger(self, pm, current_price, df_1h, Cfg) -> Optional[DecisionDict]:
        """三條件 AND 加倉觸發"""
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
            df_1h, Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
        )
        stage_vol_mult = getattr(Cfg, 'V7_STAGE_VOLUME_MULT', 1.0)
        curr = df_1h.iloc[-1]
        vol_ma = curr.get('vol_ma', 0)

        target_stage = pm.stage + 1
        swing_price = None

        if pm.side == 'LONG':
            swing_lows = swings.get('swing_lows', [])
            if len(swing_lows) < 2:
                return None
            latest = swing_lows[-1][1]
            prev = swing_lows[-2][1]
            if latest <= prev:
                return None
            if latest <= pm.current_sl:
                return None

            # Condition 2: bullish candle + body ratio
            if curr['close'] <= curr['open']:
                return None
            body = abs(curr['close'] - curr['open'])
            candle_range = curr['high'] - curr['low']
            if candle_range <= 0 or body / candle_range < MIN_BODY_RATIO:
                return None

            # Condition 3: volume
            if vol_ma <= 0 or curr['volume'] < vol_ma * stage_vol_mult:
                return None

            swing_price = latest

        elif pm.side == 'SHORT':
            swing_highs = swings.get('swing_highs', [])
            if len(swing_highs) < 2:
                return None
            latest = swing_highs[-1][1]
            prev = swing_highs[-2][1]
            if latest >= prev:
                return None
            if latest >= pm.current_sl:
                return None

            # Condition 2: bearish candle + body ratio
            if curr['close'] >= curr['open']:
                return None
            body = abs(curr['close'] - curr['open'])
            candle_range = curr['high'] - curr['low']
            if candle_range <= 0 or body / candle_range < MIN_BODY_RATIO:
                return None

            # Condition 3: volume
            if vol_ma <= 0 or curr['volume'] < vol_ma * stage_vol_mult:
                return None

            swing_price = latest

        if swing_price is None:
            return None

        # 計算新 SL
        atr_buffer = pm.atr * Cfg.SL_ATR_BUFFER if pm.atr else 0
        if pm.side == 'LONG':
            new_sl = swing_price - atr_buffer
            # Stage 2→3 加倉時 SL 至少在 breakeven（Stage 1→2 不限制，給回調空間）
            if target_stage >= 3 and pm.avg_entry and new_sl < pm.avg_entry:
                new_sl = pm.avg_entry
        else:
            new_sl = swing_price + atr_buffer
            # Stage 2→3 加倉時 SL 至少在 breakeven（Stage 1→2 不限制，給回調空間）
            if target_stage >= 3 and pm.avg_entry and new_sl > pm.avg_entry:
                new_sl = pm.avg_entry

        self.last_structure_swing = swing_price
        self.add_trigger_swings.append(swing_price)

        logger.info(
            f"[V7] {pm.symbol} Stage {pm.stage}→{target_stage} ADD triggered: "
            f"swing={swing_price:.2f}, new_sl={new_sl:.2f}"
        )

        return {
            "action": Action.ADD,
            "reason": "V7_STRUCTURE_ADD",
            "new_sl": new_sl,
            "close_pct": None,
            "add_stage": target_stage,
        }

    def _check_reverse_2b(self, pm, df_1h, Cfg) -> Optional[DecisionDict]:
        """反向 2B 檢測（從 V6 移植，穿透深度 + 下根確認）"""
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
            df_1h, Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
        )
        prev = df_1h.iloc[-2]
        curr = df_1h.iloc[-1]
        atr = pm.atr if pm.atr and pm.atr > 0 else 0
        min_depth = atr * Cfg.REVERSE_2B_MIN_FAKEOUT_ATR if atr > 0 else 0

        close_result: DecisionDict = {
            "action": Action.CLOSE, "reason": "REVERSE_2B",
            "new_sl": None, "close_pct": None, "add_stage": None,
        }

        if pm.side == 'LONG' and swings.get('last_swing_high') is not None:
            sh = swings['last_swing_high']
            fakeout = prev['high'] - sh
            if (prev['high'] > sh and prev['close'] < sh
                    and fakeout >= min_depth and curr['close'] < sh):
                depth_atr = round(fakeout / atr, 3) if atr > 0 else 0.0
                logger.warning(
                    f"[V7] {pm.symbol} Reverse 2B (bearish): depth={depth_atr:.2f}x ATR"
                )
                pm.exit_reason = 'reverse_2b'
                pm.reverse_2b_depth_atr = depth_atr
                return close_result

        if pm.side == 'SHORT' and swings.get('last_swing_low') is not None:
            sl_price = swings['last_swing_low']
            fakeout = sl_price - prev['low']
            if (prev['low'] < sl_price and prev['close'] > sl_price
                    and fakeout >= min_depth and curr['close'] > sl_price):
                depth_atr = round(fakeout / atr, 3) if atr > 0 else 0.0
                logger.warning(
                    f"[V7] {pm.symbol} Reverse 2B (bullish): depth={depth_atr:.2f}x ATR"
                )
                pm.exit_reason = 'reverse_2b'
                pm.reverse_2b_depth_atr = depth_atr
                return close_result

        return None

    def _structure_trailing_sl(self, pm, df, Cfg) -> Optional[float]:
        """結構 Trailing SL：追蹤新形成的順勢 swing point（棘輪只往有利方向移動）

        df 可以是 1H 或低時間框架（如 15m），由呼叫端根據 stage 決定。
        """
        from trader.structure import StructureAnalysis

        swings = StructureAnalysis.find_swing_points(
            df, Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
        )
        atr_buffer = pm.atr * Cfg.SL_ATR_BUFFER if pm.atr else 0

        if pm.side == 'LONG':
            swing_lows = swings.get('swing_lows', [])
            if not swing_lows:
                return None
            latest_swing = swing_lows[-1][1]
            new_sl = latest_swing - atr_buffer
            # 棘輪：只能上移
            if new_sl > pm.current_sl:
                self.last_structure_swing = latest_swing
                logger.info(
                    f"[V7] {pm.symbol} Trail SL: {pm.current_sl:.2f}→{new_sl:.2f} "
                    f"(swing_low={latest_swing:.2f})"
                )
                return new_sl

        elif pm.side == 'SHORT':
            swing_highs = swings.get('swing_highs', [])
            if not swing_highs:
                return None
            latest_swing = swing_highs[-1][1]
            new_sl = latest_swing + atr_buffer
            # 棘輪：只能下移
            if new_sl < pm.current_sl:
                self.last_structure_swing = latest_swing
                logger.info(
                    f"[V7] {pm.symbol} Trail SL: {pm.current_sl:.2f}→{new_sl:.2f} "
                    f"(swing_high={latest_swing:.2f})"
                )
                return new_sl

        return None

    @staticmethod
    def calculate_add_size(
        balance: float,
        risk_per_trade: float,
        entry_price: float,
        new_sl: float,
        max_position_percent: float = 1.0,
        max_total_risk: float = 0.05,
        current_total_risk_pct: float = 0.0,
    ) -> float:
        """
        獨立計算加倉倉位大小

        Args:
            balance: 帳戶餘額
            risk_per_trade: 單筆風險比例 (e.g. 0.017)
            entry_price: 加倉入場價
            new_sl: 加倉後的止損價
            max_position_percent: 單倉最大佔比
            max_total_risk: 最大總風險佔比
            current_total_risk_pct: 現有持倉的風險佔比

        Returns:
            float: 加倉數量（0 = 不加倉）
        """
        if entry_price <= 0 or balance <= 0:
            return 0.0

        risk_amount = balance * risk_per_trade
        sl_distance_pct = abs(entry_price - new_sl) / entry_price

        if sl_distance_pct == 0:
            return 0.0

        position_value = risk_amount / sl_distance_pct

        # max_position_percent cap
        max_value = balance * max_position_percent
        if position_value > max_value:
            position_value = max_value

        # max_total_risk cap
        remaining = max_total_risk - current_total_risk_pct
        if remaining <= 0:
            return 0.0
        max_risk_value = (balance * remaining) / sl_distance_pct
        if position_value > max_risk_value:
            position_value = max_risk_value

        return max(0.0, position_value / entry_price)


# Auto-register
from trader.strategies.base import StrategyFactory
StrategyFactory.register("v7_structure", V7StructureStrategy)
