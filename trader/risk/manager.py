"""
風險管理層

包含：
- PrecisionHandler：交易所精度處理（數量格式化、最小訂單驗證）
- RiskManager：帳戶餘額獲取、倉位大小計算、止損計算、總風險檢查
- SignalTierSystem：信號分級系統（A/B/C 等級與倉位乘數）

從 v6/core.py 提取，業務邏輯不變。
"""

import ccxt
import math
import time
import logging
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple

from trader.config import Config
from trader.infrastructure.api_client import BinanceFuturesClient
from trader.indicators.technical import DynamicThresholdManager

logger = logging.getLogger(__name__)


# ==================== 精度處理 ====================

class PrecisionHandler:
    """交易所精度處理類"""

    FUTURES_MIN_NOTIONAL = 5

    # 保底用，僅在 exchangeInfo + ccxt 都失敗時使用
    DEFAULT_PRECISIONS = {
        'BTC/USDT': {'amount': 3, 'price': 2, 'min_amount': 0.001, 'min_cost': 5},
        'ETH/USDT': {'amount': 3, 'price': 2, 'min_amount': 0.001, 'min_cost': 5},
        'SOL/USDT': {'amount': 0, 'price': 2, 'min_amount': 1, 'min_cost': 5},
        'DOGE/USDT': {'amount': 0, 'price': 5, 'min_amount': 1, 'min_cost': 5},
        'ADA/USDT': {'amount': 0, 'price': 4, 'min_amount': 1, 'min_cost': 5},
        'LINK/USDT': {'amount': 2, 'price': 3, 'min_amount': 0.01, 'min_cost': 5},
    }

    def __init__(self, exchange):
        self.exchange = exchange
        self.markets = {}
        self.use_default_precision = False
        self._exchange_info_cache = {}  # {symbol: {'quantity': int, 'price': int}}
        self.load_markets()
        self._load_exchange_info()

    def load_markets(self):
        try:
            self.markets = self.exchange.load_markets(reload=True)
            logger.info("✅ 市場精度資訊已載入")
            self.use_default_precision = False
        except Exception as e:
            logger.error(f"載入市場失敗: {e}")
            logger.warning("⚠️ 使用默認精度設置")
            self.use_default_precision = True
            self.markets = {}

    def _load_exchange_info(self):
        """啟動時從 Binance exchangeInfo 一次載入所有幣種精度"""
        import requests
        if Config.SANDBOX_MODE and Config.TRADING_MODE == 'future':
            url = "https://demo-fapi.binance.com/fapi/v1/exchangeInfo"
        else:
            url = "https://fapi.binance.com/fapi/v1/exchangeInfo"

        for attempt in range(3):
            try:
                resp = requests.get(url, timeout=15)
                if resp.status_code != 200:
                    logger.warning(f"exchangeInfo HTTP {resp.status_code} (attempt {attempt + 1}/3)")
                    time.sleep(2)
                    continue

                data = resp.json()
                count = 0
                for s in data.get('symbols', []):
                    sid = s.get('symbol', '')
                    base = s.get('baseAsset', '')
                    quote = s.get('quoteAsset', '')
                    if base and quote:
                        ccxt_sym = f"{base}/{quote}"
                    elif sid.endswith('USDT'):
                        ccxt_sym = sid[:-4] + '/USDT'
                    else:
                        continue

                    self._exchange_info_cache[ccxt_sym] = {
                        'quantity': int(s.get('quantityPrecision', 3)),
                        'price': int(s.get('pricePrecision', 2)),
                    }
                    count += 1

                logger.info(f"✅ exchangeInfo 載入 {count} 個交易對精度")
                return
            except Exception as e:
                logger.warning(f"exchangeInfo 載入失敗 (attempt {attempt + 1}/3): {e}")
                time.sleep(2)

        logger.error("❌ exchangeInfo 3 次都失敗，將依賴 ccxt/DEFAULT_PRECISIONS")

    @staticmethod
    def _step_to_decimals(step) -> int:
        """將步長轉換為小數位數"""
        if step is None or step <= 0:
            return 3
        if step >= 1:
            return 0
        return max(0, int(round(-math.log10(float(step)))))

    def get_precision(self, symbol: str) -> int:
        """獲取交易對的數量精度（優先 exchangeInfo → ccxt → DEFAULT → 預設 3）"""
        # 第一優先：exchangeInfo cache（啟動時全量載入）
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]['quantity']

        # 第二：ccxt markets
        if symbol in self.markets:
            precision = self.markets[symbol]['precision']['amount']
            if isinstance(precision, int):
                return precision
            if isinstance(precision, float) and precision > 0:
                return self._step_to_decimals(precision)

        # 第三：硬編碼保底
        if symbol in self.DEFAULT_PRECISIONS:
            logger.debug(f"{symbol} 使用 DEFAULT_PRECISIONS")
            return self.DEFAULT_PRECISIONS[symbol]['amount']

        # 全部失敗
        logger.warning(f"⚠️ {symbol} 無法取得精度，使用預設值 3")
        return 3

    def get_price_precision(self, symbol: str) -> int:
        """獲取交易對的價格精度"""
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]['price']

        if symbol in self.markets:
            precision = self.markets[symbol]['precision']['price']
            if isinstance(precision, int):
                return precision
            if isinstance(precision, float) and precision > 0:
                return self._step_to_decimals(precision)

        if symbol in self.DEFAULT_PRECISIONS:
            return self.DEFAULT_PRECISIONS[symbol]['price']

        logger.warning(f"⚠️ {symbol} 無法取得價格精度，使用預設值 2")
        return 2

    def format_quantity(self, symbol: str, quantity: float) -> str:
        """將數量格式化為交易所要求的字串精度"""
        precision = self.get_precision(symbol)
        if precision == 0:
            formatted = str(int(quantity))
        else:
            formatted = f"{quantity:.{precision}f}"
        logger.debug(f"{symbol} format_quantity: {quantity} → {formatted} (precision={precision})")
        return formatted

    def round_amount_up(self, symbol: str, amount: float, price: float) -> float:
        """向上取整數量，確保訂單價值滿足最小要求"""
        precision = self.get_precision(symbol)
        multiplier = 10 ** precision

        rounded = math.ceil(amount * multiplier) / multiplier

        order_value = rounded * price
        min_notional = self.FUTURES_MIN_NOTIONAL if Config.TRADING_MODE == 'future' else 10

        if order_value < min_notional:
            min_quantity = min_notional / price
            rounded = math.ceil(min_quantity * multiplier) / multiplier
            logger.debug(f"⚠️ 調整數量以滿足最小訂單價值 ${min_notional}")

        return rounded

    def round_amount(self, symbol: str, amount: float) -> float:
        """向下取整數量（用於平倉等操作）"""
        precision = self.get_precision(symbol)
        amount_decimal = Decimal(str(amount))
        multiplier = Decimal(10) ** precision
        rounded = (amount_decimal * multiplier).quantize(Decimal('1'), rounding=ROUND_DOWN) / multiplier
        return float(rounded)

    def get_min_amount(self, symbol: str) -> float:
        """獲取交易對的最小交易數量"""
        if symbol in self.DEFAULT_PRECISIONS:
            return self.DEFAULT_PRECISIONS[symbol].get('min_amount', 0.001)
        return 0.001

    def check_limits(self, symbol: str, amount: float, price: float) -> bool:
        """檢查訂單是否滿足限制"""
        min_notional = self.FUTURES_MIN_NOTIONAL if Config.TRADING_MODE == 'future' else 10

        if symbol not in self.markets and self.use_default_precision:
            if symbol in self.DEFAULT_PRECISIONS:
                defaults = self.DEFAULT_PRECISIONS[symbol]
                if amount < defaults['min_amount']:
                    logger.warning(f"{symbol} 數量 {amount} 小於最小值 {defaults['min_amount']}")
                    return False
                cost = amount * price
                if cost < min_notional:
                    logger.warning(f"{symbol} 金額 ${cost:.2f} 小於最小值 ${min_notional}")
                    return False
            return True

        if symbol not in self.markets:
            cost = amount * price
            if cost < min_notional:
                logger.warning(f"{symbol} 金額 ${cost:.2f} 小於最小值 ${min_notional}")
                return False
            return True

        market = self.markets[symbol]
        limits = market['limits']

        if limits['amount']['min'] and amount < limits['amount']['min']:
            logger.warning(f"{symbol} 數量小於最小值")
            return False

        cost = amount * price
        actual_min_cost = max(limits['cost']['min'] or 0, min_notional)
        if cost < actual_min_cost:
            logger.warning(f"{symbol} 金額 ${cost:.2f} 小於最小值 ${actual_min_cost}")
            return False

        return True


