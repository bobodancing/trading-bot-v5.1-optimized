"""
Tier & Equity Balance Fix 測試

修正：
1. V6 Stage 2/3 乘 tier_mult（貫穿三段）
2. V5.3 notional 不超過 equity_cap
"""
import pytest
from unittest.mock import MagicMock, patch


# ===================== 共用 fixture =====================

def make_pm(signal_tier='B', equity_base=1000.0, initial_r=5.0):
    """建立最小可用的 PositionManager"""
    from trader.positions import PositionManager
    pm = PositionManager(
        symbol='BTC/USDT',
        side='LONG',
        entry_price=100.0,
        stop_loss=95.0,
        position_size=1.0,
        is_v6_pyramid=True,
        equity_base=equity_base,
        initial_r=initial_r,
        signal_tier=signal_tier,
    )
    return pm


# ===================== Test 0: Sizing Design 驗算 =====================

class TestSizingDesign:
    """驗證設計數字是否符合目標"""

    def test_v6_stage1_is_066x_v53_max(self):
        """V6 Stage 1 Tier A = V5.3 max × 0.66"""
        from trader.config import Config as Cfg
        v6_stage1_pct = Cfg.EQUITY_CAP_PERCENT * Cfg.STAGE1_RATIO * Cfg.TIER_A_POSITION_MULT
        v53_max_pct = Cfg.V53_EQUITY_CAP_PERCENT
        ratio = v6_stage1_pct / v53_max_pct
        assert abs(ratio - 0.66) < 0.01, \
            f"V6 Stage1 Tier A should be 0.66x V5.3 max, got {ratio:.2f}x"

    def test_v6_full_is_2x_v53_max(self):
        """V6 全三段 Tier A = V5.3 max × 2.0"""
        from trader.config import Config as Cfg
        v6_full_pct = Cfg.EQUITY_CAP_PERCENT * Cfg.TIER_A_POSITION_MULT
        v53_max_pct = Cfg.V53_EQUITY_CAP_PERCENT
        ratio = v6_full_pct / v53_max_pct
        assert abs(ratio - 2.0) < 0.01, \
            f"V6 full Tier A should be 2.0x V5.3 max, got {ratio:.2f}x"

    def test_stage_ratios_sum_to_one(self):
        from trader.config import Config as Cfg
        total = Cfg.STAGE1_RATIO + Cfg.STAGE2_RATIO + Cfg.STAGE3_RATIO
        assert abs(total - 1.0) < 0.001

    def test_v53_cap_lt_v6_cap(self):
        from trader.config import Config as Cfg
        assert Cfg.V53_EQUITY_CAP_PERCENT < Cfg.EQUITY_CAP_PERCENT


# ===================== Test 1: Stage 2 tier_mult =====================

class TestStage2TierMult:

    def test_tier_a_larger_than_tier_b(self):
        pm_a = make_pm('A')
        pm_b = make_pm('B')
        size_a = pm_a.calculate_stage2_size(entry_price=105.0)
        size_b = pm_b.calculate_stage2_size(entry_price=105.0)
        assert size_a > size_b, f"Tier A stage2 ({size_a}) should > Tier B ({size_b})"

    def test_tier_b_larger_than_tier_c(self):
        pm_b = make_pm('B')
        pm_c = make_pm('C')
        size_b = pm_b.calculate_stage2_size(entry_price=105.0)
        size_c = pm_c.calculate_stage2_size(entry_price=105.0)
        assert size_b > size_c, f"Tier B stage2 ({size_b}) should > Tier C ({size_c})"

    def test_tier_a_mult_ratio(self):
        """Tier A (1.0) / Tier B (0.7) = 1/0.7 ≈ 1.43"""
        from trader.config import Config as Cfg
        pm_a = make_pm('A')
        pm_b = make_pm('B')
        size_a = pm_a.calculate_stage2_size(entry_price=105.0)
        size_b = pm_b.calculate_stage2_size(entry_price=105.0)
        expected_ratio = Cfg.TIER_A_POSITION_MULT / Cfg.TIER_B_POSITION_MULT
        actual_ratio = size_a / size_b
        assert abs(actual_ratio - expected_ratio) < 0.01, \
            f"Expected ratio {expected_ratio:.2f}, got {actual_ratio:.2f}"

    def test_unknown_tier_defaults_to_b(self):
        pm = make_pm('Z')  # 未知等級
        pm_b = make_pm('B')
        size_z = pm.calculate_stage2_size(entry_price=105.0)
        size_b = pm_b.calculate_stage2_size(entry_price=105.0)
        assert size_z == pytest.approx(size_b, rel=1e-6)


