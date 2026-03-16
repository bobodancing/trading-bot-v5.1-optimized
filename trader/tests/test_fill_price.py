"""
Test: 成交均價修正（fill price fix）

_extract_fill_price 靜態方法：
  - 正確擷取 BinanceFuturesClient 回傳的 avgPrice
  - 正確擷取 CCXT 回傳的 average
  - avgPrice 為 "0" 或缺失時，fallback 到信號價

Stage 1 整合：
  - mock _futures_create_order 回傳含 avgPrice 的 dict
  - 確認 pm.avg_entry 使用 fill price，非信號價
  - mock order 無 avgPrice 時，fallback 不拋錯
"""

import sys
import pytest
import pandas as pd
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.bot import TradingBotV6


# ──────────────────────────────────────────────
# Unit tests: _extract_fill_price
# ──────────────────────────────────────────────

class TestExtractFillPrice:

    def test_binance_avgprice_string(self):
        """BinanceFuturesClient 路徑：avgPrice 字串 → 正確轉 float"""
        result = {'avgPrice': '66850.25', 'executedQty': '0.007'}
        assert TradingBotV6._extract_fill_price(result, 66812.30) == 66850.25

    def test_ccxt_average_float(self):
        """CCXT 路徑：average float → 直接回傳"""
        result = {'average': 85.42, 'filled': 5.98}
        assert TradingBotV6._extract_fill_price(result, 85.0) == 85.42

    def test_fallback_when_avgprice_missing(self):
        """avgPrice 欄位不存在 → fallback 到信號價"""
        result = {'orderId': 123, 'status': 'FILLED'}
        assert TradingBotV6._extract_fill_price(result, 85.0) == 85.0

    def test_fallback_when_avgprice_zero(self):
        """avgPrice 為 '0' → fallback（Binance 偶爾在 FILLED 前回 0）"""
        result = {'avgPrice': '0', 'executedQty': '0.007'}
        assert TradingBotV6._extract_fill_price(result, 66812.30) == 66812.30

    def test_fallback_when_empty_dict(self):
        """空 dict → fallback，不拋 Exception"""
        assert TradingBotV6._extract_fill_price({}, 1990.0) == 1990.0

    def test_fallback_when_avgprice_invalid(self):
        """avgPrice 非數字字串 → fallback，不拋 Exception"""
        result = {'avgPrice': 'N/A'}
        assert TradingBotV6._extract_fill_price(result, 1990.0) == 1990.0


# ──────────────────────────────────────────────
# Integration tests: Stage 1 使用 fill price
# ──────────────────────────────────────────────

class TestStage1FillPriceIntegration:

    def _make_signal(self, entry_price: float) -> dict:
        return {
            'side': 'LONG',
            'entry_price': entry_price,
            'stop_loss': entry_price * 0.95,
            'neckline': entry_price * 1.01,
            'signal_tier': 'A',
            'market_regime': 'STRONG',
            'signal_type': '2B',
            'atr': entry_price * 0.01,
            'vol_ratio': 1.5,
            'btc_trend': 'BULL',
            '_market_reason': 'test',
            '_trend_desc': 'test',
            '_mtf_reason': 'test',
            'entry_adx': 30.0,
            'fakeout_depth_atr': 0.5,
        }

    def _make_df(self):
        return pd.DataFrame({'close': [50000.0] * 5})

    def _setup_mocks(self, mock_bot, order_result: dict):
        mock_bot._futures_create_order = MagicMock(return_value=order_result)
        mock_bot.risk_manager.get_balance = MagicMock(return_value=5000.0)
        mock_bot.precision_handler.round_amount_up = MagicMock(return_value=0.01)
        mock_bot.precision_handler.check_limits = MagicMock(return_value=True)
        mock_bot._place_hard_stop_loss = MagicMock(return_value='order_123')
        mock_bot._save_positions = MagicMock()
        mock_bot.notifier = MagicMock()

    def test_stage1_uses_fill_price(self, mock_bot):
        """fill price 存在時，pm.avg_entry 應使用 fill price，非信號價"""
        signal_price = 50000.0
        fill_price = 50023.5  # slippage 模擬

        self._setup_mocks(mock_bot, {'avgPrice': str(fill_price), 'executedQty': '0.01'})

        with patch('trader.bot.TelegramNotifier.notify_signal'), \
             patch('trader.bot.Config.V6_DRY_RUN', False):
            mock_bot._execute_trade(
                'BTC/USDT', self._make_signal(signal_price), '2B', 1.0, self._make_df()
            )

        assert 'BTC/USDT' in mock_bot.active_trades
        pm = mock_bot.active_trades['BTC/USDT']
        assert abs(pm.avg_entry - fill_price) < 0.01, \
            f"avg_entry 應為 fill price {fill_price}，得到 {pm.avg_entry}"

    def test_stage1_fallback_when_no_avgprice(self, mock_bot):
        """order response 無 avgPrice → fallback 信號價，不拋錯"""
        signal_price = 50000.0

        self._setup_mocks(mock_bot, {'orderId': 999, 'status': 'FILLED'})  # 無 avgPrice

        with patch('trader.bot.TelegramNotifier.notify_signal'), \
             patch('trader.bot.Config.V6_DRY_RUN', False):
            mock_bot._execute_trade(
                'BTC/USDT', self._make_signal(signal_price), '2B', 1.0, self._make_df()
            )

        assert 'BTC/USDT' in mock_bot.active_trades
        pm = mock_bot.active_trades['BTC/USDT']
        assert abs(pm.avg_entry - signal_price) < 0.01, \
            f"應 fallback 到信號價 {signal_price}，得到 {pm.avg_entry}"
