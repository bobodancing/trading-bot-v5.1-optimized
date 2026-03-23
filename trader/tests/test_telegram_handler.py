"""Tests for TelegramCommandHandler"""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock, PropertyMock
import pytest

from trader.infrastructure.telegram_handler import TelegramCommandHandler


@pytest.fixture
def mock_bot():
    bot = MagicMock()
    bot.active_trades = {}
    bot._start_time = datetime.now(timezone.utc) - timedelta(hours=2)
    bot.initial_balance = 10000.0
    bot.risk_manager.get_balance.return_value = 10500.0
    return bot


@pytest.fixture
def handler(mock_bot):
    with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
        mock_cfg.TELEGRAM_ENABLED = True
        mock_cfg.TELEGRAM_BOT_TOKEN = 'fake-token'
        mock_cfg.TELEGRAM_CHAT_ID = '12345'
        mock_cfg.V6_DRY_RUN = False
        mock_cfg.SYMBOLS = ['BTC/USDT', 'ETH/USDT']
        h = TelegramCommandHandler(mock_bot)
        yield h


class TestTelegramCommands:

    def test_cmd_positions_empty(self, handler):
        result = handler._cmd_positions()
        assert '無開倉部位' in result

    def test_cmd_positions_with_trades(self, handler):
        pm = MagicMock()
        pm.side = 'LONG'
        pm.is_v6_pyramid = True
        pm.avg_entry = 100.0
        pm.stop_loss = 95.0
        pm.position_size = 0.5
        pm.stage = 2
        pm.signal_tier = 'A'
        pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=3)
        pm.highest_price = 105.0
        pm.lowest_price = 98.0
        handler.bot.active_trades = {'BTC/USDT': pm}

        result = handler._cmd_positions()
        assert 'BTC/USDT' in result
        assert 'LONG' in result
        assert 'V6' in result
        assert 'Stage 2' in result
        assert '開倉部位 (1)' in result

    def test_cmd_status(self, handler):
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.V6_DRY_RUN = False
            mock_cfg.SYMBOLS = ['BTC/USDT', 'ETH/USDT']
            result = handler._cmd_status()
        assert 'Bot Status' in result
        assert '活躍倉位: 0' in result

    def test_cmd_balance(self, handler):
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.V6_DRY_RUN = False
            result = handler._cmd_balance()
        assert '$10500.00' in result
        assert '+$500.00' in result

    def test_cmd_help(self, handler):
        result = handler._cmd_help()
        assert '/positions' in result
        assert '/status' in result
        assert '/balance' in result


class TestTelegramSecurity:

    @patch('trader.infrastructure.telegram_handler.requests.get')
    @patch('trader.infrastructure.telegram_handler.requests.post')
    def test_ignores_wrong_chat_id(self, mock_post, mock_get, handler):
        """只回應 Config.TELEGRAM_CHAT_ID"""
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {'result': [{
                'update_id': 1,
                'message': {
                    'chat': {'id': 99999},  # 不是 12345
                    'text': '/positions',
                }
            }]}
        )
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.TELEGRAM_ENABLED = True
            mock_cfg.TELEGRAM_BOT_TOKEN = 'fake-token'
            mock_cfg.TELEGRAM_CHAT_ID = '12345'
            handler.poll()
        mock_post.assert_not_called()

    @patch('trader.infrastructure.telegram_handler.requests.get')
    @patch('trader.infrastructure.telegram_handler.requests.post')
    def test_responds_correct_chat_id(self, mock_post, mock_get, handler):
        """正確 chat_id 會回覆"""
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {'result': [{
                'update_id': 1,
                'message': {
                    'chat': {'id': 12345},
                    'text': '/help',
                }
            }]}
        )
        mock_post.return_value = MagicMock(ok=True)
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.TELEGRAM_ENABLED = True
            mock_cfg.TELEGRAM_BOT_TOKEN = 'fake-token'
            mock_cfg.TELEGRAM_CHAT_ID = '12345'
            handler.poll()
        mock_post.assert_called_once()

    @patch('trader.infrastructure.telegram_handler.requests.get')
    def test_ignores_non_command(self, mock_get, handler):
        """非 / 開頭的訊息不處理"""
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {'result': [{
                'update_id': 1,
                'message': {
                    'chat': {'id': 12345},
                    'text': 'hello',
                }
            }]}
        )
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.TELEGRAM_ENABLED = True
            mock_cfg.TELEGRAM_BOT_TOKEN = 'fake-token'
            mock_cfg.TELEGRAM_CHAT_ID = '12345'
            handler.poll()
        # 不應 crash 也不應回覆


class TestTelegramPolling:

    @patch('trader.infrastructure.telegram_handler.requests.get')
    def test_updates_last_update_id(self, mock_get, handler):
        """update_id 會遞增，避免重複處理"""
        mock_get.return_value = MagicMock(
            ok=True,
            json=lambda: {'result': [{
                'update_id': 42,
                'message': {
                    'chat': {'id': 12345},
                    'text': 'hello',
                }
            }]}
        )
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.TELEGRAM_ENABLED = True
            mock_cfg.TELEGRAM_BOT_TOKEN = 'fake-token'
            mock_cfg.TELEGRAM_CHAT_ID = '12345'
            handler.poll()
        assert handler.last_update_id == 42

    def test_poll_disabled(self, handler):
        """TELEGRAM_ENABLED=False 時不 poll"""
        with patch('trader.infrastructure.telegram_handler.Config') as mock_cfg:
            mock_cfg.TELEGRAM_ENABLED = False
            with patch('trader.infrastructure.telegram_handler.requests.get') as mock_get:
                handler.poll()
                mock_get.assert_not_called()
