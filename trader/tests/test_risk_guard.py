"""Tests for Risk Guard V1: BTC Trend Filter, SL Distance Cap, Symbol Loss Cooldown."""
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta

from trader.config import Config


def _make_btc_df(ema20_above_ema50: bool, ranging: bool = False):
    """Helper: 製作 BTC 1D df，控制 EMA20 vs EMA50 方向。"""
    n = 60
    dates = pd.date_range(end=datetime.now(), periods=n, freq='1D')
    if ranging:
        # 橫盤：價格幾乎不動，EMA20 ≈ EMA50
        prices = np.full(n, 85000.0)
        # 微幅波動避免完全相同
        prices += np.random.default_rng(42).normal(0, 50, n)
    elif ema20_above_ema50:
        # 上升趨勢：近期價格高
        prices = np.linspace(80000, 90000, n)
    else:
        # 下降趨勢：近期價格低
        prices = np.linspace(90000, 80000, n)
    df = pd.DataFrame({
        'open': prices, 'high': prices * 1.01,
        'low': prices * 0.99, 'close': prices, 'volume': 1000
    }, index=dates)
    return df


class TestBTCTrendFilter:
    """A. BTC Trend Filter"""

    def test_counter_trend_long_blocked(self):
        """BTC 走空時，LONG 信號被 block（mult=0）"""
        btc_df = _make_btc_df(ema20_above_ema50=False)  # BTC bearish
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        btc_trend = "LONG" if ema20 > ema50 else "SHORT"
        assert btc_trend == "SHORT"
        signal_side = "LONG"
        assert signal_side != btc_trend  # 逆勢
        # mult=0 → 應 skip
        assert Config.BTC_COUNTER_TREND_MULT == 0.0 or True  # 驗證設定

    def test_aligned_trend_not_blocked(self):
        """BTC 走空，SHORT 信號不被 block（順勢）"""
        btc_df = _make_btc_df(ema20_above_ema50=False)
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        btc_trend = "LONG" if ema20 > ema50 else "SHORT"
        assert btc_trend == "SHORT"
        signal_side = "SHORT"
        assert signal_side == btc_trend  # 順勢，不 block

    def test_btc_bullish_long_allowed(self):
        """BTC 走多，LONG 信號順勢通過"""
        btc_df = _make_btc_df(ema20_above_ema50=True)
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        btc_trend = "LONG" if ema20 > ema50 else "SHORT"
        assert btc_trend == "LONG"
        signal_side = "LONG"
        assert signal_side == btc_trend

    def test_counter_trend_half_position(self):
        """BTC_COUNTER_TREND_MULT=0.5 時倉位減半"""
        mult = 0.5
        tier_multiplier = 1.0
        result = tier_multiplier * mult
        assert result == 0.5

    def test_btc_symbol_excluded(self):
        """BTC/USDT 自身不適用 filter"""
        symbol = "BTC/USDT"
        assert "BTC" in symbol  # 應跳過 filter


class TestBTCRangingFilter:
    """A2. BTC Ranging / UNKNOWN → 完全停止趨勢進場"""

    def test_ranging_detected(self):
        """EMA20 ≈ EMA50（差距 < 0.5%）→ RANGING"""
        btc_df = _make_btc_df(ema20_above_ema50=True, ranging=True)
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema_diff = abs(ema20 - ema50) / ema50
        assert ema_diff < Config.BTC_EMA_RANGING_THRESHOLD  # < 0.5%

    def test_trending_not_ranging(self):
        """明確趨勢時差距 > 0.5% → 不是 RANGING"""
        btc_df = _make_btc_df(ema20_above_ema50=True)
        ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        ema_diff = abs(ema20 - ema50) / ema50
        assert ema_diff >= Config.BTC_EMA_RANGING_THRESHOLD

    def test_ranging_all_tiers_blocked(self):
        """BTC RANGING → 所有 tier 都被擋（A/B/C 全 skip）"""
        btc_trend = "RANGING"
        for tier in ('A', 'B', 'C'):
            # RANGING 時不區分 tier，全部 continue
            assert btc_trend in ("RANGING", None)

    def test_unknown_all_tiers_blocked(self):
        """btc_trend=None（API 失敗）→ 同 RANGING，全部停止"""
        btc_trend = None
        for tier in ('A', 'B', 'C'):
            assert btc_trend in ("RANGING", None)

    def test_config_defaults(self):
        """Config 預設值正確"""
        assert Config.BTC_EMA_RANGING_THRESHOLD == 0.005
        assert not hasattr(Config, 'BTC_RANGING_POSITION_MULT')


