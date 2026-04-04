# trader/tests/test_grid_integration.py
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.config import Config
from trader.regime import RegimeEngine
from trader.strategies.v8_grid import GridAction, GridState, PoolManager, V8AtrGrid


def _make_4h_regime_df(adx, bbw, atr, n=60):
    dates = pd.date_range('2026-01-01', periods=n, freq='4h')
    close = [87000 + i * 10 for i in range(n)]
    return pd.DataFrame(
        {
            'open': close,
            'high': [c + 100 for c in close],
            'low': [c - 100 for c in close],
            'close': close,
            'volume': [1000] * n,
            'adx': adx if isinstance(adx, list) else [adx] * n,
            'bbw': bbw if isinstance(bbw, list) else [bbw] * n,
            'atr': atr if isinstance(atr, list) else [atr] * n,
        },
        index=dates,
    )


class TestRegimeToGridFlow:
    def test_trending_to_ranging_activates_grid(self):
        engine = RegimeEngine()
        pool = PoolManager()
        grid = V8AtrGrid(api_client=None, notifier=None)

        df = _make_4h_regime_df(adx=15, bbw=[0.08] * 40 + [0.01] * 20, atr=200)
        for i in range(3):
            regime = engine.update(df.iloc[:50 + i])

        assert regime == "RANGING"

        pool.activate_grid_pool(10000.0)
        grid.activate(df, pool.get_grid_balance())
        assert grid.state is not None
        assert pool.is_active

    def test_ranging_to_squeeze_converges_grid(self):
        engine = RegimeEngine()
        grid = V8AtrGrid(api_client=None, notifier=None)

        df_ranging = _make_4h_regime_df(adx=15, bbw=[0.08] * 40 + [0.01] * 20, atr=200)
        for i in range(3):
            engine.update(df_ranging.iloc[:50 + i])
        grid.activate(df_ranging, 3000.0)

        df_squeeze = _make_4h_regime_df(adx=18, bbw=[0.08] * 43 + [0.002] * 20, atr=200, n=63)
        for i in range(3):
            engine.update(df_squeeze.iloc[:53 + i])

        assert engine.current_regime == "SQUEEZE"
        grid.converge()
        assert grid.state.converging is True

    def test_pool_isolation(self):
        pool = PoolManager()
        pool.activate_grid_pool(10000.0)
        pool.grid_realized_pnl = -500.0

        assert pool.get_grid_balance() == 2500.0
        assert pool.get_trend_balance(10000.0) == 7000.0


class TestGridStateRoundtrip:
    def test_save_load(self, tmp_path):
        from trader.persistence import load_grid_state, save_grid_state

        state = GridState(
            center=87000,
            upper=88000,
            lower=86000,
            grid_levels=5,
            grid_spacing=200,
            grid_balance=3000,
            active_positions=[{'level': 2, 'side': 'SHORT', 'entry': 87400, 'size': 0.01}],
            level_weights={1: 0.5, 2: 0.8, 3: 1.0, 4: 1.3, 5: 1.5},
        )
        path = str(tmp_path / "grid_test.json")
        pool = PoolManager()
        pool.activate_grid_pool(10000.0)
        pool.grid_realized_pnl = 125.0

        save_grid_state(state.to_dict(), path=path, pool_state=pool.to_dict())
        loaded = load_grid_state(path=path)
        restored = GridState.from_dict(loaded["grid_state"])

        assert restored.center == 87000
        assert len(restored.active_positions) == 1
        assert restored.level_weights[2] == 0.8
        assert loaded["schema_version"] == 2
        assert loaded["pool_state"]["grid_allocated"] == pool.grid_allocated

    def test_load_schema_v1(self, tmp_path):
        import json
        from trader.persistence import load_grid_state

        legacy = {
            "schema_version": 1,
            "grid_state": {
                "center": 87000,
                "upper": 88000,
                "lower": 86000,
                "grid_levels": 5,
                "grid_spacing": 200,
                "grid_balance": 3000,
                "active_positions": [],
                "level_weights": {"1": 0.5},
            },
        }
        path = tmp_path / "grid_v1.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(legacy, f)

        loaded = load_grid_state(path=str(path))

        assert loaded["schema_version"] == 1
        assert loaded["pool_state"] == {}
        assert loaded["grid_state"]["center"] == 87000


