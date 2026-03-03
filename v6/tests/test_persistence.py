"""Test: 持久化層 + PositionManager 序列化"""

import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pandas as pd
import pytest
from datetime import datetime, timezone, timedelta
from v6.persistence import PositionPersistence
from v6.positions import PositionManager, EntryRecord
from v6.infrastructure.performance_db import PerformanceDB


TEST_PATH = 'C:/Users/user/test_v6_persist.json'


@pytest.fixture(autouse=True)
def cleanup():
    """每個 test 前後清理"""
    yield
    if os.path.exists(TEST_PATH):
        os.remove(TEST_PATH)
    # cleanup backup
    bak = TEST_PATH + '.bak'
    if os.path.exists(bak):
        os.remove(bak)


class TestPersistence:
    """PositionPersistence 基本功能"""

    def test_save_load_roundtrip(self):
        pp = PositionPersistence(TEST_PATH)
        data = {
            'BTC/USDT': {'symbol': 'BTC/USDT', 'side': 'LONG', 'stage': 1},
            'ETH/USDT': {'symbol': 'ETH/USDT', 'side': 'SHORT', 'stage': 2},
        }
        assert pp.save_positions(data) == True
        loaded = pp.load_positions()
        assert len(loaded) == 2
        assert loaded['BTC/USDT']['side'] == 'LONG'
        assert loaded['ETH/USDT']['stage'] == 2

    def test_load_empty(self):
        pp = PositionPersistence(TEST_PATH)
        loaded = pp.load_positions()
        assert loaded == {}

    def test_save_overwrite(self):
        pp = PositionPersistence(TEST_PATH)
        pp.save_positions({'A': {'x': 1}})
        pp.save_positions({'B': {'y': 2}})
        loaded = pp.load_positions()
        assert 'A' not in loaded
        assert loaded['B']['y'] == 2

    def test_clear(self):
        pp = PositionPersistence(TEST_PATH)
        pp.save_positions({'X': {'v': 1}})
        pp.clear_positions()
        loaded = pp.load_positions()
        assert loaded == {}

    def test_backup(self):
        pp = PositionPersistence(TEST_PATH)
        pp.save_positions({'Z': {'w': 3}})
        bak = pp.backup_positions()
        assert bak is not None
        assert os.path.exists(bak)
        # cleanup
        if os.path.exists(bak):
            os.remove(bak)


class TestPositionManagerSerialization:
    """PositionManager to_dict / from_dict"""

    def test_v6_roundtrip(self):
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            neckline=96500.0, equity_base=10000.0, initial_r=170.0,
        )
        pm.add_stage2(price=96600.0, size=0.035)
        pm.highest_price = 97000.0
        pm.stop_order_id = "algo_123"

        d = pm.to_dict()
        pm2 = PositionManager.from_dict(d)

        assert pm2.symbol == 'BTC/USDT'
        assert pm2.side == 'LONG'
        assert pm2.stage == 2
        assert pm2.total_size == pytest.approx(0.070, abs=0.001)
        assert pm2.neckline == 96500.0
        assert pm2.equity_base == 10000.0
        assert pm2.stop_order_id == "algo_123"
        assert pm2.highest_price == 97000.0
        assert pm2.is_v6_pyramid == True
        assert len(pm2.entries) == 2

    def test_v53_roundtrip(self):
        pm = PositionManager(
            symbol='ETH/USDT', side='SHORT',
            entry_price=3500.0, stop_loss=3600.0,
            position_size=1.0, is_v6_pyramid=False,
            initial_r=100.0, signal_tier='A',
        )
        pm.is_1r_protected = True
        pm.is_first_partial = True

        d = pm.to_dict()
        assert d['v53_state'] is not None
        assert d['v53_state']['is_1r_protected'] == True

        pm2 = PositionManager.from_dict(d)
        assert pm2.is_v6_pyramid == False
        assert pm2.is_1r_protected == True
        assert pm2.is_first_partial == True
        assert pm2.signal_tier == 'A'

    def test_full_pipeline(self):
        """PM -> dict -> save -> load -> from_dict"""
        pm = PositionManager(
            symbol='SOL/USDT', side='LONG',
            entry_price=150.0, stop_loss=145.0,
            position_size=10.0, is_v6_pyramid=True,
            neckline=160.0, equity_base=5000.0, initial_r=50.0,
        )
        pm.add_stage2(price=161.0, size=8.0)
        pm.add_stage3(price=165.0, size=6.0, swing_stop=158.0)

        pp = PositionPersistence(TEST_PATH)
        pp.save_positions({pm.symbol: pm.to_dict()})

        loaded = pp.load_positions()
        pm2 = PositionManager.from_dict(loaded['SOL/USDT'])

        assert pm2.stage == 3
        assert pm2.total_size == pytest.approx(24.0, abs=0.01)
        assert pm2.current_sl == 158.0
        assert len(pm2.entries) == 3
        assert pm2.entries[0].stage == 1
        assert pm2.entries[1].stage == 2
        assert pm2.entries[2].stage == 3

    def test_entries_preserved(self):
        """EntryRecord fields survive roundtrip"""
        pm = PositionManager(
            symbol='DOGE/USDT', side='LONG',
            entry_price=0.15, stop_loss=0.14,
            position_size=1000.0, is_v6_pyramid=True,
        )
        d = pm.to_dict()
        entries = d['entries']
        assert len(entries) == 1
        assert entries[0]['price'] == 0.15
        assert entries[0]['size'] == 1000.0
        assert entries[0]['stage'] == 1
        assert 'time' in entries[0]


