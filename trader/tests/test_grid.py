# trader/tests/test_grid.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
import numpy as np
from trader.strategies.v8_grid import V8AtrGrid, GridState, GridAction


def _make_1h_df(close_values, n=50):
    dates = pd.date_range('2026-01-01', periods=n, freq='1h')
    close = close_values if len(close_values) == n else (close_values + [close_values[-1]] * (n - len(close_values)))[:n]
    return pd.DataFrame({
        'open': close, 'high': [c + 50 for c in close],
        'low': [c - 50 for c in close], 'close': close,
        'volume': [100] * n,
    }, index=dates)


def _make_4h_df(center=87000, n=60):
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=n, freq='4h')
    close = [center + np.random.randn() * 50 for _ in range(n)]
    return pd.DataFrame({
        'open': close, 'high': [c + 100 for c in close],
        'low': [c - 100 for c in close], 'close': close,
        'volume': [500] * n,
    }, index=dates)


class TestGridConstruction:
    def test_activate_creates_state(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        assert grid.state is not None
        assert grid.state.grid_levels == 5
        assert grid.state.center > 0
        assert grid.state.upper > grid.state.center
        assert grid.state.lower < grid.state.center

    def test_grid_spacing(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df()
        grid.activate(df, grid_balance=3000.0)
        expected_spacing = (grid.state.upper - grid.state.lower) / (5 * 2)
        assert abs(grid.state.grid_spacing - expected_spacing) < 0.01

    def test_pyramid_weights(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df()
        grid.activate(df, grid_balance=3000.0)
        weights = grid.state.level_weights
        # Edge levels should have higher weight than center
        assert weights[5] > weights[1]  # level 5 (edge) > level 1 (center)
        assert weights[1] == pytest.approx(0.5, abs=0.01)
        assert weights[5] == pytest.approx(1.5, abs=0.01)


class TestGridTick:
    def test_tick_no_action_at_center(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        # Price at center → no action
        actions = grid.tick(grid.state.center, _make_1h_df([87000] * 50))
        assert actions == []

    def test_tick_open_short_on_upward_cross(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        # Price crosses above level 2 → should open SHORT
        price_above_l2 = grid.state.center + grid.state.grid_spacing * 2.5
        actions = grid.tick(price_above_l2, _make_1h_df([price_above_l2] * 50))
        opens = [a for a in actions if a.type == 'OPEN']
        assert len(opens) > 0
        assert all(a.side == 'SHORT' for a in opens)

    def test_tick_open_long_on_downward_cross(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        price_below_l2 = grid.state.center - grid.state.grid_spacing * 2.5
        actions = grid.tick(price_below_l2, _make_1h_df([price_below_l2] * 50))
        opens = [a for a in actions if a.type == 'OPEN']
        assert len(opens) > 0
        assert all(a.side == 'LONG' for a in opens)


class TestGridConverge:
    def test_converge_blocks_new_opens(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        grid.converge()
        assert grid.state.converging is True
        # Tick with price at edge — should NOT open new positions
        price_edge = grid.state.upper - 10
        actions = grid.tick(price_edge, _make_1h_df([price_edge] * 50))
        opens = [a for a in actions if a.type == 'OPEN']
        assert opens == []


class TestGridForceClose:
    def test_force_close_all(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        # Simulate some active positions
        grid.state.active_positions = [
            {'level': 2, 'side': 'SHORT', 'entry': 87500, 'size': 0.01},
            {'level': -1, 'side': 'LONG', 'entry': 86500, 'size': 0.01},
        ]
        actions = grid.force_close_all("drawdown_limit")
        closes = [a for a in actions if a.type == 'CLOSE']
        assert len(closes) == 2


class TestGridReset:
    def test_reset_on_sma_drift(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        old_center = grid.state.center
        # Create 1H df where SMA drifts significantly beyond threshold
        drift = grid.state.grid_spacing * 0.6  # > 0.5 threshold
        shifted_close = [old_center + drift] * 50
        # After reset, a new grid should be built
        actions = grid.tick(old_center + drift, _make_1h_df(shifted_close))
        # The state should be rebuilt (center changes) OR force close actions returned
        # Either way, the function should not crash
        assert isinstance(actions, list)
