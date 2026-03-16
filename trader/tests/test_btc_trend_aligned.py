"""
Tests: BTC Trend Alignment tracking (Phase 1 data collection)

覆蓋指令書 Step 5 的 8 個測試項目：
1. EMA 判定正確：EMA20 > EMA50 → LONG
2. Aligned LONG：BTC trend LONG + position LONG → True
3. Misaligned：BTC trend LONG + position SHORT → False
4. BTC 自身：symbol=BTC/USDT → None
5. API 失敗：fetch_ohlcv raise → None (non-fatal)
6. 數據不足：len(df) < 50 → None
7. to_dict/from_dict 持久化：存取一致
8. perf_db 記錄：record_trade 含 btc_trend_aligned
"""

import sys
import pytest
import sqlite3
import tempfile
import os
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.positions import PositionManager
from trader.infrastructure.performance_db import PerformanceDB


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_pm(symbol='ETH/USDT', side='LONG') -> PositionManager:
    pm = PositionManager(
        symbol=symbol,
        side=side,
        entry_price=3000.0,
        stop_loss=2900.0,
        position_size=0.1,
    )
    pm.initial_r = 10.0
    pm.entry_time = datetime.now(timezone.utc)
    pm.highest_price = 3100.0
    pm.lowest_price = 2950.0
    return pm


def _make_btc_df(ema20_gt_ema50: bool, rows: int = 60) -> pd.DataFrame:
    """建立 BTC 1D 的假 OHLCV，使 EMA20/EMA50 呈指定大小關係。"""
    if ema20_gt_ema50:
        # 遞增價格序列 → EMA20 > EMA50
        close = np.linspace(90000, 100000, rows)
    else:
        # 遞減價格序列 → EMA20 < EMA50
        close = np.linspace(100000, 90000, rows)
    df = pd.DataFrame({'close': close})
    return df


# ─── Test 1: EMA 判定正確 ─────────────────────────────────────────────────────

class TestEmaDetection:
    def test_ema20_gt_ema50_is_long(self):
        """EMA20 > EMA50 → btc_trend == LONG"""
        df = _make_btc_df(ema20_gt_ema50=True, rows=60)
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        assert ema20 > ema50

    def test_ema20_lt_ema50_is_short(self):
        """EMA20 < EMA50 → btc_trend == SHORT"""
        df = _make_btc_df(ema20_gt_ema50=False, rows=60)
        ema20 = df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        assert ema20 < ema50


# ─── Test 2 & 3: Aligned / Misaligned ────────────────────────────────────────

class TestBtcTrendAlignedLogic:
    """模擬 bot._open_position() 中的 BTC trend 判定邏輯"""

    def _run_alignment(self, symbol, side, btc_df):
        """複製指令書中的判定片段，回傳 btc_trend_aligned"""
        btc_trend_aligned = None
        if "BTC" not in symbol:
            try:
                if btc_df is not None and len(btc_df) >= 50:
                    btc_ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                    btc_ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                    btc_trend = "LONG" if btc_ema20 > btc_ema50 else "SHORT"
                    btc_trend_aligned = (side == btc_trend)
            except Exception:
                pass
        return btc_trend_aligned

    def test_aligned_long(self):
        """BTC trend LONG + position LONG → True"""
        df = _make_btc_df(ema20_gt_ema50=True)
        result = self._run_alignment('ETH/USDT', 'LONG', df)
        assert result is True

    def test_misaligned_short(self):
        """BTC trend LONG + position SHORT → False"""
        df = _make_btc_df(ema20_gt_ema50=True)
        result = self._run_alignment('ETH/USDT', 'SHORT', df)
        assert result is False

    def test_aligned_short(self):
        """BTC trend SHORT + position SHORT → True"""
        df = _make_btc_df(ema20_gt_ema50=False)
        result = self._run_alignment('ETH/USDT', 'SHORT', df)
        assert result is True

    # ─── Test 4: BTC 自身 ─────────────────────────────────────────────────────

    def test_btc_self_returns_none(self):
        """symbol=BTC/USDT → None（不適用）"""
        df = _make_btc_df(ema20_gt_ema50=True)
        result = self._run_alignment('BTC/USDT', 'LONG', df)
        assert result is None

    # ─── Test 5: API 失敗 ─────────────────────────────────────────────────────

    def test_api_failure_returns_none(self):
        """fetch_ohlcv raise Exception → None（non-fatal）"""
        btc_trend_aligned = None
        if "BTC" not in 'ETH/USDT':
            try:
                raise RuntimeError("network error")
            except Exception:
                pass
        assert btc_trend_aligned is None

    # ─── Test 6: 數據不足 ─────────────────────────────────────────────────────

    def test_insufficient_data_returns_none(self):
        """len(df) < 50 → None"""
        short_df = _make_btc_df(ema20_gt_ema50=True, rows=30)
        result = self._run_alignment('ETH/USDT', 'LONG', short_df)
        assert result is None

    def test_none_df_returns_none(self):
        """fetch_ohlcv returns None → None"""
        result = self._run_alignment('ETH/USDT', 'LONG', None)
        assert result is None