# ==================== Phase 4 helpers ====================

def _make_df_1h_flat(n=20, close=100.0, high_offset=1.0, low_offset=1.0,
                     atr=1.0, vol_ma=1000.0, volume=1000.0,
                     ema_slow=100.0, ema_fast=None):
    """平坦 DataFrame（不含 swing points，所有行相同值）"""
    ef = ema_fast if ema_fast is not None else ema_slow
    return pd.DataFrame({
        'open':     [close] * n,
        'high':     [close + high_offset] * n,
        'low':      [close - low_offset] * n,
        'close':    [close] * n,
        'volume':   [volume] * n,
        'vol_ma':   [vol_ma] * n,
        'atr':      [atr] * n,
        'ema_slow': [ema_slow] * n,
        'ema_fast': [ef] * n,
    })


def _make_df_with_swing_low(n=20, base_low=99.0, swing_low=95.0,
                             base_close=100.0, atr=1.0, vol_ma=1000.0):
    """在 index 7 放入 swing low（left=5, right=2 確認）"""
    lows = [base_low] * n
    lows[7] = swing_low
    return pd.DataFrame({
        'open':     [base_close] * n,
        'high':     [base_close + 1.0] * n,
        'low':      lows,
        'close':    [base_close] * n,
        'volume':   [vol_ma] * n,
        'vol_ma':   [vol_ma] * n,
        'atr':      [atr] * n,
        'ema_slow': [base_close] * n,
        'ema_fast': [base_close] * n,
    })


def _make_df_with_swing_high(n=20, base_high=101.0, swing_high=105.0,
                              base_close=100.0, atr=1.0, vol_ma=1000.0):
    """在 index 7 放入 swing high（left=5, right=2 確認）"""
    highs = [base_high] * n
    highs[7] = swing_high
    return pd.DataFrame({
        'open':     [base_close] * n,
        'high':     highs,
        'low':      [base_close - 1.0] * n,
        'close':    [base_close] * n,
        'volume':   [vol_ma] * n,
        'vol_ma':   [vol_ma] * n,
        'atr':      [atr] * n,
        'ema_slow': [base_close] * n,
        'ema_fast': [base_close] * n,
    })


def _make_df_with_bos_long(n=20, swing_low=95.0, swing_high=108.0,
                            base_close=102.0, bos_close=110.0,
                            atr=1.0, vol_ma=1000.0):
    """
    LONG BOS 驗證數據（left=5, right=2）：
    index 7: swing high=108 (BOS target)
    index 12: swing low=95 (HL > current_sl)
    close[-1]=110 > 108 (BOS confirmed)
    時序：swing_high(7) → swing_low(12) → BOS close(19)
    """
    closes = [base_close] * n
    closes[-1] = bos_close

    highs = [base_close + 1.0] * n
    highs[7] = swing_high
    highs[-1] = bos_close + 1.0

    lows = [base_close - 3.0] * n
    lows[12] = swing_low

    return pd.DataFrame({
        'open':     [base_close] * n,
        'high':     highs,
        'low':      lows,
        'close':    closes,
        'volume':   [vol_ma] * n,
        'vol_ma':   [vol_ma] * n,
        'atr':      [atr] * n,
        'ema_slow': [base_close] * n,
        'ema_fast': [base_close] * n,
    })


