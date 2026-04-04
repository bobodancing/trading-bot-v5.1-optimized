"""
Binance Futures API client helpers.
"""

import logging
import time

import requests

from trader.config import Config

logger = logging.getLogger(__name__)


class BinanceFuturesClient:
    """Thin wrapper around Binance Futures signed REST endpoints."""

    def __init__(self, api_key: str, api_secret: str, sandbox: bool = True):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = (
            "https://demo-fapi.binance.com" if sandbox
            else "https://fapi.binance.com"
        )
        self._current_weight = 0
        self._weight_limit = 2000

    @staticmethod
    def is_enabled() -> bool:
        """Whether the direct Binance Futures client should be used."""
        return (
            Config.SANDBOX_MODE
            and Config.TRADING_MODE == 'future'
            and Config.EXCHANGE == 'binance'
        )

    def signed_request(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
    ) -> requests.Response:
        """Send a signed Binance request and return the raw response."""
        import hashlib
        import hmac as hmac_mod
        from urllib.parse import urlencode

        if params is None:
            params = {}

        params['timestamp'] = int(time.time() * 1000)
        params['recvWindow'] = 10000
        query_string = urlencode(params)
        signature = hmac_mod.new(
            self.api_secret.strip().encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        params['signature'] = signature

        headers = {'X-MBX-APIKEY': self.api_key}
        url = f"{self.base_url}{endpoint}"

        if self._current_weight > self._weight_limit:
            logger.warning(
                f"API weight {self._current_weight} exceeds limit "
                f"{self._weight_limit}, sleeping 1s"
            )
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

        if response.status_code == 400:
            try:
                error_body = response.json()
                if error_body.get('code') == -1021:
                    logger.warning(
                        f"[TIMESTAMP] Check local time/NTP drift for endpoint: {endpoint}"
                    )
            except Exception:
                pass

        return response

    def signed_request_json(
        self,
        method: str,
        endpoint: str,
        params: dict = None,
    ) -> dict:
        """Send a signed request and return JSON or an error dict."""
        try:
            response = self.signed_request(method, endpoint, params)
            if response.status_code == 200:
                return response.json()
            logger.error(f"API error: {response.status_code} - {response.text}")
            return {"error": response.text, "code": response.status_code}
        except Exception as e:
            logger.error(f"API request failed: {e}")
            return {"error": str(e)}

    def get_position_side_dual(self) -> bool:
        """Return account-wide dualSidePosition (hedge mode) state."""
        resp = self.signed_request_json('GET', '/fapi/v1/positionSide/dual')
        if resp is not None and 'error' not in resp and 'dualSidePosition' in resp:
            return bool(resp['dualSidePosition'])
        raise RuntimeError(f"Failed to get positionSide/dual: {resp}")

    def get_position_mode(self):
        """Backward-compatible helper for hedge mode checks."""
        try:
            return self.get_position_side_dual()
        except Exception as e:
            logger.warning(f"Failed to get position mode: {e}")
        return None

    def set_hedge_mode(self, dual: bool = True) -> bool:
        """Enable or disable Binance hedge mode."""
        try:
            resp = self.signed_request_json(
                'POST',
                '/fapi/v1/positionSide/dual',
                params={'dualSidePosition': str(dual).lower()},
            )
            if resp is not None and 'error' not in resp:
                logger.info(f"Hedge mode set to {dual}")
                return True
            logger.warning(f"Hedge mode switch rejected: {resp}")
        except Exception as e:
            logger.error(f"Failed to set hedge mode: {e}")
        return False
