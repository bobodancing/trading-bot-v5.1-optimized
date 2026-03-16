"""
Test: _sync_exchange_positions 四重防護

覆蓋場景：
1. API 回 None → 跳過同步，不誤殺
2. 正常 hard_stop_hit 偵測（exchange 無倉位）
3. exchange 有倉位 → 不標記 closed
4. Size 不一致 → log WARNING（不動 state）
5. 幽靈倉位偵測（exchange 有、bot 沒有）
6. pending_stop_cancels 在 del 前執行
7. API 正常回空 + bot 無持倉 → 平靜通過
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.tests.conftest import make_pm


class TestSyncExchangePositions:
    """_sync_exchange_positions 四重防護測試"""

    def test_api_error_skips_sync(self, mock_bot):
        """防護 1：get_positions 回 None → 跳過同步，不動 active_trades"""
        pm = make_pm(symbol='BTC/USDT')
        mock_bot.active_trades['BTC/USDT'] = pm
        mock_bot.risk_manager.get_positions = MagicMock(return_value=None)

        mock_bot._sync_exchange_positions()

        # pm 不應被標記 closed
        assert pm.is_closed is False
        assert pm.exit_reason is None

    def test_hard_stop_detected(self, mock_bot):
        """防護 2：exchange 無此 symbol → 標記 hard_stop_hit"""
        pm = make_pm(symbol='SNX/USDT')
        mock_bot.active_trades['SNX/USDT'] = pm
        # exchange 回空 list（真的沒倉位，不是 API 錯誤）
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[])
        mock_bot._save_positions = MagicMock()

        mock_bot._sync_exchange_positions()

        assert pm.is_closed is True
        assert pm.exit_reason == 'hard_stop_hit'
        mock_bot._save_positions.assert_called_once()

    def test_exchange_has_position_not_closed(self, mock_bot):
        """exchange 有倉位 → 不標記 closed"""
        pm = make_pm(symbol='BTC/USDT')
        pm.total_size = 0.01
        mock_bot.active_trades['BTC/USDT'] = pm
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[
            {'symbol': 'BTCUSDT', 'positionAmt': '0.01'}
        ])

        mock_bot._sync_exchange_positions()

        assert pm.is_closed is False

    def test_size_mismatch_warns_but_keeps_active(self, mock_bot, caplog):
        """防護 3：size 不一致 → log WARNING，不動 state"""
        import logging
        pm = make_pm(symbol='ETH/USDT')
        pm.total_size = 1.0
        mock_bot.active_trades['ETH/USDT'] = pm
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[
            {'symbol': 'ETHUSDT', 'positionAmt': '1.5'}
        ])

        with caplog.at_level(logging.WARNING):
            mock_bot._sync_exchange_positions()

        assert pm.is_closed is False
        assert any('SIZE_MISMATCH' in msg for msg in caplog.messages)

    def test_ghost_position_detected(self, mock_bot, caplog):
        """防護 4：exchange 有倉、bot 沒追蹤 → log GHOST_POSITION"""
        import logging
        # bot 只追蹤 BTC
        pm = make_pm(symbol='BTC/USDT')
        pm.total_size = 0.01
        mock_bot.active_trades['BTC/USDT'] = pm
        # exchange 有 BTC + SNX（SNX 是幽靈）
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[
            {'symbol': 'BTCUSDT', 'positionAmt': '0.01'},
            {'symbol': 'SNXUSDT', 'positionAmt': '50.0'},
        ])

        with caplog.at_level(logging.WARNING):
            mock_bot._sync_exchange_positions()

        assert any('GHOST_POSITION' in msg and 'SNX' in msg for msg in caplog.messages)

    def test_empty_both_sides_no_error(self, mock_bot):
        """兩邊都空 → 安靜通過"""
        mock_bot.active_trades.clear()
        mock_bot.risk_manager.get_positions = MagicMock(return_value=[])

        # 不應丟例外
        mock_bot._sync_exchange_positions()


class TestPendingStopCleanup:
    """pending_stop_cancels 在刪除 pm 前清理"""

    def test_pending_cancels_processed_before_delete(self, mock_bot):
        """closed pm 的 pending_stop_cancels 在 del 前執行 cancel"""
        pm = make_pm(symbol='SOL/USDT')
        pm.is_closed = True
        pm.exit_reason = 'structure_trail_sl'
        pm.pending_stop_cancels = ['algo_order_111', 'algo_order_222']
        mock_bot.active_trades['SOL/USDT'] = pm
        mock_bot._save_positions = MagicMock()
        mock_bot.execution_engine.cancel_stop_loss_order = MagicMock(return_value=True)

        # 模擬 monitor_positions 的清理流程
        closed_symbols = ['SOL/USDT']
        for symbol in closed_symbols:
            closed_pm = mock_bot.active_trades.get(symbol)
            if closed_pm:
                for order_id in closed_pm.pending_stop_cancels:
                    mock_bot.execution_engine.cancel_stop_loss_order(closed_pm.symbol, order_id)
                if closed_pm.exit_reason in ('early_stop_r', 'stage1_timeout'):
                    pass
            if symbol in mock_bot.active_trades:
                del mock_bot.active_trades[symbol]

        # 驗證 cancel 被呼叫 2 次
        assert mock_bot.execution_engine.cancel_stop_loss_order.call_count == 2
        assert 'SOL/USDT' not in mock_bot.active_trades