def _make_df_with_bos_short(n=20, swing_high=105.0, swing_low=92.0,
                             base_close=98.0, bos_close=90.0,
                             atr=1.0, vol_ma=1000.0):
    """
    SHORT BOS 驗證數據（left=5, right=2）：
    index 7: swing low=92 (BOS target)
    index 12: swing high=105 (LH < current_sl)
    close[-1]=90 < 92 (BOS confirmed)
    時序：swing_low(7) → swing_high(12) → BOS close(19)
    """
    closes = [base_close] * n
    closes[-1] = bos_close

    lows = [base_close - 1.0] * n
    lows[7] = swing_low
    lows[-1] = bos_close - 1.0

    highs = [base_close + 3.0] * n
    highs[12] = swing_high

    return pd.DataFrame({
        'open':     [base_close] * n,
        'high':     highs,
        'low':      lows,
        'close':    closes,
        'volume':   [vol_ma] * n,
        'vol_ma':   [vol_ma] * n,
        'atr':      [atr] * n,
        'ema_slow': [base_close] * n,
        'ema_fast': [base_close] * n,
    })


def _make_pm_v6(side='LONG', entry=100.0, sl=95.0, size=1.0,
                neckline=110.0, stage=1, hours_ago=1):
    """建立 V6 PositionManager（直接設定 stage，跳過真實加倉流程）"""
    pm = PositionManager(
        symbol='BTC/USDT', side=side,
        entry_price=entry, stop_loss=sl,
        position_size=size, is_v6_pyramid=True,
        neckline=neckline, equity_base=10000.0, initial_r=100.0,
    )
    pm.stage = stage
    pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return pm


def _make_pm_v53(side='LONG', entry=100.0, sl=95.0, size=1.0, hours_ago=1):
    """建立 V5.3 PositionManager"""
    pm = PositionManager(
        symbol='ETH/USDT', side=side,
        entry_price=entry, stop_loss=sl,
        position_size=size, is_v6_pyramid=False,
        initial_r=100.0,
    )
    pm.entry_time = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return pm


