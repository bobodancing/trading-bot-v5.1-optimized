"""共用 fixtures for TradingBotV6 integration tests"""

import sys
import pytest
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.bot import TradingBotV6
from trader.positions import PositionManager
from trader.risk.manager import PrecisionHandler


def make_pm(**kwargs) -> PositionManager:
    """建立最小可用的 PositionManager（測試用）"""
    defaults = dict(
        symbol='BTC/USDT',
        side='LONG',
        entry_price=50000.0,
        stop_loss=48000.0,
        position_size=0.01,
    )
    defaults.update(kwargs)
    pm = PositionManager(**defaults)
    pm.entry_time = datetime.now(timezone.utc)
    pm.highest_price = 51000.0
    pm.lowest_price = 49000.0
    pm.initial_r = 20.0      # 防止 / 0
    pm.market_regime = 'TRENDING'
    return pm


@pytest.fixture
def mock_bot(tmp_path):
    """
    TradingBotV6 instance，所有外部 I/O 已 mock：
      - _init_exchange → MagicMock（阻斷 ccxt 網路連線）
      - PrecisionHandler._load_exchange_info → no-op（阻斷 Binance exchangeInfo HTTP）
      - _restore_positions → no-op（防止載入真實 positions.json）
    PositionPersistence 和 PerformanceDB 使用 tmp_path（測完自動清除）
    """
    mock_exchange = MagicMock()
    mock_exchange.load_markets.return_value = {}
    mock_exchange.markets = {}

    with patch.object(TradingBotV6, '_init_exchange', return_value=mock_exchange), \
         patch.object(PrecisionHandler, '_load_exchange_info'), \
         patch.object(TradingBotV6, '_restore_positions'), \
         patch('trader.bot.Config.POSITIONS_JSON_PATH', str(tmp_path / 'positions.json')), \
         patch('trader.bot.Config.DB_PATH', str(tmp_path / 'perf.db')):
        bot = TradingBotV6()

    # 覆蓋 perf_db 寫入（避免 SQLite 問題）
    bot.perf_db.record_trade = MagicMock()

    yield bot


# ──────────────────────────────────────────────
# StatefulMockEngine — Integration Test 用
# ──────────────────────────────────────────────

class StatefulMockEngine:
    """
    有狀態的 mock execution engine，追蹤：
    - balance（開倉扣 notional，平倉加回 notional ± PnL）
    - positions（{symbol: {side, size, entry_price}}）
    - open_stop_orders（{order_id: {symbol, side, size, stop_price}}）

    與真實 OrderExecutionEngine 介面完全一致。
    """

    def __init__(self, initial_balance: float = 10000.0):
        self.balance = initial_balance
        self.positions: dict = {}       # {symbol: {side, size, entry_price}}
        self.open_stops: dict = {}      # {order_id: {symbol, side, size, stop_price}}
        self.order_counter = 0
        self.trade_log: list = []       # 記錄所有操作
        self._fault: 'FaultInjector | None' = None

    def attach_fault_injector(self, fi: 'FaultInjector'):
        self._fault = fi

    def _next_order_id(self) -> str:
        self.order_counter += 1
        return f"mock_order_{self.order_counter}"

    def _check_fault(self, method_name: str):
        """每個 API call 進入時先過 fault injector"""
        if self._fault:
            self._fault.check(method_name)

    def set_leverage(self, symbol: str) -> bool:
        self._check_fault('set_leverage')
        return True

    def create_order(self, symbol: str, side: str, quantity: float) -> dict:
        """開倉。side='BUY'→LONG, 'SELL'→SHORT"""
        self._check_fault('create_order')

        order_id = self._next_order_id()
        self.trade_log.append({
            'action': 'create_order', 'symbol': symbol,
            'side': side, 'quantity': quantity, 'order_id': order_id,
        })
        return {
            'orderId': order_id,
            'avgPrice': '0',  # bot 用 _extract_fill_price，fallback 到信號價
            'status': 'FILLED',
            'executedQty': str(quantity),
        }

    def close_position(self, symbol: str, side: str, quantity: float) -> dict:
        """平倉"""
        self._check_fault('close_position')
        order_id = self._next_order_id()
        self.trade_log.append({
            'action': 'close_position', 'symbol': symbol,
            'side': side, 'quantity': quantity, 'order_id': order_id,
        })
        return {
            'orderId': order_id,
            'avgPrice': '0',
            'status': 'FILLED',
            'executedQty': str(quantity),
        }

    def place_hard_stop_loss(self, symbol: str, side: str, size: float,
                              stop_price: float) -> str:
        """設置硬止損"""
        self._check_fault('place_hard_stop_loss')
        order_id = self._next_order_id()
        self.open_stops[order_id] = {
            'symbol': symbol, 'side': side,
            'size': size, 'stop_price': stop_price,
        }
        self.trade_log.append({
            'action': 'place_stop', 'symbol': symbol,
            'order_id': order_id, 'stop_price': stop_price,
        })
        return order_id

    def cancel_stop_loss_order(self, symbol: str, order_id: str | None) -> bool:
        """取消止損"""
        self._check_fault('cancel_stop_loss_order')
        if order_id and order_id in self.open_stops:
            del self.open_stops[order_id]
        self.trade_log.append({
            'action': 'cancel_stop', 'symbol': symbol, 'order_id': order_id,
        })
        return True

    def update_hard_stop_loss(self, pm, new_stop: float):
        """更新 trailing stop（PositionManager 呼叫）"""
        self.cancel_stop_loss_order(pm.symbol, pm.stop_order_id)
        pm.stop_order_id = self.place_hard_stop_loss(
            pm.symbol, pm.side, pm.total_size, new_stop
        )


