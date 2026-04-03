import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import numpy as np
import pandas as pd
import pytest

from trader.config import ConfigV6 as Config
from trader.strategies.v8_grid import GridAction, V8AtrGrid


def _make_1h_df(close_values, n=50):
    dates = pd.date_range('2026-01-01', periods=n, freq='1h')
    close = close_values if len(close_values) == n else (close_values + [close_values[-1]] * (n - len(close_values)))[:n]
    return pd.DataFrame(
        {
            'open': close,
            'high': [c + 50 for c in close],
            'low': [c - 50 for c in close],
            'close': close,
            'volume': [100] * n,
        },
        index=dates,
    )


def _make_4h_df(center=87000, n=60):
    np.random.seed(42)
    dates = pd.date_range('2026-01-01', periods=n, freq='4h')
    close = [center + np.random.randn() * 50 for _ in range(n)]
    return pd.DataFrame(
        {
            'open': close,
            'high': [c + 100 for c in close],
            'low': [c - 100 for c in close],
            'close': close,
            'volume': [500] * n,
        },
        index=dates,
    )


class TestGridConstruction:
    def test_activate_creates_state(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        assert grid.state is not None
        assert grid.state.grid_levels == 5
        assert grid.state.upper > grid.state.center
        assert grid.state.lower < grid.state.center

    def test_activate_ignores_unfinished_last_candle(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = pd.DataFrame(
            {
                'open': [100.0] * 59 + [1000.0],
                'high': [110.0] * 59 + [1010.0],
                'low': [90.0] * 59 + [990.0],
                'close': [100.0] * 59 + [1000.0],
                'volume': [500] * 60,
            },
            index=pd.date_range('2026-01-01', periods=60, freq='4h'),
        )
        grid.activate(df, grid_balance=3000.0)
        assert grid.state is not None
        assert grid.state.center == pytest.approx(100.0, abs=0.01)


class TestGridTick:
    def test_tick_no_action_at_center(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        actions = grid.tick(grid.state.center, _make_1h_df([87000] * 50))
        assert actions == []

    def test_tick_open_requires_confirm_before_state_mutates(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        price_above_l2 = grid.state.center + grid.state.grid_spacing * 2.5
        actions = grid.tick(price_above_l2, _make_1h_df([price_above_l2] * 50))
        open_action = next(a for a in actions if a.type == 'OPEN')
        assert grid.state.active_positions == []
        grid.confirm_action(open_action)
        assert len(grid.state.active_positions) == 1
        assert grid.state.active_positions[0]['level'] == open_action.level

    def test_tick_close_carries_entry_price_and_waits_for_confirm(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        grid.state.active_positions = [
            {'level': 2, 'side': 'SHORT', 'entry': 87500.0, 'size': 0.01},
        ]
        current_price = grid.state.center
        actions = grid.tick(current_price, _make_1h_df([current_price] * 50))
        close_action = next(a for a in actions if a.type == 'CLOSE')
        assert close_action.entry_price == 87500.0
        assert len(grid.state.active_positions) == 1
        grid.confirm_action(close_action)
        assert grid.state.active_positions == []

    def test_tick_ignores_unfinished_last_1h_candle(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        old_center = grid.state.center
        df_1h = _make_1h_df([grid.state.center] * 49 + [grid.state.center + grid.state.grid_spacing * 10])
        actions = grid.tick(grid.state.center, df_1h)
        assert actions == []
        assert grid.state.center == pytest.approx(old_center)

    def test_tick_respects_grid_max_total_risk(self, monkeypatch):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        monkeypatch.setattr(Config, 'GRID_MAX_TOTAL_RISK', 0.005)
        price_above_l2 = grid.state.center + grid.state.grid_spacing * 2.5
        actions = grid.tick(price_above_l2, _make_1h_df([price_above_l2] * 50))
        opens = [a for a in actions if a.type == 'OPEN']
        assert len(opens) == 1


class TestGridConverge:
    def test_converge_blocks_new_opens(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        grid.converge()
        price_edge = grid.state.upper - 10
        actions = grid.tick(price_edge, _make_1h_df([price_edge] * 50))
        assert [a for a in actions if a.type == 'OPEN'] == []


class TestGridForceClose:
    def test_force_close_all_does_not_mutate_state(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        grid.state.active_positions = [
            {'level': 2, 'side': 'SHORT', 'entry': 87500.0, 'size': 0.01},
            {'level': -1, 'side': 'LONG', 'entry': 86500.0, 'size': 0.01},
        ]
        actions = grid.force_close_all("drawdown_limit")
        assert len([a for a in actions if a.type == 'CLOSE']) == 2
        assert len(grid.state.active_positions) == 2

    def test_confirm_close_updates_grid_balance(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        grid.state.active_positions = [
            {'level': -1, 'side': 'LONG', 'entry': 100.0, 'size': 1.5},
        ]
        action = GridAction(
            type='CLOSE',
            side='LONG',
            level=-1,
            size=1.5,
            price=110.0,
            entry_price=100.0,
        )
        grid.confirm_action(action)
        assert grid.state.active_positions == []
        assert grid.state.grid_balance == pytest.approx(3015.0)


class TestGridReset:
    def test_reset_on_sma_drift_returns_close_actions(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        grid.activate(_make_4h_df(center=87000), grid_balance=3000.0)
        grid.state.active_positions = [
            {'level': 1, 'side': 'SHORT', 'entry': 87100.0, 'size': 0.01},
        ]
        drift = grid.state.grid_spacing * 0.6
        shifted_close = [grid.state.center + drift] * 50
        actions = grid.tick(grid.state.center + drift, _make_1h_df(shifted_close))
        assert any(a.type == 'CLOSE' for a in actions)

    def test_reset_rebuilds_after_close_confirm(self):
        grid = V8AtrGrid(api_client=None, notifier=None)
        df = _make_4h_df(center=87000)
        grid.activate(df, grid_balance=3000.0)
        grid.state.active_positions = [
            {'level': 1, 'side': 'SHORT', 'entry': grid.state.center + grid.state.grid_spacing, 'size': 0.01},
        ]
        old_center = grid.state.center
        drift = grid.state.grid_spacing * 0.6
        shifted_price = old_center + drift

        actions = grid.tick(shifted_price, _make_1h_df([shifted_price] * 50))
        closes = [action for action in actions if action.type == 'CLOSE']

        assert len(closes) == 1
        assert len(grid.state.active_positions) == 1

        closes[0].price = shifted_price
        grid.confirm_action(closes[0])

        assert grid.state is not None
        assert grid.state.active_positions == []
        assert grid.state.center == pytest.approx(shifted_price, abs=1.0)