class TestPositionExitDecision:
    """_get_exit_decision() 集中化出場決策引擎的 12 個場景測試"""

    def test_01_active_no_exit(self):
        """正常持倉：無任何觸發條件 → ACTIVE / NONE"""
        pm = _make_pm_v6(side='LONG', entry=100, sl=95, hours_ago=1,
                         stage=1, neckline=110)
        df_1h = _make_df_1h_flat(n=20, close=102.0, vol_ma=1000, volume=1000)
        d = pm._get_exit_decision(102.0, df_1h)
        assert d['action'] == 'ACTIVE'
        assert d['reason'] == 'NONE'
        assert d['new_sl'] is None

    def test_02_structure_trail_sl_long(self):
        """LONG 結構追蹤移損 (BOS): swing_low=95, atr=1, SL_ATR_BUFFER=0.8 -> new_sl=94.2 > sl=90"""
        pm = _make_pm_v6(side='LONG', entry=100, sl=90, size=1.0,
                         neckline=110, stage=2, hours_ago=1)
        df_1h = _make_df_with_bos_long(n=20, swing_low=95.0, swing_high=108.0,
                                        base_close=102.0, bos_close=110.0,
                                        atr=1.0, vol_ma=1000.0)
        d = pm._get_exit_decision(102.0, df_1h)
        assert d['action'] == 'ACTIVE'
        assert d['reason'] == 'STRUCTURE_TRAIL_SL'
        assert d['new_sl'] == pytest.approx(94.2, abs=0.01)
        assert pm.current_sl == pytest.approx(94.2, abs=0.01)

    def test_03_structure_trail_sl_short(self):
        """SHORT 結構追蹤移損 (BOS): swing_high=105, atr=1, SL_ATR_BUFFER=0.8 -> new_sl=105.8 < sl=110"""
        pm = _make_pm_v6(side='SHORT', entry=100, sl=110, size=1.0,
                         neckline=90, stage=2, hours_ago=1)
        df_1h = _make_df_with_bos_short(n=20, swing_high=105.0, swing_low=92.0,
                                         base_close=98.0, bos_close=90.0,
                                         atr=1.0, vol_ma=1000.0)
        d = pm._get_exit_decision(98.0, df_1h)
        assert d['action'] == 'ACTIVE'
        assert d['reason'] == 'STRUCTURE_TRAIL_SL'
        assert d['new_sl'] == pytest.approx(105.8, abs=0.01)
        assert pm.current_sl == pytest.approx(105.8, abs=0.01)

    def test_04_4h_ema20_force_long(self):
        """LONG：V6_4H_EMA20_FORCE_EXIT=False → 4H 條件被忽略，維持 ACTIVE"""
        pm = _make_pm_v6(side='LONG', entry=100, sl=95, stage=2, hours_ago=1)
        df_1h = _make_df_1h_flat(n=20, close=102.0)
        df_4h = _make_df_1h_flat(n=5, close=98.0, ema_fast=105.0)
        d = pm._get_exit_decision(102.0, df_1h, df_4h)
        assert d['action'] == 'ACTIVE'

    def test_05_4h_ema20_force_short(self):
        """SHORT：V6_4H_EMA20_FORCE_EXIT=False → 4H 條件被忽略，維持 ACTIVE"""
        pm = _make_pm_v6(side='SHORT', entry=100, sl=105, stage=2, hours_ago=1)
        df_1h = _make_df_1h_flat(n=20, close=98.0)
        df_4h = _make_df_1h_flat(n=5, close=110.0, ema_fast=105.0)
        d = pm._get_exit_decision(98.0, df_1h, df_4h)
        assert d['action'] == 'ACTIVE'

    def test_06_fast_stop_067r_v6(self):
        """V6 LONG：entry=100, sl=90, price=92.5 → r=-0.75 ≤ -EARLY_STOP_R_THRESHOLD(0.75) → FAST_STOP_067R"""
        pm = _make_pm_v6(side='LONG', entry=100, sl=90, size=1.0,
                         stage=1, hours_ago=1)
        df_1h = _make_df_1h_flat(n=20, close=92.5)
        d = pm._get_exit_decision(92.5, df_1h)
        assert d['action'] == 'CLOSE'
        assert d['reason'] == 'FAST_STOP_067R'

    def test_07_time_exit_v6(self):
        """V6 Stage 1 持倉 37h（>V6_STAGE1_MAX_HOURS=36h）且未升至 Stage 2 → TIME_EXIT"""
        pm = _make_pm_v6(side='LONG', entry=100, sl=95, stage=1,
                         neckline=110, hours_ago=37)
        # volume < vol_ma 阻止 stage2 trigger，low 偏低阻止 neckline break
        df_1h = _make_df_1h_flat(n=20, close=102.0, volume=500, vol_ma=1000)
        d = pm._get_exit_decision(102.0, df_1h)
        assert d['action'] == 'CLOSE'
        assert d['reason'] == 'TIME_EXIT'

    def test_08_profit_pullback(self):
        """LONG 獲利回吐：highest=110, price=104.5, entry=100 → pullback=55% ≥ PROFIT_PULLBACK_THRESHOLD(55%)"""
        pm = _make_pm_v6(side='LONG', entry=100, sl=95, stage=2, hours_ago=1)
        pm.highest_price = 110.0
        df_1h = _make_df_1h_flat(n=20, close=104.5)
        d = pm._get_exit_decision(104.5, df_1h)
        assert d['action'] == 'CLOSE'
        assert d['reason'] == 'PROFIT_PULLBACK'

    def test_09_stage2_trigger_neckline_break(self):
        """Stage 1 neckline(103) 突破 + 1.4x 放量 → STAGE2_TRIGGER"""
        pm = _make_pm_v6(side='LONG', entry=95, sl=90, size=1.0,
                         neckline=103.0, stage=1, hours_ago=1)
        df_1h = _make_df_1h_flat(n=20, close=106.0, volume=1400.0, vol_ma=1000.0)
        d = pm._get_exit_decision(106.0, df_1h)
        assert d['action'] == 'STAGE2_TRIGGER'
        assert d['reason'] == 'NECKLINE_BREAK'

    def test_10_stage3_trigger_ema_pullback(self):
        """Stage 2 EMA pullback + 縮量 + 反轉 K（LONG）→ STAGE3_TRIGGER"""
        pm = _make_pm_v6(side='LONG', entry=95, sl=90, size=1.0,
                         neckline=103.0, stage=2, hours_ago=1)
        n = 20
        rows = []
        for i in range(n):
            if i == n - 2:  # prev candle: EMA touch + reduced volume
                rows.append({'open': 100, 'high': 101.0, 'low': 100.5, 'close': 100.7,
                             'volume': 700, 'vol_ma': 1000, 'atr': 1.0,
                             'ema_slow': 100.0, 'ema_fast': 100.0})
            elif i == n - 1:  # current candle: close > prev high (reversal)
                rows.append({'open': 101, 'high': 103.0, 'low': 100.5, 'close': 103.0,
                             'volume': 1200, 'vol_ma': 1000, 'atr': 1.0,
                             'ema_slow': 100.0, 'ema_fast': 100.0})
            else:
                rows.append({'open': 102, 'high': 103.0, 'low': 101.0, 'close': 102.0,
                             'volume': 1000, 'vol_ma': 1000, 'atr': 1.0,
                             'ema_slow': 100.0, 'ema_fast': 100.0})
        df_1h = pd.DataFrame(rows)
        d = pm._get_exit_decision(103.0, df_1h)
        assert d['action'] == 'STAGE3_TRIGGER'
        assert d['reason'] == 'EMA_PULLBACK'

    def test_11_fast_stop_067r_v53(self):
        """V5.3 LONG：共同路徑 early stop, price=92.5 → r=-0.75 ≤ -EARLY_STOP_R_THRESHOLD(0.75) → FAST_STOP_067R"""
        pm = _make_pm_v53(side='LONG', entry=100, sl=90, hours_ago=1)
        df_1h = _make_df_1h_flat(n=20, close=92.5)
        d = pm._get_exit_decision(92.5, df_1h)
        assert d['action'] == 'CLOSE'
        assert d['reason'] == 'FAST_STOP_067R'

    def test_12_time_exit_v53(self):
        """V5.3 持倉 25h 且 is_first_partial=False → TIME_EXIT"""
        pm = _make_pm_v53(side='LONG', entry=100, sl=95, hours_ago=25)
        pm.is_first_partial = False
        df_1h = _make_df_1h_flat(n=20, close=102.0)
        d = pm._get_exit_decision(102.0, df_1h)
        assert d['action'] == 'CLOSE'
        assert d['reason'] == 'TIME_EXIT'


