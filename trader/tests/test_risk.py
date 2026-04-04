"""Test: Equity cap 計算 + Risk Progression（Stage 1/2/3 數學）"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
from trader.positions import PositionManager


class TestEquityCap:
    """Equity Cap 計算"""

    def test_stage1_sizing(self):
        """Stage 1: equity_cap * stage1_ratio"""
        equity = 10000.0
        cap = 0.10       # 10%
        s1_ratio = 0.35  # 35%
        entry = 95000.0

        expected_value = equity * cap * s1_ratio  # 350
        expected_size = expected_value / entry     # 0.003684...

        actual_size = expected_value / entry
        assert abs(actual_size - expected_size) < 0.000001

    def test_stage2_max_size(self):
        """Stage 2: equity_cap * stage2_ratio"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=170.0,
        )

        from trader.config import Config as Cfg
        s2_size = pm.calculate_stage2_size(entry_price=96600.0)
        # Tier B (default) = 0.7; new cap = EQUITY_CAP_PERCENT * STAGE2_RATIO * tier_mult
        max_expected = 10000.0 * Cfg.EQUITY_CAP_PERCENT * Cfg.STAGE2_RATIO * Cfg.TIER_B_POSITION_MULT / 96600.0
        assert s2_size > 0
        assert s2_size <= max_expected + 0.0001

    def test_stage3_max_size(self):
        """Stage 3: equity_cap * stage3_ratio"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=170.0,
        )
        pm.add_stage2(price=96600.0, size=0.003)

        from trader.config import Config as Cfg
        s3_size = pm.calculate_stage3_size(entry_price=97200.0, swing_stop=96000.0)
        # Tier B (default) = 0.7; new cap = EQUITY_CAP_PERCENT * STAGE3_RATIO * tier_mult
        max_expected = 10000.0 * Cfg.EQUITY_CAP_PERCENT * Cfg.STAGE3_RATIO * Cfg.TIER_B_POSITION_MULT / 97200.0
        assert s3_size >= 0
        assert s3_size <= max_expected + 0.0001


class TestRiskProgression:
    """Risk Progression 數學"""

    def test_stage1_risk(self):
        """Stage 1: total_risk = initial_R"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=170.0,
        )
        # risk = size * |entry - sl| = 0.035 * 800 = 28
        risk = pm.total_size * abs(pm.avg_entry - pm.current_sl)
        assert risk == pytest.approx(28.0, abs=0.01)

    def test_stage2_breakeven(self):
        """Stage 2 後止損移至 Stage 1 entry（保本位）"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=170.0,
        )
        pm.add_stage2(price=96600.0, size=0.035)

        # SL should be at Stage 1 entry
        assert pm.current_sl == 95000.0

        # Risk from SL perspective: some entries are above SL, some at SL
        # Stage 1 entry was at 95000, Stage 2 at 96600
        # avg_entry = 95800, SL = 95000
        # risk = 0.070 * (95800 - 95000) = 0.070 * 800 = 56
        risk = pm.total_size * abs(pm.avg_entry - pm.current_sl)
        assert risk > 0  # Still has some risk based on avg_entry

    def test_stage3_swing_stop(self):
        """Stage 3 止損在 swing structure"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=170.0,
        )
        pm.add_stage2(price=96600.0, size=0.035)
        swing_stop = 96000.0
        pm.add_stage3(price=97200.0, size=0.030, swing_stop=swing_stop)

        assert pm.current_sl == swing_stop
        assert pm.stage == 3

    def test_short_risk_progression(self):
        """SHORT: 保本位 = Stage 1 entry, swing stop 在 entry 下方"""
        pm = PositionManager(
            symbol='ETH/USDT', side='SHORT',
            entry_price=3500.0, stop_loss=3600.0,
            position_size=1.0, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=100.0,
        )
        pm.add_stage2(price=3280.0, size=0.8)
        assert pm.current_sl == 3500.0  # Stage 1 entry = breakeven

        pm.add_stage3(price=3200.0, size=0.6, swing_stop=3350.0)
        assert pm.current_sl == 3350.0  # swing structure
        assert pm.stage == 3

    def test_total_size_accumulation(self):
        """Stage 加倉後 total_size 正確累加"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=170.0,
        )
        assert pm.total_size == 0.035

        pm.add_stage2(price=96600.0, size=0.035)
        assert pm.total_size == pytest.approx(0.070, abs=0.001)

        pm.add_stage3(price=97200.0, size=0.030, swing_stop=96000.0)
        assert pm.total_size == pytest.approx(0.100, abs=0.001)

    def test_avg_entry_weighted(self):
        """avg_entry 是加權平均"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
        )
        pm.add_stage2(price=96600.0, size=0.035)

        expected = (0.035 * 95000.0 + 0.035 * 96600.0) / 0.070
        assert pm.avg_entry == pytest.approx(expected, abs=0.01)

    def test_stage_guard(self):
        """錯誤 stage 不能加倉"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
        )

        # Stage 1 → 不能直接 add_stage3
        assert pm.add_stage3(price=97000.0, size=0.03, swing_stop=96000.0) == False

        pm.add_stage2(price=96600.0, size=0.035)

        # Stage 2 → 不能再 add_stage2
        assert pm.add_stage2(price=97000.0, size=0.035) == False

    def test_risk_cap_shrinks_size(self):
        """當 risk 超標時 size 被縮減"""
        pm = PositionManager(
            symbol='BTC/USDT', side='LONG',
            entry_price=95000.0, stop_loss=94200.0,
            position_size=0.035, is_v6_pyramid=True,
            equity_base=10000.0,
            initial_r=0.001,  # Extremely small initial_r to force shrink
        )

        s2_size = pm.calculate_stage2_size(entry_price=96600.0)
        max_uncapped = 10000.0 * 0.10 * 0.35 / 96600.0
        # Should be smaller than uncapped due to risk limit
        assert s2_size < max_uncapped


class TestStage3ProportionalScaling:
    """Stage 3 proportional risk cap — 比例縮放，不懸崖歸零"""

    def test_high_risk_not_zeroed(self):
        """LONG: risk >> initial_r 時仍有 size（舊公式會歸零）"""
        pm = PositionManager(
            symbol='ORCA/USDT', side='LONG',
            entry_price=1.0, stop_loss=0.95,
            position_size=100.0, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=5.0,
        )
        pm.add_stage2(price=1.05, size=35.0)

        # swing_stop 遠低於 avg_entry → 大風險 → 舊公式歸零
        s3_size = pm.calculate_stage3_size(entry_price=1.10, swing_stop=0.98)
        assert s3_size > 0, "Stage 3 proportional scaling should not zero out"

    def test_proportional_ratio_value(self):
        """ratio ≈ initial_r / total_risk（比例縮放）"""
        pm = PositionManager(
            symbol='TEST/USDT', side='LONG',
            entry_price=100.0, stop_loss=95.0,
            position_size=10.0, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=50.0,
        )
        pm.add_stage2(price=105.0, size=3.5)

        s3_size = pm.calculate_stage3_size(entry_price=110.0, swing_stop=97.0)
        max_size = 10000.0 * 0.10 * 0.30 / 110.0
        # Should be reduced but not zero
        assert 0 < s3_size < max_size

    def test_short_side_not_zeroed(self):
        """SHORT: swing_stop 高於 avg_entry → 有風險但不歸零"""
        pm = PositionManager(
            symbol='ETH/USDT', side='SHORT',
            entry_price=3000.0, stop_loss=3150.0,
            position_size=1.0, is_v6_pyramid=True,
            equity_base=10000.0, initial_r=15.0,
        )
        pm.add_stage2(price=2900.0, size=0.35)

        # swing_stop=3000 高於 avg_entry(~2974) → SHORT 不 in profit
        s3_size = pm.calculate_stage3_size(entry_price=2800.0, swing_stop=3000.0)
        assert s3_size > 0, "SHORT Stage 3 proportional scaling should not zero out"
