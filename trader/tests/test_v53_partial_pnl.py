"""
Test: V5.3 減倉 PnL 追蹤（Partial Close PnL Fix）

Unit tests:
  - PositionManager original_size / realized_partial_pnl 初始化
  - to_dict / from_dict 持久化與向後相容

Integration tests:
  - _handle_v53_reduce 累積 partial PnL
  - _handle_close 合計 partial + final PnL
"""

import sys
import pytest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.positions import PositionManager


# ──────────────────────────────────────────────
# Unit tests: PositionManager 新欄位
# ──────────────────────────────────────────────

class TestPartialPnlFields:

    def test_original_size_initialized(self):
        """original_size 應等於建立時的 position_size"""
        pm = PositionManager('BTC/USDT', 'LONG', 100.0, 95.0, 0.5)
        assert pm.original_size == 0.5

    def test_realized_partial_pnl_initialized_zero(self):
        """realized_partial_pnl 初始為 0"""
        pm = PositionManager('BTC/USDT', 'LONG', 100.0, 95.0, 0.5)
        assert pm.realized_partial_pnl == 0.0

    def test_to_dict_includes_new_fields(self):
        """to_dict 包含 original_size 和 realized_partial_pnl"""
        pm = PositionManager('BTC/USDT', 'LONG', 100.0, 95.0, 0.5)
        pm.realized_partial_pnl = 25.5
        d = pm.to_dict()
        assert d['original_size'] == 0.5
        assert d['realized_partial_pnl'] == 25.5

    def test_from_dict_restores_new_fields(self):
        """from_dict 正確還原 original_size 和 realized_partial_pnl"""
        pm = PositionManager('BTC/USDT', 'LONG', 100.0, 95.0, 0.5)
        pm.realized_partial_pnl = 42.0
        d = pm.to_dict()
        pm2 = PositionManager.from_dict(d)
        assert pm2.original_size == 0.5
        assert pm2.realized_partial_pnl == 42.0

    def test_from_dict_backward_compat(self):
        """舊 positions.json（無新欄位）→ fallback 不拋錯"""
        pm = PositionManager('BTC/USDT', 'LONG', 100.0, 95.0, 0.5, is_v6_pyramid=False)
        d = pm.to_dict()
        # 模擬舊格式：刪除新欄位
        del d['original_size']
        del d['realized_partial_pnl']
        pm2 = PositionManager.from_dict(d)
        # original_size fallback 到 entries[0].size
        assert pm2.original_size == 0.5
        assert pm2.realized_partial_pnl == 0.0


# ──────────────────────────────────────────────
# Integration tests: 減倉 PnL 累積
# ──────────────────────────────────────────────

class TestPartialPnlIntegration:

    def test_v53_reduce_accumulates_pnl_long(self, mock_bot):
        """LONG 減倉時，partial PnL 正確累積"""
        from trader.config import ConfigV6 as Config

        pm = PositionManager(
            'ETH/USDT', 'LONG', 2000.0, 1900.0, 10.0,
            is_v6_pyramid=False, initial_r=100.0
        )
        mock_bot.active_trades['ETH/USDT'] = pm

        # mock 平倉訂單
        mock_bot._futures_close_position = MagicMock(
            return_value={'avgPrice': '2200.0'}
        )
        mock_bot._cancel_stop_loss_order = MagicMock(return_value=True)
        mock_bot._place_hard_stop_loss = MagicMock(return_value='sl_123')
        mock_bot._save_positions = MagicMock()

        # 1.5R 減倉（30%，3.0 個 @ $2200）
        current_price = 2200.0
        mock_bot._handle_v53_reduce(pm, Config.FIRST_PARTIAL_PCT, "1.5R", current_price)

        expected_pnl = 3.0 * (2200.0 - 2000.0)  # 3.0 × 200 = 600
        assert abs(pm.realized_partial_pnl - expected_pnl) < 1.0
        assert pm.total_size < 10.0  # 倉位已減少

    def test_v53_reduce_accumulates_pnl_short(self, mock_bot):
        """SHORT 減倉時，partial PnL 正確累積"""
        from trader.config import ConfigV6 as Config

        pm = PositionManager(
            'ETH/USDT', 'SHORT', 2000.0, 2100.0, 10.0,
            is_v6_pyramid=False, initial_r=100.0
        )
        mock_bot.active_trades['ETH/USDT'] = pm

        mock_bot._futures_close_position = MagicMock(
            return_value={'avgPrice': '1800.0'}
        )
        mock_bot._cancel_stop_loss_order = MagicMock(return_value=True)
        mock_bot._place_hard_stop_loss = MagicMock(return_value='sl_123')
        mock_bot._save_positions = MagicMock()

        current_price = 1800.0
        mock_bot._handle_v53_reduce(pm, Config.FIRST_PARTIAL_PCT, "1.5R", current_price)

        expected_pnl = 3.0 * (2000.0 - 1800.0)  # 3.0 × 200 = 600
        assert abs(pm.realized_partial_pnl - expected_pnl) < 1.0

    def test_handle_close_uses_total_pnl(self, mock_bot):
        """_handle_close 的 pnl_usdt 應合計 partial + final"""
        pm = PositionManager(
            'ETH/USDT', 'LONG', 2000.0, 1900.0, 10.0,
            is_v6_pyramid=False, initial_r=100.0
        )
        # 模擬已經過兩次減倉
        pm.total_size = 4.9       # 剩 49%
        pm.original_size = 10.0
        pm.realized_partial_pnl = 1200.0  # 累積減倉利潤
        pm.is_closed = True
        pm.exit_reason = 'trailing_sl'
        mock_bot.active_trades['ETH/USDT'] = pm

        mock_bot._futures_close_position = MagicMock(return_value={})
        mock_bot._save_positions = MagicMock()
        mock_bot.perf_db = MagicMock()

        current_price = 2300.0
        result = mock_bot._handle_close(pm, current_price)
        assert result is True

        # 檢查 perf_db.record_trade 被呼叫，且 pnl_usdt 包含 partial
        call_args = mock_bot.perf_db.record_trade.call_args[0][0]
        final_pnl = 4.9 * (2300.0 - 2000.0)  # 4.9 × 300 = 1470
        total_pnl = final_pnl + 1200.0         # 1470 + 1200 = 2670
        assert abs(call_args['pnl_usdt'] - total_pnl) < 1.0
        assert call_args['original_size'] == 10.0
        assert call_args['partial_pnl_usdt'] == 1200.0