class TestPerformanceDB:
    """Tests for PerformanceDB SQLite writer."""

    def test_init_creates_table(self, tmp_path):
        """DB 初始化後 trades table 應存在。"""
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        assert "trades" in tables

    def test_record_trade_success(self, tmp_path):
        """正常寫入一筆記錄。"""
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        data = {
            "trade_id": "test-001", "symbol": "BTCUSDT", "side": "LONG",
            "is_v6_pyramid": 1, "signal_tier": "A",
            "entry_price": 90000.0, "exit_price": 92000.0, "total_size": 0.01,
            "initial_r": 50.0, "entry_time": "2026-01-01T00:00:00",
            "exit_time": "2026-01-02T00:00:00", "holding_hours": 24.0,
            "pnl_usdt": 20.0, "pnl_pct": 2.22, "realized_r": 0.4,
            "mfe_pct": 3.0, "mae_pct": -0.5, "capture_ratio": 0.74,
            "stage_reached": 2, "exit_reason": "STRUCTURE_TRAIL_SL",
            "market_regime": "STRONG",
            "entry_adx": None, "fakeout_depth_atr": None,
        }
        result = db.record_trade(data)
        assert result is True
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 1

    def test_duplicate_trade_id_ignored(self, tmp_path):
        """同一 trade_id 重複寫入應被忽略（crash 重啟安全）。"""
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        data = {
            "trade_id": "dup-001", "symbol": "ETHUSDT", "side": "SHORT",
            "is_v6_pyramid": 0, "signal_tier": "B",
            "entry_price": 3000.0, "exit_price": 2900.0, "total_size": 0.1,
            "initial_r": 30.0, "entry_time": "2026-01-01T00:00:00",
            "exit_time": "2026-01-01T12:00:00", "holding_hours": 12.0,
            "pnl_usdt": 10.0, "pnl_pct": 3.33, "realized_r": 0.33,
            "mfe_pct": 4.0, "mae_pct": -0.2, "capture_ratio": 0.83,
            "stage_reached": 1, "exit_reason": "TIME_EXIT",
            "market_regime": "TRENDING",
            "entry_adx": None, "fakeout_depth_atr": None,
        }
        db.record_trade(data)
        db.record_trade(data)  # 重複
        import sqlite3
        with sqlite3.connect(db.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        assert count == 1

    def test_record_trade_db_failure_nonfatal(self, tmp_path):
        """DB 路徑無效時 record_trade 應回傳 False，不拋 exception。"""
        db = PerformanceDB.__new__(PerformanceDB)
        db.db_path = "/nonexistent/path/test.db"
        result = db.record_trade({"trade_id": "x"})
        assert result is False


class TestProfitPullbackMFEThreshold:
    """profit_pullback 最低 MFE 門檻（MIN_MFE_R_FOR_PULLBACK）測試"""

    def test_profit_pullback_skipped_when_mfe_below_threshold(self):
        """profit_pullback should NOT trigger when MFE < MIN_MFE_R_FOR_PULLBACK"""
        pm = PositionManager(
            symbol='TEST/USDT', side='LONG',
            entry_price=100.0, stop_loss=98.0,
            position_size=1.0, is_v6_pyramid=True,
            initial_r=2.0,
        )
        # risk_dist = 2.0, MIN_MFE_R = 0.3 → need MFE >= 0.6 price units
        # Set highest_price to small gain (0.2R = 0.4 price units, below threshold)
        pm.highest_price = 100.4
        # Current price pulls back 60% of MFE (above PULLBACK_THRESHOLD)
        current_price = 100.16  # pullback = (100.4-100.16)/0.4 = 60%
        df_1h = _make_df_1h_flat(n=20, close=current_price)
        decision = pm.monitor(current_price, df_1h)
        assert decision['action'] != 'CLOSE', \
            f"Should not trigger pullback at MFE 0.2R, got {decision}"

    def test_profit_pullback_triggers_when_mfe_above_threshold(self):
        """profit_pullback SHOULD trigger when MFE >= MIN_MFE_R_FOR_PULLBACK"""
        pm = PositionManager(
            symbol='TEST/USDT', side='LONG',
            entry_price=100.0, stop_loss=98.0,
            position_size=1.0, is_v6_pyramid=True,
            initial_r=2.0,
        )
        # risk_dist = 2.0, set MFE = 0.5R = 1.0 price units (above 0.3R threshold)
        pm.highest_price = 101.0
        # Current price pulls back 60% of MFE
        current_price = 100.4  # pullback = 0.6/1.0 = 60% >= 55%
        df_1h = _make_df_1h_flat(n=20, close=current_price)
        decision = pm.monitor(current_price, df_1h)
        assert decision['action'] == 'CLOSE', \
            f"Should trigger pullback at MFE 0.5R, got {decision}"

    def test_profit_pullback_short_mfe_threshold(self):
        """SHORT: profit_pullback respects MIN_MFE_R threshold"""
        pm = PositionManager(
            symbol='TEST/USDT', side='SHORT',
            entry_price=100.0, stop_loss=102.0,
            position_size=1.0, is_v6_pyramid=True,
            initial_r=2.0,
        )
        # risk_dist = 2.0, small MFE = 0.1R (below threshold)
        pm.lowest_price = 99.8
        pm.highest_price = 100.0
        current_price = 99.92  # pullback = 0.12/0.2 = 60%
        df_1h = _make_df_1h_flat(n=20, close=current_price)
        decision = pm.monitor(current_price, df_1h)
        assert decision['action'] != 'CLOSE', \
            f"SHORT: should not trigger pullback at MFE 0.1R"

    def test_strategy_dispatch_v53_no_pullback(self):
        """non-V6 trade should use V53SopStrategy (no profit_pullback)"""
        from v6.strategies.v53_sop import V53SopStrategy
        pm = PositionManager(
            symbol='TEST/USDT', side='LONG',
            entry_price=100.0, stop_loss=98.0,
            position_size=1.0, is_v6_pyramid=False,
            initial_r=2.0,
        )
        assert isinstance(pm.strategy, V53SopStrategy), \
            f"non-V6 trade should use V53SopStrategy, got {type(pm.strategy)}"
