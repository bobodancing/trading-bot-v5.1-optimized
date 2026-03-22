"""Tests for HTML escape in TelegramNotifier"""
import html
from unittest.mock import patch, MagicMock
import pytest

from trader.infrastructure.notifier import TelegramNotifier


@pytest.fixture(autouse=True)
def enable_telegram():
    with patch('trader.infrastructure.notifier.Config') as mock_cfg:
        mock_cfg.TELEGRAM_ENABLED = True
        mock_cfg.TELEGRAM_BOT_TOKEN = 'fake-token'
        mock_cfg.TELEGRAM_CHAT_ID = '12345'
        yield mock_cfg


class TestNotifierEscape:

    @patch('trader.infrastructure.notifier.requests.post')
    def test_notify_warning_escapes_html(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        msg = '<script>alert("xss")</script>&param=1'
        TelegramNotifier.notify_warning(msg)
        payload = mock_post.call_args.kwargs.get('data', {})
        text = payload.get('text', '')
        assert '&lt;script&gt;' in text
        assert '&amp;param=1' in text

    @patch('trader.infrastructure.notifier.requests.post')
    def test_notify_action_escapes_details(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        TelegramNotifier.notify_action('BTCUSDT', 'test<action>', 100.0, '<b>hack</b>')
        payload = mock_post.call_args.kwargs.get('data', {})
        text = payload.get('text', '')
        assert '&lt;b&gt;hack&lt;/b&gt;' in text
        assert 'test&lt;action&gt;' in text

    @patch('trader.infrastructure.notifier.requests.post')
    def test_notify_signal_escapes_symbol(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        details = {
            'signal_strength': 'strong',
            'signal_tier': 'A',
            'side': 'LONG',
            'market_state': 'trend<up>',
            'vol_ratio': 1.5,
            'entry_price': 100.0,
            'stop_loss': 95.0,
            'target_ref': '$110&more',
            'position_size': 0.01,
            'r15_target': 'N/A',
        }
        TelegramNotifier.notify_signal('<TEST>', details)
        payload = mock_post.call_args.kwargs.get('data', {})
        text = payload.get('text', '')
        assert '&lt;TEST&gt;' in text
        assert 'trend&lt;up&gt;' in text
        assert '$110&amp;more' in text

    @patch('trader.infrastructure.notifier.requests.post')
    def test_notify_exit_escapes_reason(self, mock_post):
        mock_post.return_value = MagicMock(ok=True)
        details = {
            'side': 'LONG',
            'entry_price': 100.0,
            'exit_reason': 'a&b<c>',
            'pnl_pct': 1.5,
            'position_size': 0.01,
        }
        TelegramNotifier.notify_exit('BTCUSDT', details)
        payload = mock_post.call_args.kwargs.get('data', {})
        text = payload.get('text', '')
        assert 'a&amp;b&lt;c&gt;' in text

    @patch('trader.infrastructure.notifier.logger')
    @patch('trader.infrastructure.notifier.requests.post')
    def test_send_message_logs_error_on_bad_status(self, mock_post, mock_logger):
        mock_resp = MagicMock()
        mock_resp.ok = False
        mock_resp.status_code = 400
        mock_resp.text = 'Bad Request: cannot parse HTML'
        mock_post.return_value = mock_resp
        TelegramNotifier.send_message('test')
        mock_logger.error.assert_called_once()
        assert '400' in mock_logger.error.call_args[0][0]