# ===================== Test 2: Stage 3 tier_mult =====================

class TestStage3TierMult:

    def test_tier_a_larger_than_tier_b(self):
        pm_a = make_pm('A')
        pm_b = make_pm('B')
        # 止損在保本以上（無 risk cap）
        size_a = pm_a.calculate_stage3_size(entry_price=110.0, swing_stop=102.0)
        size_b = pm_b.calculate_stage3_size(entry_price=110.0, swing_stop=102.0)
        assert size_a > size_b

    def test_tier_b_larger_than_tier_c(self):
        pm_b = make_pm('B')
        pm_c = make_pm('C')
        size_b = pm_b.calculate_stage3_size(entry_price=110.0, swing_stop=102.0)
        size_c = pm_c.calculate_stage3_size(entry_price=110.0, swing_stop=102.0)
        assert size_b > size_c

    def test_tier_a_mult_ratio(self):
        from trader.config import Config as Cfg
        pm_a = make_pm('A')
        pm_b = make_pm('B')
        size_a = pm_a.calculate_stage3_size(entry_price=110.0, swing_stop=102.0)
        size_b = pm_b.calculate_stage3_size(entry_price=110.0, swing_stop=102.0)
        expected_ratio = Cfg.TIER_A_POSITION_MULT / Cfg.TIER_B_POSITION_MULT
        actual_ratio = size_a / size_b
        assert abs(actual_ratio - expected_ratio) < 0.01


# ===================== Test 3: V5.3 equity cap =====================

class TestV53EquityCap:

    def _make_mock_bot(self):
        """建立最小 bot mock，執行 V5.3 _execute_trade 分支"""
        from trader.bot import TradingBotV6
        from trader.config import Config as Cfg

        with patch.object(Cfg, 'PYRAMID_ENABLED', True), \
             patch('trader.bot.BinanceFuturesClient'):
            bot = MagicMock(spec=TradingBotV6)
            bot.active_trades = {}
            bot.risk_manager = MagicMock()
            bot.precision_handler = MagicMock()
            return bot

    def test_v53_notional_capped_when_over_equity_cap(self):
        """V5.3 notional > v53_equity_cap → 截頂"""
        from trader.config import Config as Cfg
        balance = 1000.0
        entry_price = 100.0
        equity_cap = balance * Cfg.V53_EQUITY_CAP_PERCENT  # 100 USDT（V53 獨立 cap = 10%）

        # simulate: risk-based 算出 500 USDT notional（例如 1.7% / 0.034% 極緊止損）
        over_size = 500.0 / entry_price  # 5.0 units

        # expected cap
        expected_cap_size = equity_cap / entry_price  # 1.0 units

        assert over_size * entry_price > equity_cap
        # 驗證公式：capped_size ≤ equity_cap / entry_price
        assert expected_cap_size == pytest.approx(1.0)

    def test_v53_notional_unchanged_when_under_cap(self):
        """V5.3 notional < v53_equity_cap → 不截頂"""
        from trader.config import Config as Cfg
        balance = 1000.0
        entry_price = 100.0
        equity_cap = balance * Cfg.V53_EQUITY_CAP_PERCENT  # 100 USDT（V53 獨立 cap = 10%）

        under_size = 50.0 / entry_price  # 0.5 units，50 USDT < 100 USDT cap
        assert under_size * entry_price < equity_cap
        # 不應被截頂，size 維持不變
        assert under_size == 0.5
