# trader/strategies/v8_grid/pool_manager.py
"""Grid/Trend 資金池管理"""
import logging

from trader.config import Config

logger = logging.getLogger(__name__)


class PoolManager:
    """管理趨勢池/網格池的資金分配"""

    # 網格池最低分配金額（低於此值不啟動）
    GRID_MIN_ALLOCATION = 500.0  # USDT

    def __init__(self):
        self.grid_allocated: float = 0.0
        self.grid_realized_pnl: float = 0.0
        self.cumulative_grid_pnl: float = 0.0
        self._round_count: int = 0

    @property
    def is_active(self) -> bool:
        return self.grid_allocated > 0

    def activate_grid_pool(self, total_balance: float) -> bool:
        """Regime 進入 RANGING 時呼叫，鎖定固定金額。
        Returns False if allocation too small."""
        allocation = total_balance * Config.GRID_CAPITAL_RATIO
        if allocation < self.GRID_MIN_ALLOCATION:
            logger.warning(f"Grid pool allocation {allocation:.0f} < min {self.GRID_MIN_ALLOCATION} — skipping")
            return False
        self.grid_allocated = allocation
        self.grid_realized_pnl = 0.0
        self._round_count += 1
        return True

    def deactivate_grid_pool(self):
        """網格收斂完成後呼叫"""
        self.cumulative_grid_pnl += self.grid_realized_pnl
        self.grid_allocated = 0.0
        self.grid_realized_pnl = 0.0

    def get_grid_balance(self) -> float:
        """網格池可用 = 初始分配 + 已實現損益"""
        return max(0.0, self.grid_allocated + self.grid_realized_pnl)

    def get_trend_balance(self, total_balance: float) -> float:
        """趨勢池 = 總餘額 - 網格池分配"""
        return total_balance - self.grid_allocated
