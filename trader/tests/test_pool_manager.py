import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from trader.risk.manager import PoolManager


class TestPoolManager:
    def test_activate_grid_pool(self):
        pm = PoolManager()
        assert pm.activate_grid_pool(10000.0) is True
        assert pm.grid_allocated == 3000.0  # 30% of 10000
        assert pm.grid_realized_pnl == 0.0

    def test_get_grid_balance(self):
        pm = PoolManager()
        pm.activate_grid_pool(10000.0)
        assert pm.get_grid_balance() == 3000.0

    def test_grid_balance_with_profit(self):
        pm = PoolManager()
        pm.activate_grid_pool(10000.0)
        pm.grid_realized_pnl = 150.0
        assert pm.get_grid_balance() == 3150.0

    def test_grid_balance_with_loss(self):
        pm = PoolManager()
        pm.activate_grid_pool(10000.0)
        pm.grid_realized_pnl = -3500.0
        assert pm.get_grid_balance() == 0.0  # max(0, ...) clamped

    def test_get_trend_balance(self):
        pm = PoolManager()
        pm.activate_grid_pool(10000.0)
        assert pm.get_trend_balance(10000.0) == 7000.0

    def test_deactivate_tracks_cumulative_pnl(self):
        pm = PoolManager()
        pm.activate_grid_pool(10000.0)
        pm.grid_realized_pnl = -200.0
        pm.deactivate_grid_pool()
        assert pm.grid_allocated == 0.0
        assert pm.is_active is False
        assert pm.cumulative_grid_pnl == -200.0  # tracks across rounds

    def test_is_active(self):
        pm = PoolManager()
        assert pm.is_active is False
        pm.activate_grid_pool(5000.0)
        assert pm.is_active is True

    def test_reject_too_small_allocation(self):
        pm = PoolManager()
        assert pm.activate_grid_pool(1000.0) is False  # 1000 * 0.3 = 300 < 500 min
        assert pm.is_active is False

    def test_multi_round_cumulative(self):
        pm = PoolManager()
        pm.activate_grid_pool(10000.0)
        pm.grid_realized_pnl = -100.0
        pm.deactivate_grid_pool()
        pm.activate_grid_pool(9900.0)  # balance dropped
        pm.grid_realized_pnl = 50.0
        pm.deactivate_grid_pool()
        assert pm.cumulative_grid_pnl == -50.0  # -100 + 50
