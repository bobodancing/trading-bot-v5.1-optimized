"""
[DEPRECATED] V6.0 滾倉策略 — 已被 V7 StructureStrategy 取代

保留原因：既有 V6 持倉仍需此策略正常運行直到平倉。
新進場一律走 V7（SIGNAL_STRATEGY_MAP["2B"] = "v7_structure"）。

原始邏輯：
- 三段式動態防守（Tier 1 保本 / Tier 2 加速追蹤 / Tier 3 標準追蹤）
- Stage 1 超時退出（TIME_EXIT）
- 4H EMA20 強制平倉（4H_EMA20_FORCE）
- 反向 2B 強制平倉（REVERSE_2B）
- 結構追蹤移損（STRUCTURE_TRAIL_SL）
- Stage 2 / Stage 3 觸發（ADD add_stage=2/3）
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    import pandas as pd
    from trader.positions import PositionManager

from trader.strategies.base import Action, TradingStrategy, DecisionDict, _apply_common_pre

logger = logging.getLogger(__name__)


class V6PyramidStrategy(TradingStrategy):
    """V6.0 三段式金字塔滾倉策略"""

    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h,
        df_4h=None,
        **kwargs,
    ) -> DecisionDict:
        """
        V6.0 出場決策：

        優先級（從高到低）：
        1. 共同前處理（SL hit / Early Stop）
        2. Stage 1 超時退出
        3. 4H EMA20 強制平倉
        4. 反向 2B 強制平倉
        5. Tier 1 保本移損（不 return）
        6. 結構追蹤移損（Tier 2 / Tier 3 分流，不 return）
        7. Stage Trigger 檢查
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

        # === Stage 1 超時退出 ===
        if pm.stage == 1:
            hours_held = (datetime.now(timezone.utc) - pm.entry_time).total_seconds() / 3600
            if hours_held >= Cfg.V6_STAGE1_MAX_HOURS:
                logger.warning(
                    f"[V6] {pm.symbol} Stage 1 timeout: "
                    f"{hours_held:.1f}h >= {Cfg.V6_STAGE1_MAX_HOURS}h"
                )
                pm.exit_reason = 'stage1_timeout'
                return {**result, "action": Action.CLOSE, "reason": "TIME_EXIT"}

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
                    return {**result, "action": Action.CLOSE, "reason": "4H_EMA20_FORCE"}
                if pm.side == 'SHORT' and close_4h > ema20_4h:
                    logger.warning(
                        f"[V6] {pm.symbol} 4H EMA20 breakdown: "
                        f"close=${close_4h:.2f} > EMA20=${ema20_4h:.2f}"
                    )
                    pm.exit_reason = 'ema20_4h'
                    return {**result, "action": Action.CLOSE, "reason": "4H_EMA20_FORCE"}

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
                    return {**result, "action": Action.CLOSE, "reason": "REVERSE_2B"}

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
                    return {**result, "action": Action.CLOSE, "reason": "REVERSE_2B"}

        # === Tier 1: 保本移損（Breakeven Bridge）===
        # MFE 跑出 1.5R 後，SL 移到 entry + 0.1R（只做一次，棘輪）
        if Cfg.V6_BREAKEVEN_ENABLED and pm.risk_dist > 0:
            if pm.side == 'LONG':
                mfe = pm.highest_price - pm.avg_entry
            else:
                mfe = pm.avg_entry - pm.lowest_price
            mfe_r = mfe / pm.risk_dist

            if mfe_r >= Cfg.V6_BREAKEVEN_MFE_R:
                buffer = pm.risk_dist * Cfg.V6_BREAKEVEN_BUFFER_R
                if pm.side == 'LONG':
                    be_sl = pm.avg_entry + buffer
                    if pm.current_sl < be_sl:
                        old_sl = pm.current_sl
                        pm.current_sl = be_sl
                        logger.info(
                            f"[V6] {pm.symbol} Tier 1 Breakeven: "
                            f"SL ${old_sl:.4f} -> ${be_sl:.4f} "
                            f"(mfe={mfe_r:.2f}R >= {Cfg.V6_BREAKEVEN_MFE_R}R)"
                        )
                else:  # SHORT
                    be_sl = pm.avg_entry - buffer
                    if pm.current_sl > be_sl:
                        old_sl = pm.current_sl
                        pm.current_sl = be_sl
                        logger.info(
                            f"[V6] {pm.symbol} Tier 1 Breakeven: "
                            f"SL ${old_sl:.4f} -> ${be_sl:.4f} "
                            f"(mfe={mfe_r:.2f}R >= {Cfg.V6_BREAKEVEN_MFE_R}R)"
                        )

        # === 結構追蹤移損（Tier 2 / Tier 3 分流）===
        trailing_new_sl = None
        if Cfg.V6_STRUCTURE_TRAILING and df_1h is not None and len(df_1h) > 0:
            # Stage 1: Tier 2 加速追蹤（right_bars=2, 不要求 BOS）
            # Stage 2+: Tier 3 標準追蹤（right_bars=3, 要求 BOS）
            if pm.stage == 1 and not Cfg.V6_FAST_TRAIL_REQUIRE_BOS:
                validated_swing = StructureAnalysis.get_fast_trailing_swing(
                    df_1h, pm.side, pm.current_sl,
                    Cfg.SWING_LEFT_BARS, Cfg.V6_FAST_TRAIL_RIGHT_BARS
                )
                trail_label = "Tier2 Fast"
            else:
                validated_swing = StructureAnalysis.get_validated_trailing_swing(
                    df_1h, pm.side, pm.current_sl,
                    Cfg.SWING_LEFT_BARS, Cfg.SWING_RIGHT_BARS
                )
                trail_label = "Tier3 BOS"

            if validated_swing is not None:
                atr_buffer = pm.atr * Cfg.SL_ATR_BUFFER if pm.atr else 0
                if pm.side == 'LONG':
                    new_sl = validated_swing - atr_buffer
                    if new_sl > pm.current_sl:
                        old_sl = pm.current_sl
                        pm.current_sl = new_sl
                        trailing_new_sl = new_sl
                        logger.info(
                            f"[V6] {pm.symbol} {trail_label} HL Trailing: "
                            f"SL ${old_sl:.2f} -> ${new_sl:.2f} "
                            f"(swing_low={validated_swing:.2f})"
                        )
                else:  # SHORT
                    new_sl = validated_swing + atr_buffer
                    if new_sl < pm.current_sl:
                        old_sl = pm.current_sl
                        pm.current_sl = new_sl
                        trailing_new_sl = new_sl
                        logger.info(
                            f"[V6] {pm.symbol} {trail_label} LH Trailing: "
                            f"SL ${old_sl:.2f} -> ${new_sl:.2f} "
                            f"(swing_high={validated_swing:.2f})"
                        )

        if trailing_new_sl is not None:
            result = {**result, "reason": "STRUCTURE_TRAIL_SL", "new_sl": trailing_new_sl}

        # === Stage Trigger 檢查 ===
        if pm.stage == 1 and pm.check_stage2_trigger(df_1h):
            return {**result, "action": Action.ADD, "reason": "NECKLINE_BREAK", "add_stage": 2}
        if pm.stage == 2 and pm.check_stage3_trigger(df_1h):
            return {**result, "action": Action.ADD, "reason": "EMA_PULLBACK", "add_stage": 3}

        return result


# 自動註冊至 StrategyFactory
from trader.strategies.base import StrategyFactory
StrategyFactory.register("v6_pyramid", V6PyramidStrategy)
