"""
訂單執行引擎

將 TradingBotV6 中所有「發送 API 下單」的底層方法集中封裝，
讓 TradingBotV6（大腦/策略）只需呼叫 self.execution_engine.xxx()，
未來可輕易替換為模擬交易引擎。

從 v6/bot.py 提取的方法：
  _futures_set_leverage     → set_leverage
  _futures_create_order     → create_order
  _futures_close_position   → close_position
  _place_hard_stop_loss     → place_hard_stop_loss
  _cancel_stop_loss_order   → cancel_stop_loss_order
  _update_hard_stop_loss    → update_hard_stop_loss
"""

import logging
from typing import Optional

from trader.config import Config
from trader.infrastructure.api_client import BinanceFuturesClient
from trader.risk.manager import PrecisionHandler

logger = logging.getLogger(__name__)


class OrderExecutionEngine:
    """訂單執行引擎：封裝所有與交易所 API 的實際下單互動"""

    def __init__(
        self,
        exchange,
        futures_client: BinanceFuturesClient,
        precision_handler: PrecisionHandler,
        hedge_mode: bool = False,
    ):
        """
        Args:
            exchange: 已初始化的 ccxt exchange（non-sandbox fallback 用）
            futures_client: BinanceFuturesClient（簽章下單用）
            precision_handler: PrecisionHandler（數量格式化用）
        """
        self.exchange = exchange
        self.futures_client = futures_client
        self.precision_handler = precision_handler
        self.hedge_mode: bool = bool(hedge_mode)

    # ==================== 槓桿設置 ====================

    def set_leverage(self, symbol: str) -> bool:
        """設置槓桿"""
        symbol_id = symbol.replace('/', '')
        result = self.futures_client.signed_request_json('POST', '/fapi/v1/leverage', {
            'symbol': symbol_id, 'leverage': Config.LEVERAGE
        })
        return 'error' not in result

    # ==================== 開倉 ====================

    def create_order(self, symbol: str, side: str, quantity: float) -> dict:
        """下市價單（自動先設置槓桿）"""
        self.set_leverage(symbol)
        formatted = self.precision_handler.format_quantity(symbol, quantity)
        params = {
            'symbol': symbol.replace('/', ''),
            'side': side.upper(),
            'type': 'MARKET',
            'quantity': formatted,
        }
        if self.hedge_mode:
            params['positionSide'] = 'LONG' if side.upper() == 'BUY' else 'SHORT'
        result = self.futures_client.signed_request_json('POST', '/fapi/v1/order', params)
        if 'error' in result:
            raise Exception(f"Order failed: {result['error']}")
        return result

    # ==================== 平倉 ====================

    def close_position(self, symbol: str, side: str, quantity: float) -> dict:
        """
        平倉（reduceOnly 市價單）。

        失敗時 log 詳情並重新拋出例外，讓 _handle_close 的 rollback 機制接手。
        """
        close_side = 'SELL' if side == 'LONG' else 'BUY'
        formatted = self.precision_handler.format_quantity(symbol, quantity)
        params = {
            'symbol': symbol.replace('/', ''),
            'side': close_side,
            'type': 'MARKET',
            'quantity': formatted,
            'reduceOnly': 'true',
        }
        if self.hedge_mode:
            params.pop('reduceOnly', None)
            params['positionSide'] = 'LONG' if side == 'LONG' else 'SHORT'
        try:
            result = self.futures_client.signed_request_json('POST', '/fapi/v1/order', params)
            if 'error' in result:
                raise Exception(f"Exchange error: {result['error']}")
            logger.debug(
                f"[OrderEngine] close_position OK: {symbol} {side} qty={quantity}"
            )
            return result
        except Exception as e:
            logger.error(
                f"[OrderEngine] close_position FAILED: {symbol} {side} qty={quantity} — {e}"
            )
            raise  # 向上傳遞，由 _handle_close 的 rollback 機制決定後續處理

    # ==================== 硬止損單 ====================

    def place_hard_stop_loss(
        self, symbol: str, side: str, size: float, stop_price: float
    ) -> Optional[str]:
        """設置硬止損單，回傳 order ID（失敗或已關閉則回傳 None）"""
        if not Config.USE_HARD_STOP_LOSS:
            return None
        try:
            symbol_id = symbol.replace('/', '')
            stop_side = 'SELL' if side == 'LONG' else 'BUY'
            formatted = self.precision_handler.format_quantity(symbol, size)

            if BinanceFuturesClient.is_enabled():
                params = {
                    'symbol': symbol_id,
                    'side': stop_side,
                    'type': 'STOP_MARKET',
                    'algoType': 'CONDITIONAL',
                    'quantity': formatted,
                    'triggerPrice': f"{stop_price:.2f}",
                    'reduceOnly': 'true',
                }
                if self.hedge_mode:
                    params.pop('reduceOnly', None)
                    params['positionSide'] = 'LONG' if side == 'LONG' else 'SHORT'
                response = self.futures_client.signed_request('POST', '/fapi/v1/algoOrder', params)
                if response.status_code == 200:
                    algo_id = response.json().get('algoId')
                    logger.info(f"{symbol} 硬止損已設定 @ ${stop_price:.2f} (ID: {algo_id})")
                    return str(algo_id)
                else:
                    logger.error(f"硬止損設定失敗: {response.status_code} - {response.text}")
            else:
                stop_side_lower = 'sell' if side == 'LONG' else 'buy'
                params = {'stopPrice': stop_price, 'reduceOnly': True}
                if self.hedge_mode:
                    params.pop('reduceOnly', None)
                    params['positionSide'] = 'LONG' if side == 'LONG' else 'SHORT'
                order = self.exchange.create_order(
                    symbol=symbol, type='STOP_MARKET', side=stop_side_lower,
                    amount=size, params=params
                )
                return order.get('id')
        except Exception as e:
            logger.error(f"{symbol} 硬止損設定失敗: {e}")
        return None

    def cancel_stop_loss_order(self, symbol: str, order_id: Optional[str]) -> bool:
        """取消止損單"""
        if not order_id:
            return True
        try:
            if BinanceFuturesClient.is_enabled():
                params = {'symbol': symbol.replace('/', ''), 'algoId': order_id}
                self.futures_client.signed_request('DELETE', '/fapi/v1/algoOrder', params)
            else:
                self.exchange.cancel_order(order_id, symbol)
            return True
        except Exception as e:
            logger.debug(f"取消止損單失敗（可能已觸發）: {e}")
            return False

    def update_hard_stop_loss(self, pm, new_stop: float):
        """更新硬止損單（取消舊的，設置新的，直接更新 pm.stop_order_id）"""
        if not Config.USE_HARD_STOP_LOSS:
            return
        self.cancel_stop_loss_order(pm.symbol, pm.stop_order_id)
        pm.stop_order_id = self.place_hard_stop_loss(
            pm.symbol, pm.side, pm.total_size, new_stop
        )
