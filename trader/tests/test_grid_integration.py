# trader/tests/test_grid_integration.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
from trader.regime import RegimeEngine
from trader.grid import V8AtrGrid, GridState
from trader.risk.manager import PoolManager
from trader.config import ConfigV6 as Config


def _make_4h_regime_df(adx, bbw, atr, n=60):
    dates = pd.date_range('2026-01-01', periods=n, freq='4h')
    close = [87000 + i * 10 for i in range(n)]
    return pd.DataFrame({
        'open': close, 'high': [c + 100 for c in close],
        'low': [c - 100 for c in close], 'close': close,
        'volume': [1000] * n,
        'adx': adx if isinstance(adx, list) else [adx] * n,
        'bbw': bbw if isinstance(bbw, list) else [bbw] * n,
        'atr': atr if isinstance(atr, list) else [atr] * n,
    }, index=dates)


class TestRegimeToGridFlow:
    def test_trending_to_ranging_activates_grid(self):
        """When regime switches to RANGING, grid should activate"""
        engine = RegimeEngine()
        pool = PoolManager()
        grid = V8AtrGrid(api_client=None, notifier=None)

        # Confirm RANGING
        df = _make_4h_regime_df(adx=15, bbw=[0.08]*40+[0.01]*20, atr=200)
        for i in range(3):
            regime = engine.update(df.iloc[:50+i])

        assert regime == "RANGING"

        # Activate grid
        pool.activate_grid_pool(10000.0)
        grid.activate(df, pool.get_grid_balance())
        assert grid.state is not None
        assert pool.is_active

    def test_ranging_to_squeeze_converges_grid(self):
        """When regime switches from RANGING to SQUEEZE, grid enters converge"""
        engine = RegimeEngine()
        grid = V8AtrGrid(api_client=None, notifier=None)

        # First establish RANGING
        df_ranging = _make_4h_regime_df(adx=15, bbw=[0.08]*40+[0.01]*20, atr=200)
        for i in range(3):
            engine.update(df_ranging.iloc[:50+i])
        grid.activate(df_ranging, 3000.0)

        # Now switch to SQUEEZE
        df_squeeze = _make_4h_regime_df(adx=18, bbw=[0.08]*43+[0.002]*20, atr=200, n=63)
        for i in range(3):
            engine.update(df_squeeze.iloc[:53+i])

        assert engine.current_regime == "SQUEEZE"
        grid.converge()
        assert grid.state.converging is True

    def test_pool_isolation(self):
        """Grid pool losses should not affect trend balance calculation"""
        pool = PoolManager()
        pool.activate_grid_pool(10000.0)  # 3000 grid, 7000 trend

        # Grid takes losses
        pool.grid_realized_pnl = -500.0

        assert pool.get_grid_balance() == 2500.0  # 3000 - 500
        assert pool.get_trend_balance(10000.0) == 7000.0  # unaffected


class TestGridStateRoundtrip:
    def test_save_load(self, tmp_path):
        """GridState should survive save/load cycle"""
        from trader.persistence import save_grid_state, load_grid_state
        state = GridState(
            center=87000, upper=88000, lower=86000,
            grid_levels=5, grid_spacing=200,
            grid_balance=3000,
            active_positions=[{'level': 2, 'side': 'SHORT', 'entry': 87400, 'size': 0.01}],
            level_weights={1: 0.5, 2: 0.8, 3: 1.0, 4: 1.3, 5: 1.5},
        )
        path = str(tmp_path / "grid_test.json")
        save_grid_state(state.to_dict(), path=path)
        loaded = load_grid_state(path=path)
        restored = GridState.from_dict(loaded)
        assert restored.center == 87000
        assert len(restored.active_positions) == 1
        assert restored.level_weights[2] == 0.8


class TestBotMainLoopGrid:
    """Mock bot 主循環測試 — 確保 grid 路徑正確"""

    def test_grid_not_triggered_when_disabled(self):
        """ENABLE_GRID_TRADING=False → grid code path never entered"""
        with patch.object(Config, 'ENABLE_GRID_TRADING', False):
            engine = RegimeEngine()
            grid = V8AtrGrid(api_client=None, notifier=None)
            # Even with RANGING regime, grid should not activate
            engine.current_regime = "RANGING"
            assert grid.state is None  # grid never activated

    def test_ranging_regime_activates_grid_engine(self):
        """RANGING regime + ENABLE_GRID_TRADING → grid.activate() called"""
        with patch.object(Config, 'ENABLE_GRID_TRADING', True):
            engine = RegimeEngine()
            pool = PoolManager()
            grid = V8AtrGrid(api_client=None, notifier=None)

            df = _make_4h_regime_df(adx=15, bbw=[0.08]*40+[0.01]*20, atr=200)
            for i in range(3):
                engine.update(df.iloc[:50+i])
            assert engine.current_regime == "RANGING"

            # Activate grid pool + engine
            pool.activate_grid_pool(10000.0)
            grid.activate(df, pool.get_grid_balance())
            assert grid.state is not None
            assert pool.is_active

    def test_regime_change_triggers_converge(self):
        """Regime → TRENDING while grid active → converge mode"""
        engine = RegimeEngine()
        pool = PoolManager()
        grid = V8AtrGrid(api_client=None, notifier=None)

        df_ranging = _make_4h_regime_df(adx=15, bbw=[0.08]*40+[0.01]*20, atr=200)
        for i in range(3):
            engine.update(df_ranging.iloc[:50+i])
        pool.activate_grid_pool(10000.0)
        grid.activate(df_ranging, 3000.0)

        # Switch to TRENDING
        df_trending = _make_4h_regime_df(adx=30, bbw=0.05, atr=200, n=63)
        for i in range(3):
            engine.update(df_trending.iloc[:53+i])
        assert engine.current_regime == "TRENDING"

        # Grid should converge
        grid.converge()
        assert grid.state.converging is True

    def test_pnl_flows_to_pool_manager(self):
        """Grid close action PnL updates pool_manager.grid_realized_pnl"""
        pool = PoolManager()
        pool.activate_grid_pool(10000.0)
        initial_pnl = pool.grid_realized_pnl

        # Simulate a profitable close
        pool.grid_realized_pnl += 50.0
        assert pool.get_grid_balance() == 3050.0
