"""
Test: _handle_close rollback 行為

場景：close_position 丟出 Exception 時，
  - 回傳 False（不從 active_trades 移除）
  - _save_positions 被呼叫（positions.json 確保可重試）
  - 持倉狀態 (is_closed) 維持 False
  - stop_order_id 已移至 pending_stop_cancels（平倉優先邏輯）

正常路徑：close 成功 → 回傳 True
"""

import sys
import pytest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.tests.conftest import make_pm


# ──────────────────────────────────────────────
# Rollback Tests
# ──────────────────────────────────────────────

class TestHandleCloseRollback:

    def test_failure_returns_false(self, mock_bot):
        """close_position 丟 Exception → _handle_close 回傳 False"""
        pm = make_pm()
        mock_bot.execution_engine.close_position = MagicMock(
            side_effect=Exception("Network timeout")
        )
        mock_bot._save_positions = MagicMock()

        result = mock_bot._handle_close(pm, current_price=50000.0)

        assert result is False

    def test_failure_calls_save_positions(self, mock_bot):
        """rollback：_save_positions 必須被呼叫（確保 positions.json 保留持倉）"""
        pm = make_pm()
        mock_bot.execution_engine.close_position = MagicMock(
            side_effect=Exception("API error")
        )
        mock_bot._save_positions = MagicMock()

        mock_bot._handle_close(pm, current_price=50000.0)

        mock_bot._save_positions.assert_called_once()

    def test_failure_position_remains_active(self, mock_bot):
        """rollback：active_trades 仍保留該 symbol（不被移除）"""
        pm = make_pm()
        mock_bot.active_trades['BTC/USDT'] = pm
        mock_bot.execution_engine.close_position = MagicMock(
            side_effect=Exception("Exchange down")
        )
        mock_bot._save_positions = MagicMock()

        mock_bot._handle_close(pm, current_price=50000.0)

        assert 'BTC/USDT' in mock_bot.active_trades

    def test_stop_order_moved_to_pending_before_close(self, mock_bot):
        """
        stop_order_id 在 close 下單前就移入 pending_stop_cancels（平倉優先）。
        即使 close 失敗，stop 已在 pending list、stop_order_id 已清空。
        注意：這是已知邊界情境——下個 cycle 的 pending_stop_cancels 處理
        可能誤取消止損。測試把此行為顯式化。
        """
        pm = make_pm()
        pm.stop_order_id = 'stop_order_123'
        mock_bot.execution_engine.close_position = MagicMock(
            side_effect=Exception("Timeout")
        )
        mock_bot._save_positions = MagicMock()

        mock_bot._handle_close(pm, current_price=50000.0)

        assert 'stop_order_123' in pm.pending_stop_cancels
        assert pm.stop_order_id is None

    # ──────────────────────────────────────────────
    # 正常路徑（Sanity Check）
    # ──────────────────────────────────────────────

    def test_success_returns_true(self, mock_bot):
        """正常路徑：close_position 成功 → 回傳 True"""
        pm = make_pm()
        mock_bot.execution_engine.close_position = MagicMock(
            return_value={'orderId': 'order_456', 'status': 'FILLED'}
        )
        mock_bot._save_positions = MagicMock()

        result = mock_bot._handle_close(pm, current_price=51000.0)

        assert result is True