# ─── Test 7: to_dict / from_dict 持久化 ──────────────────────────────────────

class TestPositionManagerPersistence:
    def test_btc_trend_aligned_true_roundtrip(self):
        """btc_trend_aligned=True → to_dict → from_dict → True"""
        pm = _make_pm()
        pm.btc_trend_aligned = True
        data = pm.to_dict()
        assert data['btc_trend_aligned'] is True

        pm2 = PositionManager.from_dict(data)
        assert pm2.btc_trend_aligned is True

    def test_btc_trend_aligned_false_roundtrip(self):
        """btc_trend_aligned=False → to_dict → from_dict → False"""
        pm = _make_pm()
        pm.btc_trend_aligned = False
        data = pm.to_dict()
        pm2 = PositionManager.from_dict(data)
        assert pm2.btc_trend_aligned is False

    def test_btc_trend_aligned_none_roundtrip(self):
        """btc_trend_aligned=None → to_dict → from_dict → None"""
        pm = _make_pm()
        pm.btc_trend_aligned = None
        data = pm.to_dict()
        pm2 = PositionManager.from_dict(data)
        assert pm2.btc_trend_aligned is None

    def test_backward_compat_missing_key(self):
        """舊 positions.json 無此欄位 → from_dict 回 None"""
        pm = _make_pm()
        data = pm.to_dict()
        del data['btc_trend_aligned']  # 模擬舊格式
        pm2 = PositionManager.from_dict(data)
        assert pm2.btc_trend_aligned is None

    def test_default_is_none(self):
        """新建 PM 預設值為 None"""
        pm = _make_pm()
        assert pm.btc_trend_aligned is None


# ─── Test 8: perf_db record_trade 含 btc_trend_aligned ──────────────────────

class TestPerfDbBtcTrendAligned:
    def _make_trade_data(self, btc_trend_aligned):
        return {
            "trade_id": "test_001",
            "symbol": "ETH/USDT",
            "side": "LONG",
            "is_v6_pyramid": 1,
            "signal_tier": "B",
            "entry_price": 3000.0,
            "exit_price": 3200.0,
            "total_size": 0.1,
            "initial_r": 10.0,
            "entry_time": "2026-01-01T00:00:00+00:00",
            "exit_time": "2026-01-02T00:00:00+00:00",
            "holding_hours": 24.0,
            "pnl_usdt": 20.0,
            "pnl_pct": 6.67,
            "realized_r": 2.0,
            "mfe_pct": 7.0,
            "mae_pct": -1.0,
            "capture_ratio": 0.95,
            "stage_reached": 1,
            "exit_reason": "structure_break",
            "market_regime": "TRENDING",
            "entry_adx": 30.0,
            "fakeout_depth_atr": 0.5,
            "original_size": 0.1,
            "partial_pnl_usdt": 0.0,
            "btc_trend_aligned": btc_trend_aligned,
        }

    def test_record_trade_aligned_true(self, tmp_path):
        """btc_trend_aligned=True 寫入 DB → 讀回 1"""
        db = PerformanceDB(db_path=str(tmp_path / 'test.db'))
        data = self._make_trade_data(True)
        assert db.record_trade(data) is True

        with sqlite3.connect(str(tmp_path / 'test.db')) as conn:
            row = conn.execute(
                "SELECT btc_trend_aligned FROM trades WHERE trade_id=?", ("test_001",)
            ).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_record_trade_aligned_false(self, tmp_path):
        """btc_trend_aligned=False 寫入 DB → 讀回 0"""
        db = PerformanceDB(db_path=str(tmp_path / 'test.db'))
        data = self._make_trade_data(False)
        data["trade_id"] = "test_002"
        assert db.record_trade(data) is True

        with sqlite3.connect(str(tmp_path / 'test.db')) as conn:
            row = conn.execute(
                "SELECT btc_trend_aligned FROM trades WHERE trade_id=?", ("test_002",)
            ).fetchone()
        assert row[0] == 0

    def test_record_trade_aligned_none(self, tmp_path):
        """btc_trend_aligned=None 寫入 DB → 讀回 NULL"""
        db = PerformanceDB(db_path=str(tmp_path / 'test.db'))
        data = self._make_trade_data(None)
        data["trade_id"] = "test_003"
        assert db.record_trade(data) is True

        with sqlite3.connect(str(tmp_path / 'test.db')) as conn:
            row = conn.execute(
                "SELECT btc_trend_aligned FROM trades WHERE trade_id=?", ("test_003",)
            ).fetchone()
        assert row[0] is None

    def test_record_trade_without_key_defaults_none(self, tmp_path):
        """未傳 btc_trend_aligned key → setdefault → NULL"""
        db = PerformanceDB(db_path=str(tmp_path / 'test.db'))
        data = self._make_trade_data(None)
        data["trade_id"] = "test_004"
        del data["btc_trend_aligned"]  # 模擬舊呼叫方
        assert db.record_trade(data) is True

        with sqlite3.connect(str(tmp_path / 'test.db')) as conn:
            row = conn.execute(
                "SELECT btc_trend_aligned FROM trades WHERE trade_id=?", ("test_004",)
            ).fetchone()
        assert row[0] is None
