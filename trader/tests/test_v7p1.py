"""Test: V7 P1 — Rate Limit 防禦 + pending_stop_cancels 序列化"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from unittest.mock import MagicMock, patch
from trader.positions import PositionManager


# ──────────────────────────────────────────────
# 輔助：建立最小 PositionManager
# ──────────────────────────────────────────────

def _make_pm(**kwargs) -> PositionManager:
    defaults = dict(
        symbol='BTC/USDT',
        side='LONG',
        entry_price=50000.0,
        stop_loss=48000.0,
        position_size=0.01,
    )
    defaults.update(kwargs)
    return PositionManager(**defaults)


# ──────────────────────────────────────────────
# 1. pending_stop_cancels round-trip
# ──────────────────────────────────────────────

class TestPendingStopCancels:

    def test_pending_stop_cancels_serialization(self):
        """to_dict → from_dict 應正確還原 pending_stop_cancels"""
        pm = _make_pm()
        pm.pending_stop_cancels = ['order123', 'order456']

        d = pm.to_dict()
        assert d['pending_stop_cancels'] == ['order123', 'order456']

        pm2 = PositionManager.from_dict(d)
        assert pm2.pending_stop_cancels == ['order123', 'order456']

    def test_pending_stop_cancels_backward_compat(self):
        """舊格式 dict（不含 pending_stop_cancels）應 default 為空 list，不 crash"""
        pm = _make_pm()
        d = pm.to_dict()
        # 模擬舊版 dict 不含此欄位
        d.pop('pending_stop_cancels', None)

        pm2 = PositionManager.from_dict(d)
        assert pm2.pending_stop_cancels == []

    def test_new_pm_has_empty_pending_stop_cancels(self):
        """新建的 PositionManager 應有空的 pending_stop_cancels"""
        pm = _make_pm()
        assert pm.pending_stop_cancels == []


# ──────────────────────────────────────────────
# 2. API weight tracking
# ──────────────────────────────────────────────

class TestApiWeightTracking:

    def test_api_weight_tracking(self):
        """signed_request 應解析 X-MBX-USED-WEIGHT-1M header 並更新 _current_weight"""
        from trader.infrastructure.api_client import BinanceFuturesClient

        client = BinanceFuturesClient(api_key='test_key', api_secret='test_secret', sandbox=True)
        assert client._current_weight == 0

        # 建立 mock response
        mock_response = MagicMock()
        mock_response.headers = {'X-MBX-USED-WEIGHT-1M': '1900'}

        with patch('requests.get', return_value=mock_response):
            resp = client.signed_request('GET', '/fapi/v2/account')

        assert client._current_weight == 1900
        assert resp is mock_response

    def test_api_weight_missing_header(self):
        """Header 不存在時 _current_weight 維持 0，不 crash"""
        from trader.infrastructure.api_client import BinanceFuturesClient

        client = BinanceFuturesClient(api_key='test_key', api_secret='test_secret', sandbox=True)

        mock_response = MagicMock()
        mock_response.headers = {}

        with patch('requests.get', return_value=mock_response):
            client.signed_request('GET', '/fapi/v2/account')

        assert client._current_weight == 0

    def test_api_weight_invalid_header(self):
        """Header 值非整數時應靜默忽略，不 crash"""
        from trader.infrastructure.api_client import BinanceFuturesClient

        client = BinanceFuturesClient(api_key='test_key', api_secret='test_secret', sandbox=True)

        mock_response = MagicMock()
        mock_response.headers = {'X-MBX-USED-WEIGHT-1M': 'invalid'}

        with patch('requests.get', return_value=mock_response):
            client.signed_request('GET', '/fapi/v2/account')

        assert client._current_weight == 0