class TestBotMainLoopGrid:
    def test_grid_not_triggered_when_disabled(self):
        with patch.object(Config, 'ENABLE_GRID_TRADING', False):
            engine = RegimeEngine()
            grid = V8AtrGrid(api_client=None, notifier=None)
            engine.current_regime = "RANGING"
            assert grid.state is None

    def test_ranging_regime_activates_grid_engine(self):
        with patch.object(Config, 'ENABLE_GRID_TRADING', True):
            engine = RegimeEngine()
            pool = PoolManager()
            grid = V8AtrGrid(api_client=None, notifier=None)

            df = _make_4h_regime_df(adx=15, bbw=[0.08] * 40 + [0.01] * 20, atr=200)
            for i in range(3):
                engine.update(df.iloc[:50 + i])
            assert engine.current_regime == "RANGING"

            pool.activate_grid_pool(10000.0)
            grid.activate(df, pool.get_grid_balance())
            assert grid.state is not None
            assert pool.is_active

    def test_regime_change_triggers_converge(self):
        engine = RegimeEngine()
        pool = PoolManager()
        grid = V8AtrGrid(api_client=None, notifier=None)

        df_ranging = _make_4h_regime_df(adx=15, bbw=[0.08] * 40 + [0.01] * 20, atr=200)
        for i in range(3):
            engine.update(df_ranging.iloc[:50 + i])
        pool.activate_grid_pool(10000.0)
        grid.activate(df_ranging, 3000.0)

        df_trending = _make_4h_regime_df(adx=30, bbw=0.05, atr=200, n=63)
        for i in range(3):
            engine.update(df_trending.iloc[:53 + i])
        assert engine.current_regime == "TRENDING"

        grid.converge()
        assert grid.state.converging is True

    def test_pnl_flows_to_pool_manager(self):
        pool = PoolManager()
        pool.activate_grid_pool(10000.0)
        pool.grid_realized_pnl += 50.0
        assert pool.get_grid_balance() == 3050.0


class TestGridBotExecution:
    def test_api_error_dict_does_not_confirm_close(self, mock_bot):
        mock_bot.grid_engine.state = GridState(
            center=87000,
            upper=88000,
            lower=86000,
            grid_levels=5,
            grid_spacing=200,
            grid_balance=3000,
            active_positions=[{'level': 1, 'side': 'SHORT', 'entry': 87500, 'size': 0.01}],
            level_weights={1: 0.5},
        )
        mock_bot.futures_client = MagicMock()
        mock_bot.futures_client.signed_request_json.return_value = {'error': 'rejected'}

        action = GridAction(type='CLOSE', side='SHORT', level=1, size=0.01, price=87400, entry_price=87500)
        with patch('trader.bot.TelegramNotifier.notify_grid_close'):
            mock_bot._execute_grid_action(action, current_price=87400)

        assert len(mock_bot.grid_engine.state.active_positions) == 1
        assert mock_bot.grid_engine.state.grid_balance == 3000
        assert mock_bot.pool_manager.grid_realized_pnl == 0.0

    def test_confirmed_close_updates_grid_balance(self, mock_bot):
        mock_bot.grid_engine.state = GridState(
            center=87000,
            upper=88000,
            lower=86000,
            grid_levels=5,
            grid_spacing=200,
            grid_balance=3000,
            active_positions=[{'level': 1, 'side': 'SHORT', 'entry': 87500, 'size': 0.01}],
            level_weights={1: 0.5},
        )
        mock_bot.futures_client = MagicMock()
        mock_bot.futures_client.signed_request_json.return_value = {'avgPrice': '87400'}

        action = GridAction(type='CLOSE', side='SHORT', level=1, size=0.01, price=87400, entry_price=87500)
        with patch('trader.bot.TelegramNotifier.notify_grid_close'):
            mock_bot._execute_grid_action(action, current_price=87400)

        assert mock_bot.grid_engine.state.active_positions == []
        assert mock_bot.grid_engine.state.grid_balance == 3001.0
        assert mock_bot.pool_manager.grid_realized_pnl == 1.0
