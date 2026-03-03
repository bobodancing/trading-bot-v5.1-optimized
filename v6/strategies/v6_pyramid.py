"""
V7 P2: V6.0 滾倉策略實作

邏輯完整搬移自 positions.py _get_exit_decision() V6 路徑：
- 獲利回吐保護（PROFIT_PULLBACK）
- Stage 1 超時退出（TIME_EXIT）
- 4H EMA20 強制平倉（4H_EMA20_FORCE）
- 反向 2B 強制平倉（REVERSE_2B）
- 結構追蹤移損（STRUCTURE_TRAIL_SL）
- Stage 2 / Stage 3 觸發（STAGE2_TRIGGER / STAGE3_TRIGGER）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import pandas as pd
    from v6.positions import PositionManager

from v6.strategies.base import TradingStrategy, DecisionDict, _apply_common_pre

logger = logging.getLogger(__name__)


class V6PyramidStrategy(TradingStrategy):
    """V6.0 三段式金字塔滾倉策略"""

    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
    ) -> DecisionDict:
        """
        V6.0 出場決策：

        優先級（從高到低）：
        1. 共同前處理（SL hit / Early Stop）
        2. 獲利回吐保護
        3. Stage 1 超時退出
        4. 4H EMA20 強制平倉
        5. 反向 2B 強制平倉
        6. 結構追蹤移損
        7. Stage Trigger 檢查
        8. ACTIVE（持倉中）
        """
        from v6.config import ConfigV6 as Cfg
        from v6.structure import StructureAnalysis

        result: DecisionDict = {
            "action": "ACTIVE",
            "reason": "NONE",
            "new_sl": None,
            "close_pct": None,
        }

        # === 共同前處理（SL / Early Stop）===
        early = _apply_common_pre(pm, current_price, df_1h)
        if early is not None:
            return early

        # === 獲利回吐保護（需 MFE >= MIN_MFE_R 才啟用）===
        if pm.side == 'LONG' and pm.highest_price > pm.avg_entry:
            mfe = pm.highest_price - pm.avg_entry
            mfe_r = mfe / pm.risk_dist if pm.risk_dist > 0 else 0
            if mfe_r >= Cfg.MIN_MFE_R_FOR_PULLBACK:
                pullback = pm.highest_price - current_price
                if pullback / mfe >= Cfg.PROFIT_PULLBACK_THRESHOLD:
                    logger.warning(
                        f"[V6] {pm.symbol} Profit pullback: "
                        f"peak=${pm.highest_price:.2f} cur=${current_price:.2f} "
                        f"mfe={mfe_r:.2f}R pullback={pullback / mfe * 100:.1f}% >= "
                        f"{Cfg.PROFIT_PULLBACK_THRESHOLD * 100:.0f}%"
                    )
                    pm.exit_reason = 'profit_pullback'
                    return {**result, "action": "CLOSE", "reason": "PROFIT_PULLBACK"}

        if pm.side == 'SHORT' and pm.lowest_price < pm.avg_entry:
            mfe = pm.avg_entry - pm.lowest_price
            mfe_r = mfe / pm.risk_dist if pm.risk_dist > 0 else 0
            if mfe_r >= Cfg.MIN_MFE_R_FOR_PULLBACK:
                pullback = current_price - pm.lowest_price
                if pullback / mfe >= Cfg.PROFIT_PULLBACK_THRESHOLD:
                    logger.warning(
                        f"[V6] {pm.symbol} Profit pullback: "
                        f"trough=${pm.lowest_price:.2f} cur=${current_price:.2f} "
                        f"mfe={mfe_r:.2f}R pullback={pullback / mfe * 100:.1f}% >= "
                        f"{Cfg.PROFIT_PULLBACK_THRESHOLD * 100:.0f}%"
                    )
                    pm.exit_reason = 'profit_pullback'
                    return {**result, "action": "CLOSE", "reason": "PROFIT_PULLBACK"}

        # === Stage 1 超時退出 ===
        if pm.stage == 1:
            hours_held = (datetime.now(timezone.utc) - pm.entry_time).total_seconds() / 3600
            if hours_held >= Cfg.V6_STAGE1_MAX_HOURS:
                logger.warning(
                    f"[V6] {pm.symbol} Stage 1 timeout: "
                    f"{hours_held:.1f}h >= {Cfg.V6_STAGE1_MAX_HOURS}h"
                )
                pm.exit_reason = 'stage1_timeout'
                return {**result, "action": "CLOSE", "reason": "TIME_EXIT"}

        # === 4H EMA20 強制平倉 ===
        if Cfg.V6_4H_EMA20_FORCE_EXIT and df_4h is not None and len(df_4h) > 0:
            ema20_4h = None
            if 'ema_fast' in df_4h.columns:
                ema20_4h = df_4h['ema_fast'].iloc[-1]
            elif 'ema_slow' in df_4h.columns:
                ema20_4h = df_4h['ema_slow'].iloc[-1]

            if ema20_4h is not None:
                close_4h = df_4h['close'].iloc[-1]
                if pm.side == 'LONG' and close_4h < ema20_4h:
                    logger.warning(
                        f"[V6] {pm.symbol} 4H EMA20 breakdown: "
                        f"close=${close_4h:.2f} < EMA20=${ema20_4h:.2f}"
                    )
                    pm.exit_reason = 'ema20_4h'
                    return {**result, "action": "CLOSE", "reason": "4H_EMA20_FORCE"}
                if pm.side == 'SHORT' and close_4h > ema20_4h:
                    logger.warning(
                        f"[V6] {pm.symbol} 4H EMA20 breakdown: "
                        f"close=${close_4h:.2f} > EMA20=${ema20_4h:.2f}"
                    )
                    pm.exit_reason = 'ema20_4h'
                    return {**result, "action": "CLOSE", "reason": "4H_EMA20_FORCE"}

        # === 反向 2B 強制平倉（穿透深度 + 下一根確認）===
        if Cfg.V6_REVERSE_2B_EXIT and df_1h is not None and len(df_1h) >= 2:
            swings = StructureAnalysis.find_swing_points(
                df_1h, Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
            )
            prev = df_1h.iloc[-2]   # 穿透 K 線
            curr = df_1h.iloc[-1]   # 確認 K 線
            atr = pm.atr if pm.atr and pm.atr > 0 else 0
            min_depth = atr * Cfg.REVERSE_2B_MIN_FAKEOUT_ATR if atr > 0 else 0

            if pm.side == 'LONG' and swings['last_swing_high'] is not None:
                sh = swings['last_swing_high']
                fakeout_depth = prev['high'] - sh
                if (prev['high'] > sh
                        and prev['close'] < sh
                        and fakeout_depth >= min_depth
                        and curr['close'] < sh):
                    depth_atr = round(fakeout_depth / atr, 3) if atr > 0 else 0.0
                    logger.warning(
                        f"[V6] {pm.symbol} Reverse 2B (bearish) confirmed: "
                        f"depth={depth_atr:.2f}x ATR, "
                        f"prev_high=${prev['high']:.2f} > SH=${sh:.2f}, "
                        f"curr_close=${curr['close']:.2f} < SH"
                    )
                    pm.exit_reason = 'reverse_2b'
                    pm.reverse_2b_depth_atr = depth_atr
                    return {**result, "action": "CLOSE", "reason": "REVERSE_2B"}

            if pm.side == 'SHORT' and swings['last_swing_low'] is not None:
                sl_price = swings['last_swing_low']
                fakeout_depth = sl_price - prev['low']
                if (prev['low'] < sl_price
                        and prev['close'] > sl_price
                        and fakeout_depth >= min_depth
                        and curr['close'] > sl_price):
                    depth_atr = round(fakeout_depth / atr, 3) if atr > 0 else 0.0
                    logger.warning(
                        f"[V6] {pm.symbol} Reverse 2B (bullish) confirmed: "
                        f"depth={depth_atr:.2f}x ATR, "
                        f"prev_low=${prev['low']:.2f} < SL=${sl_price:.2f}, "
                        f"curr_close=${curr['close']:.2f} > SL"
                    )
                    pm.exit_reason = 'reverse_2b'
                    pm.reverse_2b_depth_atr = depth_atr
                    return {**result, "action": "CLOSE", "reason": "REVERSE_2B"}

        # === 嚴謹結構追蹤移損 (HL/LH + Temporal BOS) ===
        trailing_new_sl = None
        if Cfg.V6_STRUCTURE_TRAILING and df_1h is not None and len(df_1h) > 0:
            validated_swing = StructureAnalysis.get_validated_trailing_swing(
                df_1h, pm.side, pm.current_sl,
                Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
            )
            if validated_swing is not None:
                atr_buffer = pm.atr * Cfg.SL_ATR_BUFFER if pm.atr else 0
                if pm.side == 'LONG':
                    new_sl = validated_swing - atr_buffer
                    if new_sl > pm.current_sl:
                        old_sl = pm.current_sl
                        pm.current_sl = new_sl
                        trailing_new_sl = new_sl
                        logger.info(
                            f"[V6] {pm.symbol} HL Trailing (BOS confirmed): "
                            f"SL ${old_sl:.2f} -> ${new_sl:.2f} "
                            f"(swing_low=${validated_swing:.2f})"
                        )
                else:  # SHORT
                    new_sl = validated_swing + atr_buffer
                    if new_sl < pm.current_sl:
                        old_sl = pm.current_sl
                        pm.current_sl = new_sl
                        trailing_new_sl = new_sl
                        logger.info(
                            f"[V6] {pm.symbol} LH Trailing (BOS confirmed): "
                            f"SL ${old_sl:.2f} -> ${new_sl:.2f} "
                            f"(swing_high=${validated_swing:.2f})"
                        )

        if trailing_new_sl is not None:
            result = {**result, "reason": "STRUCTURE_TRAIL_SL", "new_sl": trailing_new_sl}

        # === Stage Trigger 檢查 ===
        if pm.stage == 1 and pm.check_stage2_trigger(df_1h):
            return {**result, "action": "STAGE2_TRIGGER", "reason": "NECKLINE_BREAK"}
        if pm.stage == 2 and pm.check_stage3_trigger(df_1h):
            return {**result, "action": "STAGE3_TRIGGER", "reason": "EMA_PULLBACK"}

        return result
