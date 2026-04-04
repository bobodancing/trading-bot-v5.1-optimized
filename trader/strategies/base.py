"""
V7 P2: Strategy Pattern 基礎模組

包含：
- Action enum（通用 action 類型）
- DecisionDict TypedDict
- _apply_common_pre() 共用前處理（V6 + V53 共享）
- TradingStrategy 抽象基類（含 get_state / load_state）
- StrategyFactory（Registry 模式）
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import TYPE_CHECKING, Dict, Optional, Type, TypedDict

import pandas as pd

if TYPE_CHECKING:
    from trader.positions import PositionManager

logger = logging.getLogger(__name__)


class Action(str, Enum):
    """通用 action 類型（策略回傳值）"""
    HOLD          = "HOLD"           # 繼續持有（原 ACTIVE）
    CLOSE         = "CLOSE"          # 全平
    PARTIAL_CLOSE = "PARTIAL_CLOSE"  # 部分平倉（原 V53_REDUCE_*）
    ADD           = "ADD"            # 加倉（原 STAGE2/3_TRIGGER）
    UPDATE_SL     = "UPDATE_SL"      # 純更新止損


class DecisionDict(TypedDict):
    action: str              # Action enum value
    reason: str              # exit/action reason code（供 performance DB 記錄）
    new_sl: Optional[float]  # 若有移損，填新止損價；否則 None
    close_pct: Optional[float]  # PARTIAL_CLOSE 時的比例（0.3 = 30%）
    add_stage: Optional[int]    # ADD 時的階段（2 or 3）


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
    from trader.config import Config as Cfg

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
            f"[{pm.strategy_name}] {pm.symbol} "
            f"SL hit @ ${current_price:.2f}"
        )
        pm.exit_reason = 'sl_hit'
        return {"action": Action.CLOSE, "reason": "NONE", "new_sl": None, "close_pct": None, "add_stage": None}

    if pm.side == 'SHORT' and current_price >= pm.current_sl:
        logger.warning(
            f"[{pm.strategy_name}] {pm.symbol} "
            f"SL hit @ ${current_price:.2f}"
        )
        pm.exit_reason = 'sl_hit'
        return {"action": Action.CLOSE, "reason": "NONE", "new_sl": None, "close_pct": None, "add_stage": None}

    # === 快速止損 -EARLY_STOP_R_THRESHOLD（僅 V6，V53 由 SL + structure_break 處理）===
    if pm.is_v6_pyramid and pm.risk_dist > 0 and pm.initial_r > 0:
        if pm.side == 'LONG':
            _r = (current_price - pm.avg_entry) / pm.risk_dist
        else:
            _r = (pm.avg_entry - current_price) / pm.risk_dist
        if _r <= -Cfg.EARLY_STOP_R_THRESHOLD:
            logger.warning(
                f"[{pm.strategy_name}] {pm.symbol} "
                f"Early stop: {_r:.2f}R <= -{Cfg.EARLY_STOP_R_THRESHOLD}R"
            )
            pm.exit_reason = 'early_stop_r'
            return {"action": Action.CLOSE, "reason": "FAST_STOP_067R", "new_sl": None, "close_pct": None, "add_stage": None}

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
        **kwargs,
    ) -> DecisionDict:
        """
        根據當前市場狀態，回傳持倉決策。

        Args:
            pm: PositionManager 實例（含倉位狀態）
            current_price: 當前最新價格
            df_1h: 1H OHLCV DataFrame（含 indicators）
            df_4h: 4H OHLCV DataFrame（可選，V6 路徑用於 EMA20 force exit）

        Returns:
            DecisionDict: {action, reason, new_sl, close_pct, add_stage}
        """
        pass

    def get_state(self) -> dict:
        """回傳策略內部 state（for persistence）"""
        return {}

    def load_state(self, state: dict):
        """從 dict 還原策略 state"""
        pass


class StrategyFactory:
    """策略工廠（Registry 模式）：依名稱建立對應的 TradingStrategy 實例"""

    _registry: Dict[str, Type[TradingStrategy]] = {}

    @classmethod
    def register(cls, name: str, strategy_cls: Type[TradingStrategy]):
        cls._registry[name] = strategy_cls

    @classmethod
    def create(cls, name: str) -> TradingStrategy:
        if name not in cls._registry:
            raise ValueError(f"Unknown strategy: {name!r}. Available: {list(cls._registry.keys())}")
        return cls._registry[name]()

    @classmethod
    def create_strategy(cls, name: str) -> TradingStrategy:
        """Backward-compat alias for create(); also accepts legacy names."""
        _legacy = {
            "v6": "v6_pyramid",
            "V6": "v6_pyramid",
            "V6_PYRAMID": "v6_pyramid",
            "v53": "v53_sop",
            "V53": "v53_sop",
            "V5.3": "v53_sop",
            "V53_SOP": "v53_sop",
            "v7": "v7_structure",
            "V7": "v7_structure",
            "V7_STRUCTURE": "v7_structure",
            "v54": "v54_noscale",
            "V54": "v54_noscale",
            "V54_NOSCALE": "v54_noscale",
        }
        resolved = _legacy.get(name, name)
        if resolved not in cls._registry:
            # Lazy import fallback for first call before registration
            from trader.strategies.v6_pyramid import V6PyramidStrategy
            from trader.strategies.v53_sop import V53SopStrategy
            from trader.strategies.v7_structure import V7StructureStrategy
            from trader.strategies.v54_noscale import V54NoScaleStrategy
            cls.register("v6_pyramid", V6PyramidStrategy)
            cls.register("v53_sop", V53SopStrategy)
            cls.register("v7_structure", V7StructureStrategy)
            cls.register("v54_noscale", V54NoScaleStrategy)
        return cls.create(_legacy.get(name, name))
