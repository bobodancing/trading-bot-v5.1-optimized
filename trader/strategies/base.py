"""
V7 P2: Strategy Pattern 基礎模組

包含：
- DecisionDict TypedDict
- _apply_common_pre() 共用前處理（V6 + V53 共享）
- TradingStrategy 抽象基類
- StrategyFactory
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional, TypedDict

import pandas as pd

if TYPE_CHECKING:
    from trader.positions import PositionManager

logger = logging.getLogger(__name__)


class DecisionDict(TypedDict):
    action: str        # "ACTIVE" | "CLOSE" | "STAGE2_TRIGGER" | "STAGE3_TRIGGER"
                       # | "V53_REDUCE_15R" | "V53_REDUCE_25R"
    reason: str        # exit/action reason code（供 performance DB 記錄）
    new_sl: Optional[float]   # 若有移損，填新止損價；否則 None
    close_pct: Optional[float]  # 僅 V53_REDUCE 時填值（0.3 = 30%）


def _apply_common_pre(pm: 'PositionManager', current_price: float, df_1h) -> Optional[dict]:
    """
    共同前處理（V6 + V53 共用）：
    1. 更新 highest_price / lowest_price
    2. 更新 ATR
    3. 遞增 monitor_count
    4. 止損觸發檢查
    5. 快速止損（Early Stop R）檢查

    Returns:
        None → 無早期退出，繼續策略邏輯
        dict → 需立即退出，直接回傳此 DecisionDict
    """
    from trader.config import ConfigV6 as Cfg

    # 更新極值
    pm.highest_price = max(pm.highest_price, current_price)
    pm.lowest_price = min(pm.lowest_price, current_price)

    # 更新 ATR
    if df_1h is not None and len(df_1h) > 0 and 'atr' in df_1h.columns:
        pm.atr = df_1h['atr'].iloc[-1]

    pm.monitor_count += 1

    # === 止損觸發 ===
    if pm.side == 'LONG' and current_price <= pm.current_sl:
        logger.warning(
            f"[{'V6' if pm.is_v6_pyramid else 'V53'}] {pm.symbol} "
            f"SL hit @ ${current_price:.2f}"
        )
        pm.exit_reason = 'sl_hit'
        return {"action": "CLOSE", "reason": "NONE", "new_sl": None, "close_pct": None}

    if pm.side == 'SHORT' and current_price >= pm.current_sl:
        logger.warning(
            f"[{'V6' if pm.is_v6_pyramid else 'V53'}] {pm.symbol} "
            f"SL hit @ ${current_price:.2f}"
        )
        pm.exit_reason = 'sl_hit'
        return {"action": "CLOSE", "reason": "NONE", "new_sl": None, "close_pct": None}

    # === 快速止損 -EARLY_STOP_R_THRESHOLD（僅 V6，V53 由 SL + structure_break 處理）===
    if pm.is_v6_pyramid and pm.risk_dist > 0 and pm.initial_r > 0:
        if pm.side == 'LONG':
            _r = (current_price - pm.avg_entry) / pm.risk_dist
        else:
            _r = (pm.avg_entry - current_price) / pm.risk_dist
        if _r <= -Cfg.EARLY_STOP_R_THRESHOLD:
            logger.warning(
                f"[{'V6' if pm.is_v6_pyramid else 'V53'}] {pm.symbol} "
                f"Early stop: {_r:.2f}R <= -{Cfg.EARLY_STOP_R_THRESHOLD}R"
            )
            pm.exit_reason = 'early_stop_r'
            return {"action": "CLOSE", "reason": "FAST_STOP_067R", "new_sl": None, "close_pct": None}

    return None


class TradingStrategy(ABC):
    """交易策略抽象基類"""

    @abstractmethod
    def get_decision(
        self,
        pm: 'PositionManager',
        current_price: float,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame = None,
    ) -> DecisionDict:
        """
        根據當前市場狀態，回傳持倉決策。

        Args:
            pm: PositionManager 實例（含倉位狀態）
            current_price: 當前最新價格
            df_1h: 1H OHLCV DataFrame（含 indicators）
            df_4h: 4H OHLCV DataFrame（可選，V6 路徑用於 EMA20 force exit）

        Returns:
            DecisionDict: {action, reason, new_sl, close_pct}
        """
        pass


class StrategyFactory:
    """策略工廠：依名稱建立對應的 TradingStrategy 實例"""

    @staticmethod
    def create_strategy(strategy_name: str = "v6") -> TradingStrategy:
        """
        Args:
            strategy_name: "v6" | "V6" | "V6_PYRAMID" | "v53" | "V53" | "V5.3" | "V53_SOP"

        Returns:
            TradingStrategy 實例

        Raises:
            ValueError: 不支援的策略名稱
        """
        if strategy_name.upper() in ("V6", "V6_PYRAMID"):
            from trader.strategies.v6_pyramid import V6PyramidStrategy
            return V6PyramidStrategy()
        elif strategy_name.upper() in ("V53", "V5.3", "V53_SOP"):
            from trader.strategies.v53_sop import V53SopStrategy
            return V53SopStrategy()
        raise ValueError(f"Unknown strategy type: {strategy_name!r}")
