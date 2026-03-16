"""
Binance Futures API 客戶端

統一的 HMAC SHA256 簽章 + HTTP 請求封裝，消除重複的簽章與請求邏輯。
從 v6/core.py 提取。
"""

import time
import logging
import requests

from trader.config import Config

logger = logging.getLogger(__name__)


class BinanceFuturesClient:
    """統一的 Binance Futures API 客戶端，消除重複的簽章與請求邏輯"""

    def __init__(self, api_key: str, api_secret: str, sandbox: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (
            "https://demo-fapi.binance.com" if sandbox
            else "https://fapi.binance.com"
        )
        self._current_weight = 0
        self._weight_limit = 2000  # Binance 上限 2400，保留安全邊際

    @staticmethod
    def is_enabled() -> bool:
        """判斷是否應使用 Binance Futures 直接 API（取代 ccxt）"""
        return (Config.SANDBOX_MODE
                and Config.TRADING_MODE == 'future'
                and Config.EXCHANGE == 'binance')

    def signed_request(self, method: str, endpoint: str, params: dict = None) -> requests.Response:
        """
        HMAC SHA256 簽章 + HTTP 請求，回傳原始 Response。
        """
        import hmac as hmac_mod
        import hashlib
        from urllib.parse import urlencode

        if params is None:
            params = {}

        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 10000  # 10s 容差（默認 5s 太緊，易觸發 -1021）
        query_string = urlencode(params)
        signature = hmac_mod.new(
            self.api_secret.strip().encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature

        headers = {'X-MBX-APIKEY': self.api_key}
        url = f"{self.base_url}{endpoint}"

        if self._current_weight > self._weight_limit:
            logger.warning(f"API weight {self._current_weight} exceeds limit {self._weight_limit}, sleeping 1s")
            time.sleep(1.0)

        if method.upper() == 'POST':
            response = requests.post(url, data=params, headers=headers, timeout=30)
        elif method.upper() == 'DELETE':
            response = requests.delete(url, params=params, headers=headers, timeout=30)
        else:
            response = requests.get(url, params=params, headers=headers, timeout=30)

        weight_header = response.headers.get('X-MBX-USED-WEIGHT-1M')
        if weight_header:
            try:
                self._current_weight = int(weight_header)
                logger.debug(f"API weight: {self._current_weight}/2400")
            except ValueError:
                pass

        # 偵測 -1021 timestamp 錯誤，方便排查時鐘同步問題
        if response.status_code == 400:
            try:
                error_body = response.json()
                if error_body.get('code') == -1021:
                    logger.warning(f"[TIMESTAMP] 時鐘偏差過大，建議檢查 NTP: {endpoint}")
            except Exception:
                pass

        return response

    def signed_request_json(self, method: str, endpoint: str, params: dict = None) -> dict:
        """簽章 + 請求 + JSON 解析 + 統一錯誤處理。"""
        try:
            response = self.signed_request(method, endpoint, params)
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API 錯誤: {response.status_code} - {response.text}")
                return {"error": response.text, "code": response.status_code}
        except Exception as e:
            logger.error(f"API 請求失敗: {e}")
            return {"error": str(e)}