class FaultInjector:
    """
    故障注入器。可在指定的 method call 上觸發 Exception。

    用法：
        fi = FaultInjector()
        fi.set_fault('close_position', Exception("API 5xx"), times=1)
        engine.attach_fault_injector(fi)
        # 下一次 close_position 會丟 Exception，之後恢復正常
    """

    def __init__(self):
        self._faults: dict = {}  # {method_name: {'error': Exception, 'remaining': int}}

    def set_fault(self, method_name: str, error: Exception, times: int = 1):
        """設定某 method 在接下來 N 次呼叫時丟出 error"""
        self._faults[method_name] = {'error': error, 'remaining': times}

    def clear(self):
        """清除所有故障設定"""
        self._faults.clear()

    def check(self, method_name: str):
        """每次 API call 前呼叫，有故障則 raise"""
        fault = self._faults.get(method_name)
        if fault and fault['remaining'] > 0:
            fault['remaining'] -= 1
            if fault['remaining'] <= 0:
                del self._faults[method_name]
            raise fault['error']


@pytest.fixture
def integration_bot(tmp_path):
    """
    Integration test 用的 TradingBotV6：
    - StatefulMockEngine（有狀態的 execution engine）
    - FaultInjector（可注入故障）
    - V6_DRY_RUN = False（走完整 _execute_trade / _handle_close 路徑）
    - perf_db 使用 tmp_path（測完自動清除）

    回傳 (bot, engine, fault_injector) tuple。
    """
    from trader.config import Config

    engine = StatefulMockEngine(initial_balance=10000.0)
    fi = FaultInjector()
    engine.attach_fault_injector(fi)

    mock_exchange = MagicMock()
    mock_exchange.load_markets.return_value = {}
    mock_exchange.markets = {}

    pos_path = str(tmp_path / 'positions.json')
    db_path = str(tmp_path / 'perf.db')

    with patch.object(TradingBotV6, '_init_exchange', return_value=mock_exchange), \
         patch.object(PrecisionHandler, '_load_exchange_info'), \
         patch.object(TradingBotV6, '_restore_positions'), \
         patch('trader.bot.Config.POSITIONS_JSON_PATH', pos_path), \
         patch('trader.bot.Config.DB_PATH', db_path):
        bot = TradingBotV6()

    # 注入 StatefulMockEngine
    bot.execution_engine = engine

    # fetch_ticker → 可由 test 設定 side_effect
    bot.exchange.fetch_ticker = MagicMock(return_value={
        'last': 50000.0, 'bid': 49999.0, 'ask': 50001.0,
    })

    # data_provider.fetch_ohlcv → MagicMock（由各 test 自行設回傳值）
    bot.data_provider = MagicMock()
    bot.data_provider.fetch_ohlcv = MagicMock(return_value=pd.DataFrame())

    # risk_manager.get_balance → 固定值（阻斷 Binance API）
    bot.risk_manager.get_balance = MagicMock(return_value=10000.0)

    # risk_manager.get_positions → 預設回空 list（sync 用）
    bot.risk_manager.get_positions = MagicMock(return_value=[])

    # precision_handler → 直接回傳原值（不做精度調整）
    bot.precision_handler.round_amount_up = MagicMock(side_effect=lambda sym, amt, price: amt)
    bot.precision_handler.round_amount = MagicMock(side_effect=lambda sym, amt: amt)
    bot.precision_handler.check_limits = MagicMock(return_value=True)

    # persistence → 使用真實 PositionPersistence（寫 tmp_path）
    # 不 mock，確保 _save_positions / _restore_positions 走真實路徑

    # 儲存原始 Config 值
    _orig = {
        'V6_DRY_RUN': Config.V6_DRY_RUN,
        'USE_SCANNER_SYMBOLS': Config.USE_SCANNER_SYMBOLS,
        'SYMBOLS': Config.SYMBOLS,
        'TELEGRAM_ENABLED': Config.TELEGRAM_ENABLED,
    }

    # Config 設定
    Config.V6_DRY_RUN = False
    Config.USE_SCANNER_SYMBOLS = False
    Config.SYMBOLS = ['BTC/USDT']
    Config.TELEGRAM_ENABLED = False

    yield bot, engine, fi

    # teardown：還原所有 Config 至原始值
    for k, v in _orig.items():
        setattr(Config, k, v)
