"""Tier Diagnostic Fields 測試"""
import pytest
from unittest.mock import patch, MagicMock
from trader.risk.manager import SignalTierSystem
from trader.config import Config


class TestSignalTierScoreReturn:
    """Test calculate_signal_tier returns (tier, mult, score)"""

    def test_tier_a_returns_score(self):
        """Tier A: MTF + strong market + strong vol = score 6+"""
        details = {'candle_confirmed': True}
        tier, mult, score = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=True, market_strong=True, volume_grade='strong'
        )
        assert tier == 'A'
        assert mult == Config.TIER_A_POSITION_MULT
        assert score == 7  # 2+2+2+1

    def test_tier_a_without_candle(self):
        """Tier A: MTF + strong market + strong vol (no candle) = score 6"""
        details = {'candle_confirmed': False}
        tier, mult, score = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=True, market_strong=True, volume_grade='strong'
        )
        assert tier == 'A'
        assert score == 6  # 2+2+2+0

    def test_tier_b_score(self):
        """Tier B: MTF + strong market + moderate vol (no candle) = score 5"""
        details = {'candle_confirmed': False}
        tier, mult, score = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=True, market_strong=True, volume_grade='moderate'
        )
        assert tier == 'B'
        assert mult == Config.TIER_B_POSITION_MULT
        assert score == 5  # 2+2+1+0

    def test_tier_c_score(self):
        """Tier C: no MTF + no strong + moderate vol = score 1"""
        details = {'candle_confirmed': False}
        tier, mult, score = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=False, market_strong=False, volume_grade='moderate'
        )
        assert tier == 'C'
        assert mult == Config.TIER_C_POSITION_MULT
        assert score == 1  # 0+0+1+0

    def test_disabled_returns_minus_one(self):
        """ENABLE_TIERED_ENTRY=False => score=-1"""
        with patch.object(Config, 'ENABLE_TIERED_ENTRY', False):
            tier, mult, score = SignalTierSystem.calculate_signal_tier(
                {}, mtf_aligned=True, market_strong=True, volume_grade='strong'
            )
            assert tier == 'B'
            assert score == -1

    def test_weak_volume_zero_points(self):
        """weak volume gives 0 points"""
        details = {'candle_confirmed': True}
        tier, mult, score = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=True, market_strong=True, volume_grade='weak'
        )
        assert score == 5  # 2+2+0+1 → Tier B

    def test_explosive_same_as_strong(self):
        """explosive volume gives same 2 points as strong"""
        details = {'candle_confirmed': False}
        _, _, score_exp = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=True, market_strong=True, volume_grade='explosive'
        )
        _, _, score_str = SignalTierSystem.calculate_signal_tier(
            details, mtf_aligned=True, market_strong=True, volume_grade='strong'
        )
        assert score_exp == score_str == 6


class TestTierDiagnosticPersistence:
    """Test 4 new fields persist through to_dict/from_dict"""

    def test_pm_to_from_dict_tier_fields(self):
        """trend_adx/mtf_aligned/volume_grade/tier_score survive serialization"""
        from trader.positions import PositionManager
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=100.0, stop_loss=95.0,
            position_size=1.0, is_v6_pyramid=True,
            initial_r=50.0, signal_tier='A',
        )
        pm.trend_adx = 35.5
        pm.mtf_aligned = True
        pm.volume_grade = 'strong'
        pm.tier_score = 7

        data = pm.to_dict()
        assert data['trend_adx'] == 35.5
        assert data['mtf_aligned'] == True
        assert data['volume_grade'] == 'strong'
        assert data['tier_score'] == 7

        pm2 = PositionManager.from_dict(data)
        assert pm2.trend_adx == 35.5
        assert pm2.mtf_aligned == True
        assert pm2.volume_grade == 'strong'
        assert pm2.tier_score == 7

    def test_pm_backward_compat_none(self):
        """Old positions.json without new fields => all None"""
        from trader.positions import PositionManager
        pm = PositionManager(
            symbol='ETH/USDT', side='SHORT',
            entry_price=3000.0, stop_loss=3100.0,
            position_size=0.5, is_v6_pyramid=False,
            initial_r=25.0,
        )
        data = pm.to_dict()
        # Simulate old format: remove new fields
        data.pop('trend_adx', None)
        data.pop('mtf_aligned', None)
        data.pop('volume_grade', None)
        data.pop('tier_score', None)

        pm2 = PositionManager.from_dict(data)
        assert pm2.trend_adx is None
        assert pm2.mtf_aligned is None
        assert pm2.volume_grade is None
        assert pm2.tier_score is None


