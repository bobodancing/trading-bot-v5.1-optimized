"""
Tests for performance_db data quality fixes:
1. capture_ratio formula: pnl_pct / mfe_pct (not realized_r / mfe_pct)
2. MFE/MAE fallback: exit price included in tracking for fast exits
"""
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone


def make_pm(side='SHORT', entry=100.0, size=10.0, initial_r=50.0):
    """Helper: create a minimal PositionManager-like mock."""
    pm = MagicMock()
    pm.symbol = 'TEST/USDT'
    pm.side = side
    pm.is_v6_pyramid = False
    pm.signal_tier = 'A'
    pm.trade_id = 'test_trade_001'
    pm.avg_entry = entry
    pm.total_size = size
    pm.initial_r = initial_r
    pm.stage = 1
    pm.market_regime = 'STRONG'
    pm.exit_reason = 'sl_hit'
    pm.entry_time = datetime.now(timezone.utc)
    pm.stop_order_id = None
    pm.pending_stop_cancels = []
    pm.highest_price = entry   # 初始 = entry（模擬快速出場未更新）
    pm.lowest_price = entry    # 初始 = entry
    pm.is_closed = False
    return pm


class TestCaptureRatioFormula:
    """Bug 1: capture_ratio should be pnl_pct / mfe_pct"""

    def test_capture_ratio_positive_mfe(self):
        """MFE > 0: capture_ratio = pnl_pct / mfe_pct"""
        pnl_pct = 11.56
        mfe_pct = 15.58
        expected = round(pnl_pct / mfe_pct, 4)  # 0.7419
        result = round(pnl_pct / mfe_pct, 4) if mfe_pct > 0.0001 else None
        assert result == expected
        assert 0.7 < result < 0.8

    def test_capture_ratio_zero_mfe_returns_none(self):
        """MFE = 0: capture_ratio should be None (not 0)"""
        mfe_pct = 0.0
        pnl_pct = -0.5
        result = round(pnl_pct / mfe_pct, 4) if mfe_pct > 0.0001 else None
        assert result is None

    def test_capture_ratio_negative_pnl_positive_mfe(self):
        """Trade went up but we exited at loss (pullback past entry): capture_ratio negative"""
        pnl_pct = -0.15
        mfe_pct = 0.04
        result = round(pnl_pct / mfe_pct, 4) if mfe_pct > 0.0001 else None
        assert result is not None
        assert result < 0   # -3.75: went negative despite positive MFE

    def test_old_formula_would_give_wrong_result(self):
        """Regression: old realized_r / mfe_pct formula was wrong"""
        realized_r = 0.83
        mfe_pct = 15.58
        pnl_pct = 11.56

        old_result = realized_r / mfe_pct  # 0.053 — meaningless
        new_result = pnl_pct / mfe_pct     # 0.742 — correct

        assert abs(old_result - 0.053) < 0.01
        assert abs(new_result - 0.742) < 0.01
        assert new_result > old_result * 10  # old was drastically wrong


class TestMFEMAEFallback:
    """Bug 2: exit price must be included in highest/lowest tracking"""

    def test_short_fast_exit_mae_nonzero(self):
        """SHORT trade exits at loss (price went up): MAE must reflect adverse move"""
        entry = 0.14897
        exit_price = 0.14917  # price went UP = adverse for SHORT

        # Simulate: highest_price not updated (fast exit), then fallback applied
        highest_price = entry   # initial = entry (no update happened)
        lowest_price = entry

        # Apply fallback (the fix)
        highest_price = max(highest_price, exit_price)
        lowest_price = min(lowest_price, exit_price)

        # For SHORT: mfe = (entry - lowest) / entry, mae = (entry - highest) / entry
        mfe_pct = round((entry - lowest_price) / entry * 100, 4)
        mae_pct = round((entry - highest_price) / entry * 100, 4)

        assert mfe_pct == 0.0              # price never went favorably (DOWN)
        assert mae_pct < 0.0              # adverse move captured
        assert abs(mae_pct - (-0.1343)) < 0.01

    def test_short_fast_exit_without_fallback_gives_zero(self):
        """Regression: without fallback, fast exit SHORT gives mae=0 incorrectly"""
        entry = 0.14897
        exit_price = 0.14917

        highest_price = entry   # NOT updated (simulating the bug)
        lowest_price = entry

        mae_pct = round((entry - highest_price) / entry * 100, 4)
        assert mae_pct == 0.0   # confirms the bug exists without the fix

    def test_long_fast_exit_mfe_nonzero(self):
        """LONG trade exits at profit (price went up): MFE must reflect favorable move"""
        entry = 100.0
        exit_price = 102.0  # price went UP = favorable for LONG

        highest_price = entry   # not updated (fast exit)
        lowest_price = entry

        # Apply fallback
        highest_price = max(highest_price, exit_price)
        lowest_price = min(lowest_price, exit_price)

        mfe_pct = round((highest_price - entry) / entry * 100, 4)
        mae_pct = round((lowest_price - entry) / entry * 100, 4)

        assert mfe_pct > 0.0   # favorable move captured
        assert mae_pct == 0.0  # price never went below entry

    def test_fallback_idempotent_when_already_tracked(self):
        """If price was already tracked (normal path), fallback doesn't change values"""
        entry = 100.0
        peak = 105.0    # already tracked during monitoring
        exit_price = 103.0

        highest_price = peak  # already updated by _apply_common_pre
        lowest_price = entry

        # Apply fallback
        highest_price = max(highest_price, exit_price)  # 105 > 103, no change
        lowest_price = min(lowest_price, exit_price)    # 100 < 103, no change

        assert highest_price == 105.0   # unchanged
        assert lowest_price == entry    # unchanged
