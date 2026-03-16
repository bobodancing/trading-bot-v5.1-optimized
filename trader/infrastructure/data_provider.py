"""
資料提供者 — 統一交易所 OHLCV 獲取層

封裝 ccxt exchange 實例、fetch_ohlcv 重試邏輯，以及 Binance Demo Trading
的直連 fallback，消除 bot.py 與 scanner/market_scanner.py 的重複實現。

使用方式（依賴注入）：
    provider = MarketDataProvider(
        exchange,
        max_retry=3,
        retry_delay=5.0,
        sandbox_mode=Config.SANDBOX_MODE,
        trading_mode=Config.TRADING_MODE,
    )
    df = provider.fetch_ohlcv('BTC/USDT', '1h', limit=100)
"""

import time
import logging
import pandas as pd

try:
    import ccxt
except ImportError:
    ccxt = None  # type: ignore

logger = logging.getLogger(__name__)


class MarketDataProvider:
    """統一市場數據提供者：封裝 ccxt exchange 與 OHLCV 獲取邏輯"""

    def __init__(
        self,
        exchange,
        max_retry: int = 3,
        retry_delay: float = 5.0,
        sandbox_mode: bool = False,
        trading_mode: str = 'spot',
    ):
        """
        Args:
            exchange: 已初始化的 ccxt exchange 實例（依賴注入）
            max_retry: 最大重試次數
            retry_delay: 重試基礎間隔（秒），NetworkError 時會隨 attempt 線性增長
            sandbox_mode: 是否為沙盒/Demo 模式（啟用 demo-fapi 直連 fallback）
            trading_mode: 交易模式 'spot' 或 'future'
        """
        self.exchange = exchange
        self.max_retry = max_retry
        self.retry_delay = retry_delay
        self.sandbox_mode = sandbox_mode
        self.trading_mode = trading_mode

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """
        獲取 OHLCV K 線數據（含重試與沙盒 fallback）

        沙盒模式下，若 ccxt 失敗會自動切換為直連 demo-fapi.binance.com。

        Returns:
            pd.DataFrame with columns: timestamp, open, high, low, close, volume
            失敗時回傳空 DataFrame
        """
        for attempt in range(self.max_retry):
            try:
                ohlcv = None

                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                except Exception:
                    # Sandbox / Demo Trading fallback：直接呼叫 demo-fapi REST API
                    if self.trading_mode == 'future' and self.sandbox_mode:
                        import requests as req
                        symbol_id = symbol.replace('/', '')
                        base_url = 'https://demo-fapi.binance.com'
                        resp = req.get(
                            f'{base_url}/fapi/v1/klines',
                            params={'symbol': symbol_id, 'interval': timeframe, 'limit': limit},
                            timeout=30,
                        )
                        if resp.status_code == 200:
                            ohlcv = [
                                [int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4]), float(c[5])]
                                for c in resp.json()
                            ]

                if ohlcv is None or len(ohlcv) == 0:
                    return pd.DataFrame()

                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                return df

            except Exception as e:
                # ccxt.NetworkError 或其他異常：重試
                is_network = ccxt is not None and isinstance(e, ccxt.NetworkError)
                if attempt < self.max_retry - 1:
                    delay = self.retry_delay * (attempt + 1) if is_network else self.retry_delay
                    time.sleep(delay)
                else:
                    break

        return pd.DataFrame()