# ==================== 風險管理 ====================

class RiskManager:
    """風險管理類"""

    def __init__(self, exchange, precision_handler: PrecisionHandler):
        self.exchange = exchange
        self.precision_handler = precision_handler
        self.futures_client = BinanceFuturesClient(Config.API_KEY, Config.API_SECRET, Config.SANDBOX_MODE)

    def _get_futures_balance(self) -> float:
        """使用 /fapi/v2/balance 端點獲取 Futures 餘額"""
        try:
            response = self.futures_client.signed_request('GET', '/fapi/v2/balance')

            if response.status_code == 200:
                data = response.json()
                for asset in data:
                    if asset.get('asset') == 'USDT':
                        return float(asset.get('availableBalance', 0))
                return 0
            else:
                logger.error(f"Futures API 錯誤: {response.status_code} - {response.text}")
                return 0

        except Exception as e:
            logger.error(f"獲取 Futures 餘額失敗: {e}")
            return 0

    def get_balance(self) -> float:
        """獲取帳戶餘額"""
        for attempt in range(Config.MAX_RETRY):
            try:
                if Config.SANDBOX_MODE and Config.TRADING_MODE == 'future' and Config.EXCHANGE == 'binance':
                    balance = self._get_futures_balance()
                    if balance > 0:
                        return balance
                    if attempt < Config.MAX_RETRY - 1:
                        time.sleep(Config.RETRY_DELAY)
                        continue
                    return 0
                else:
                    balance = self.exchange.fetch_balance()
                    return balance['USDT']['free']

            except ccxt.NetworkError as e:
                logger.warning(f"網絡錯誤，重試 {attempt+1}/{Config.MAX_RETRY}")
                time.sleep(Config.RETRY_DELAY)
            except Exception as e:
                logger.error(f"獲取餘額失敗: {e}")
                if attempt < Config.MAX_RETRY - 1:
                    time.sleep(Config.RETRY_DELAY)
                else:
                    return 0
        return 0

    def get_positions(self) -> Optional[list]:
        """
        獲取現有持倉。

        Returns:
            list  — 成功，可能為 []（真的沒倉位）
            None  — API 錯誤，呼叫方應跳過同步
        """
        try:
            if Config.SANDBOX_MODE and Config.TRADING_MODE == 'future' and Config.EXCHANGE == 'binance':
                return self._get_futures_positions()
            else:
                positions = self.exchange.fetch_positions()
                return [p for p in positions if float(p.get('contracts', 0)) != 0]
        except Exception as e:
            logger.error(f"獲取持倉失敗: {e}")
            return None

    def _get_futures_positions(self) -> Optional[list]:
        """使用 Binance Futures API 獲取持倉。回傳 None 表示 API 錯誤。"""
        try:
            response = self.futures_client.signed_request('GET', '/fapi/v2/positionRisk')

            if response.status_code == 200:
                data = response.json()
                return [p for p in data if float(p.get('positionAmt', 0)) != 0]
            else:
                logger.error(f"獲取持倉 API 錯誤: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"獲取 Futures 持倉失敗: {e}")
            return None

    def get_account_info(self) -> dict:
        """獲取完整帳戶資訊"""
        return {
            'balance': self.get_balance(),
            'positions': self.get_positions() or []
        }

    def calculate_position_size(self, symbol: str, balance: float,
                               entry_price: float, stop_loss: float,
                               tier_multiplier: float = 1.0) -> float:
        """計算倉位大小"""
        risk_amount = balance * Config.RISK_PER_TRADE
        stop_dist_percent = abs(entry_price - stop_loss) / entry_price

        if stop_dist_percent == 0:
            return 0

        position_value = risk_amount / stop_dist_percent

        max_position_value = balance * Config.MAX_POSITION_PERCENT * Config.LEVERAGE
        if position_value > max_position_value:
            logger.warning(f"⚠️ {symbol} 倉位超過上限，從 ${position_value:.2f} 調整為 ${max_position_value:.2f}")
            position_value = max_position_value

        raw_position = position_value / entry_price
        raw_position *= tier_multiplier

        rounded_position = self.precision_handler.round_amount_up(symbol, raw_position, entry_price)

        if not self.precision_handler.check_limits(symbol, rounded_position, entry_price):
            return 0

        order_value = rounded_position * entry_price
        logger.info(f"💰 {symbol} 倉位: {rounded_position:.6f} (訂單價值: ${order_value:.2f}, 等級乘數: {tier_multiplier})")
        return rounded_position

    def calculate_stop_loss(self, extreme_point: float, atr: float,
                            side: str, df=None) -> float:
        """計算止損價位"""
        atr_mult = DynamicThresholdManager.get_atr_multiplier(df) if df is not None else Config.ATR_MULTIPLIER

        if side == 'LONG':
            return extreme_point - (atr * atr_mult)
        else:
            return extreme_point + (atr * atr_mult)

    def check_total_risk(self, active_positions: List) -> bool:
        """計算所有持倉的實際剩餘風險"""
        if not active_positions:
            return True

        total_risk = 0.0

        for trade in active_positions:
            if trade.is_closed:
                continue

            if trade.side == 'LONG':
                risk_per_unit = trade.entry_price - trade.current_sl
            else:
                risk_per_unit = trade.current_sl - trade.entry_price

            if risk_per_unit <= 0:
                continue

            actual_risk_amount = trade.current_size * risk_per_unit
            total_risk += actual_risk_amount

        balance = self.get_balance()
        if balance <= 0:
            return False

        total_risk_pct = total_risk / balance
        return total_risk_pct <= Config.MAX_TOTAL_RISK


# ==================== 信號分級系統 ====================

class SignalTierSystem:
    """信號分級系統"""

    @staticmethod
    def calculate_signal_tier(
        signal_details: Dict,
        mtf_aligned: bool,
        market_strong: bool,
        volume_grade: str
    ) -> Tuple[str, float, int]:
        """
        計算信號等級並返回對應的倉位乘數
        A 級：所有條件滿足
        B 級：大部分條件滿足
        C 級：基本條件滿足
        Returns: (tier, multiplier, score)
        """
        if not Config.ENABLE_TIERED_ENTRY:
            return 'B', Config.TIER_B_POSITION_MULT, -1  # -1 表示未啟用

        score = 0

        if mtf_aligned:
            score += 2
        if market_strong:
            score += 2

        if volume_grade in ['explosive', 'strong']:
            score += 2
        elif volume_grade == 'moderate':
            score += 1

        if signal_details.get('candle_confirmed', False):
            score += 1

        if score >= 6:
            return 'A', Config.TIER_A_POSITION_MULT, score
        elif score >= 4:
            return 'B', Config.TIER_B_POSITION_MULT, score
        else:
            return 'C', Config.TIER_C_POSITION_MULT, score


# ==================== 資金池管理 ====================

class PoolManager:
    """管理趨勢池/網格池的資金分配"""

    # 網格池最低分配金額（低於此值不啟動）
    GRID_MIN_ALLOCATION = 500.0  # USDT

    def __init__(self):
        self.grid_allocated: float = 0.0
        self.grid_realized_pnl: float = 0.0
        self.cumulative_grid_pnl: float = 0.0
        self._round_count: int = 0

    @property
    def is_active(self) -> bool:
        return self.grid_allocated > 0

    def activate_grid_pool(self, total_balance: float) -> bool:
        """Regime 進入 RANGING 時呼叫，鎖定固定金額。
        Returns False if allocation too small."""
        allocation = total_balance * Config.GRID_CAPITAL_RATIO
        if allocation < self.GRID_MIN_ALLOCATION:
            logger.warning(f"Grid pool allocation {allocation:.0f} < min {self.GRID_MIN_ALLOCATION} — skipping")
            return False
        self.grid_allocated = allocation
        self.grid_realized_pnl = 0.0
        self._round_count += 1
        return True

    def deactivate_grid_pool(self):
        """網格收斂完成後呼叫"""
        self.cumulative_grid_pnl += self.grid_realized_pnl
        self.grid_allocated = 0.0
        self.grid_realized_pnl = 0.0

    def get_grid_balance(self) -> float:
        """網格池可用 = 初始分配 + 已實現損益"""
        return max(0.0, self.grid_allocated + self.grid_realized_pnl)

    def get_trend_balance(self, total_balance: float) -> float:
        """趨勢池 = 總餘額 - 網格池分配"""
        return total_balance - self.grid_allocated