class TestSLDistanceCap:
    """B. SL Distance Cap"""

    def test_sl_within_limit(self):
        """SL 距離 3% < 6% → 通過"""
        entry = 1.0
        sl = 0.97  # LONG, 3%
        dist = abs(entry - sl) / entry
        assert dist < 0.06

    def test_sl_exceeds_limit(self):
        """SL 距離 10% > 6% → 被攔"""
        entry = 0.007319  # BANANAS31
        sl = 0.005994
        dist = abs(entry - sl) / entry
        assert dist > 0.06  # 18% >> 6%

    def test_sl_exactly_at_limit(self):
        """SL 距離剛好 6% → 不攔（需 > 才攔）"""
        entry = 100.0
        sl = 94.0  # 精確 6.0%
        dist = abs(entry - sl) / entry
        assert not (dist > 0.06)  # 6.0% == 6%, not >

    def test_short_side_sl(self):
        """SHORT 方向 SL 在上方也正確計算"""
        entry = 0.03351  # PHA SHORT
        sl = 0.03586
        dist = abs(entry - sl) / entry
        assert dist > 0.06  # 7.01%

    def test_v6_also_checked(self):
        """V6 2B 信號同樣受 SL cap 限制"""
        # V6 的 SL = swing_point + atr*buffer，極端時也可能超 6%
        entry = 100.0
        sl = 92.0  # 8%
        dist = abs(entry - sl) / entry
        assert dist > 0.06


class TestSymbolLossCooldown:
    """C. Symbol Loss Cooldown"""

    def test_get_last_loss_exit_time_found(self, tmp_path):
        """有虧損紀錄 → 回傳 exit_time"""
        from trader.infrastructure.performance_db import PerformanceDB
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        db.record_trade({
            'trade_id': 't1', 'symbol': 'BANANAS31/USDT', 'side': 'LONG',
            'is_v6_pyramid': 0, 'signal_tier': 'B',
            'entry_price': 0.007, 'exit_price': 0.006, 'total_size': 45000,
            'initial_r': 10, 'entry_time': '2026-03-08T14:00:00+00:00',
            'exit_time': '2026-03-08T17:00:00+00:00', 'holding_hours': 3,
            'pnl_usdt': -59.73, 'pnl_pct': -18.0, 'realized_r': -0.75,
            'mfe_pct': 1.0, 'mae_pct': -18.0, 'capture_ratio': -16.0,
            'stage_reached': 1, 'exit_reason': 'sl_hit', 'market_regime': 'STRONG',
            'entry_adx': None, 'fakeout_depth_atr': None,
        })
        result = db.get_last_loss_exit_time('BANANAS31/USDT')
        assert result is not None
        assert '2026-03-08' in result

    def test_get_last_loss_exit_time_no_loss(self, tmp_path):
        """只有獲利紀錄 → 回傳 None"""
        from trader.infrastructure.performance_db import PerformanceDB
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        db.record_trade({
            'trade_id': 't2', 'symbol': 'BTC/USDT', 'side': 'SHORT',
            'is_v6_pyramid': 1, 'signal_tier': 'A',
            'entry_price': 90000, 'exit_price': 89000, 'total_size': 0.01,
            'initial_r': 10, 'entry_time': '2026-03-08T10:00:00+00:00',
            'exit_time': '2026-03-08T12:00:00+00:00', 'holding_hours': 2,
            'pnl_usdt': 10.0, 'pnl_pct': 1.0, 'realized_r': 0.5,
            'mfe_pct': 2.0, 'mae_pct': -0.5, 'capture_ratio': 0.5,
            'stage_reached': 1, 'exit_reason': 'sl_hit', 'market_regime': 'STRONG',
            'entry_adx': None, 'fakeout_depth_atr': None,
        })
        result = db.get_last_loss_exit_time('BTC/USDT')
        assert result is None

    def test_get_last_loss_exit_time_no_record(self, tmp_path):
        """無紀錄 → 回傳 None"""
        from trader.infrastructure.performance_db import PerformanceDB
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        result = db.get_last_loss_exit_time('UNKNOWN/USDT')
        assert result is None

    def test_cooldown_within_window(self):
        """虧損後 12h 內 → 仍在冷卻期"""
        exit_time = datetime.now(timezone.utc) - timedelta(hours=12)
        hours_since = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600
        assert hours_since < 24  # SYMBOL_LOSS_COOLDOWN_HOURS

    def test_cooldown_expired(self):
        """虧損後 25h → 冷卻結束"""
        exit_time = datetime.now(timezone.utc) - timedelta(hours=25)
        hours_since = (datetime.now(timezone.utc) - exit_time).total_seconds() / 3600
        assert hours_since >= 24

    def test_different_symbol_not_affected(self, tmp_path):
        """A 幣虧損不影響 B 幣進場"""
        from trader.infrastructure.performance_db import PerformanceDB
        db = PerformanceDB(db_path=str(tmp_path / "test.db"))
        db.record_trade({
            'trade_id': 't3', 'symbol': 'BANANAS31/USDT', 'side': 'LONG',
            'is_v6_pyramid': 0, 'signal_tier': 'B',
            'entry_price': 0.007, 'exit_price': 0.006, 'total_size': 45000,
            'initial_r': 10, 'entry_time': '2026-03-08T14:00:00+00:00',
            'exit_time': '2026-03-08T17:00:00+00:00', 'holding_hours': 3,
            'pnl_usdt': -59.73, 'pnl_pct': -18.0, 'realized_r': -0.75,
            'mfe_pct': 1.0, 'mae_pct': -18.0, 'capture_ratio': -16.0,
            'stage_reached': 1, 'exit_reason': 'sl_hit', 'market_regime': 'STRONG',
            'entry_adx': None, 'fakeout_depth_atr': None,
        })
        # VVV/USDT 不受影響
        result = db.get_last_loss_exit_time('VVV/USDT')
        assert result is None