class TestPerfDBTierDiagnostic:
    """Test perf_db record_trade with new fields"""

    def test_record_with_tier_fields(self, tmp_path):
        """record_trade accepts and stores 4 new fields"""
        from trader.infrastructure.performance_db import PerformanceDB
        db = PerformanceDB(str(tmp_path / 'test.db'))
        data = {
            'trade_id': 'test_tier_diag_001',
            'symbol': 'BTC/USDT', 'side': 'LONG',
            'is_v6_pyramid': 1, 'signal_tier': 'A',
            'entry_price': 100.0, 'exit_price': 110.0,
            'total_size': 1.0, 'initial_r': 50.0,
            'entry_time': '2026-03-10T00:00:00+00:00',
            'exit_time': '2026-03-10T12:00:00+00:00',
            'holding_hours': 12.0,
            'pnl_usdt': 10.0, 'pnl_pct': 10.0,
            'realized_r': 0.2, 'mfe_pct': 12.0, 'mae_pct': -1.0,
            'capture_ratio': 0.83,
            'stage_reached': 1, 'exit_reason': 'sl_hit',
            'market_regime': 'STRONG',
            'entry_adx': 28.5, 'fakeout_depth_atr': 0.5,
            # New fields
            'trend_adx': 35.5,
            'mtf_aligned': 1,
            'volume_grade': 'strong',
            'tier_score': 7,
        }
        assert db.record_trade(data)

        import sqlite3
        conn = sqlite3.connect(str(tmp_path / 'test.db'))
        row = conn.execute(
            'SELECT trend_adx, mtf_aligned, volume_grade, tier_score FROM trades WHERE trade_id=?',
            ('test_tier_diag_001',)
        ).fetchone()
        assert row == (35.5, 1, 'strong', 7)
        conn.close()

    def test_record_without_tier_fields_defaults_none(self, tmp_path):
        """Old callers without new fields => all NULL in DB"""
        from trader.infrastructure.performance_db import PerformanceDB
        db = PerformanceDB(str(tmp_path / 'test.db'))
        data = {
            'trade_id': 'test_tier_diag_002',
            'symbol': 'ETH/USDT', 'side': 'SHORT',
            'is_v6_pyramid': 0, 'signal_tier': 'B',
            'entry_price': 3000.0, 'exit_price': 2900.0,
            'total_size': 0.5, 'initial_r': 25.0,
            'entry_time': '2026-03-10T00:00:00+00:00',
            'exit_time': '2026-03-10T06:00:00+00:00',
            'holding_hours': 6.0,
            'pnl_usdt': 50.0, 'pnl_pct': 3.33,
            'realized_r': 2.0, 'mfe_pct': 4.0, 'mae_pct': -0.5,
            'capture_ratio': 0.83,
            'stage_reached': 1, 'exit_reason': 'profit_pullback',
            'market_regime': 'TRENDING',
            'entry_adx': 22.0, 'fakeout_depth_atr': None,
            # Intentionally omit new fields
        }
        assert db.record_trade(data)

        import sqlite3
        conn = sqlite3.connect(str(tmp_path / 'test.db'))
        row = conn.execute(
            'SELECT trend_adx, mtf_aligned, volume_grade, tier_score FROM trades WHERE trade_id=?',
            ('test_tier_diag_002',)
        ).fetchone()
        assert row == (None, None, None, None)
        conn.close()

    def test_migration_adds_columns(self, tmp_path):
        """Migration idempotent: 重複 init 不 crash"""
        from trader.infrastructure.performance_db import PerformanceDB
        db1 = PerformanceDB(str(tmp_path / 'migrate.db'))
        db2 = PerformanceDB(str(tmp_path / 'migrate.db'))  # Second init
        # Should not raise
        assert db2 is not None
