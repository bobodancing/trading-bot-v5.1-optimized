import sys
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.config import Config
from trader.strategies.v8_grid import GridState
from trader.tests.conftest import make_pm


class TestGridRuntimeControls:
    def test_monitor_grid_runs_without_active_trades(self, mock_bot):
        mock_bot.active_trades.clear()
        mock_bot._btc_regime_context = {'regime': 'RANGING'}
        mock_bot._scan_grid_signals = MagicMock()

        with patch.object(Config, 'ENABLE_GRID_TRADING', True):
            mock_bot._monitor_grid_state()

        mock_bot._scan_grid_signals.assert_called_once()

    def test_trending_scan_returns_before_trend_entry_and_forces_grid_exit(self, mock_bot):
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
        mock_bot.fetch_ticker = MagicMock(return_value={'last': 87400.0})
        mock_bot.futures_client = MagicMock()
        mock_bot.futures_client.signed_request_json.return_value = {'avgPrice': '87400'}
        mock_bot._execute_trade = MagicMock()
        mock_bot._update_btc_regime_context = MagicMock(return_value=mock_bot._make_btc_context(
            source='regime',
            regime='TRENDING',
            detected='TRENDING',
            direction='LONG',
            candle_time=pd.Timestamp('2026-04-04 00:00:00'),
            reason='regime_updated',
        ))

        with patch.object(Config, 'ENABLE_GRID_TRADING', True), \
             patch('trader.bot.TelegramNotifier.notify_grid_close'), \
             patch('trader.bot.TelegramNotifier.notify_grid_stopped'):
            mock_bot.scan_for_signals()
            assert mock_bot.grid_engine.state is not None
            assert mock_bot.grid_engine.state.converging is True
            mock_bot._execute_trade.assert_not_called()

            mock_bot._monitor_grid_state()

        assert mock_bot.futures_client.signed_request_json.called

    def test_partial_force_close_failure_keeps_remaining_levels_for_retry(self, mock_bot):
        mock_bot.pool_manager.activate_grid_pool(10000.0)
        mock_bot.grid_engine.state = GridState(
            center=87000,
            upper=88000,
            lower=86000,
            grid_levels=5,
            grid_spacing=200,
            grid_balance=3000,
            active_positions=[
                {'level': 1, 'side': 'SHORT', 'entry': 87500, 'size': 0.01},
                {'level': -1, 'side': 'LONG', 'entry': 86500, 'size': 0.01},
            ],
            level_weights={1: 0.5},
            converging=True,
        )
        mock_bot.fetch_ticker = MagicMock(return_value={'last': 87000.0})
        mock_bot.futures_client = MagicMock()
        mock_bot.futures_client.signed_request_json.side_effect = [
            {'avgPrice': '87000'},
            {'error': 'rate_limit'},
            {'avgPrice': '87000'},
        ]
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[])

        with patch.object(Config, 'ENABLE_GRID_TRADING', True), \
             patch('trader.bot.TelegramNotifier.notify_grid_close'):
            mock_bot._monitor_grid_state()
            assert mock_bot.grid_engine.state is not None
            assert len(mock_bot.grid_engine.state.active_positions) == 1

            mock_bot._monitor_grid_state()

        assert mock_bot.grid_engine.state is None
        assert mock_bot.pool_manager.is_active is False
        assert mock_bot.futures_client.signed_request_json.call_count == 3

    def test_converged_grid_waits_for_exchange_flat_before_deactivate(self, mock_bot):
        mock_bot.pool_manager.activate_grid_pool(10000.0)
        mock_bot.grid_engine.state = GridState(
            center=87000,
            upper=88000,
            lower=86000,
            grid_levels=5,
            grid_spacing=200,
            grid_balance=3000,
            active_positions=[],
            level_weights={1: 0.5},
            converging=True,
        )
        mock_bot.risk_manager.get_positions = MagicMock(side_effect=[
            [{'symbol': 'BTCUSDT', 'positionAmt': '0.01', 'positionSide': 'LONG'}],
            [],
        ])

        with patch.object(Config, 'ENABLE_GRID_TRADING', True):
            mock_bot._monitor_grid_state()
            assert mock_bot.grid_engine.state is not None
            assert mock_bot.pool_manager.is_active is True

            mock_bot._monitor_grid_state()

        assert mock_bot.grid_engine.state is None
        assert mock_bot.pool_manager.is_active is False


class TestHedgeAwareSync:
    def test_sync_exchange_positions_keeps_long_when_short_leg_also_exists(self, mock_bot, caplog):
        pm = make_pm(symbol='BTC/USDT', side='LONG')
        pm.total_size = 0.01
        mock_bot.active_trades['BTC/USDT'] = pm
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[
            {'symbol': 'BTCUSDT', 'positionAmt': '0.01', 'positionSide': 'LONG'},
            {'symbol': 'BTCUSDT', 'positionAmt': '-0.02', 'positionSide': 'SHORT'},
        ])

        with caplog.at_level(logging.WARNING):
            mock_bot._sync_exchange_positions()

        assert pm.is_closed is False
        assert any('GHOST_POSITION' in msg and 'SHORT 0.020000' in msg for msg in caplog.messages)

    def test_sync_does_not_flag_grid_btc_position_as_ghost(self, mock_bot, caplog):
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
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[
            {'symbol': 'BTCUSDT', 'positionAmt': '-0.01', 'positionSide': 'SHORT'},
        ])

        with caplog.at_level(logging.WARNING):
            mock_bot._sync_exchange_positions()

        assert not any('GHOST_POSITION' in msg for msg in caplog.messages)
