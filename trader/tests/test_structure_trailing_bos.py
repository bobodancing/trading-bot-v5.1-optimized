"""
Structure Trailing BOS 驗證測試

測試 get_validated_trailing_swing 的時序 BOS 邏輯：
- LONG: HL (swing_low > SL) + BOS (close > preceding swing_high)
- SHORT: LH (swing_high < SL) + BOS (close < preceding swing_low)
- Temporal ordering: BOS target 必須在 HL/LH 之前形成
"""

import pytest
import pandas as pd
from trader.structure import StructureAnalysis


def _df(lows, highs, closes):
    """OHLC helper (open=close, simplified for unit test)"""
    n = len(closes)
    return pd.DataFrame({
        'open': list(closes),
        'high': list(highs),
        'low': list(lows),
        'close': list(closes),
        'volume': [1000] * n,
    })


class TestGetValidatedTrailingSwing:
    """get_validated_trailing_swing BOS 時序驗證"""

    # ==================== LONG ====================

    def test_long_bos_confirmed(self):
        """LONG: HL + BOS confirmed -> return swing low price"""
        # left=2, right=1
        # Swing lows: (2, 95), (7, 97) | Swing highs: (4, 108), (9, 110)
        # BOS: close=109 > bos_target=108 (swing high at idx 4, before swing low at idx 7)
        lows =  [100, 99, 95, 99, 100, 102, 101, 97, 101, 103, 102, 104, 105]
        highs = [105, 104, 100, 104, 108, 107, 106, 102, 106, 110, 107, 109, 110]
        close = [103, 102, 97, 102, 106, 105, 104, 100, 104, 109, 105, 107, 109]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'LONG', 90.0, left_bars=2, right_bars=1
        )
        assert result == 97.0

    def test_long_no_bos(self):
        """LONG: HL exists but close < swing high -> None"""
        lows =  [100, 99, 95, 99, 100, 102, 101, 97, 101, 103, 102, 104, 105]
        highs = [105, 104, 100, 104, 108, 107, 106, 102, 106, 110, 107, 109, 110]
        close = [103, 102, 97, 102, 106, 105, 104, 100, 104, 109, 105, 107, 105]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'LONG', 90.0, left_bars=2, right_bars=1
        )
        assert result is None

    def test_long_no_hl(self):
        """LONG: swing low below current SL -> None"""
        lows =  [100, 99, 95, 99, 100, 102, 101, 97, 101, 103, 102, 104, 105]
        highs = [105, 104, 100, 104, 108, 107, 106, 102, 106, 110, 107, 109, 110]
        close = [103, 102, 97, 102, 106, 105, 104, 100, 104, 109, 105, 107, 109]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'LONG', 98.0, left_bars=2, right_bars=1
        )
        assert result is None

    # ==================== SHORT ====================

    def test_short_bos_confirmed(self):
        """SHORT: LH + BOS confirmed -> return swing high price"""
        # Swing highs: (2, 105), (7, 103) | Swing lows: (4, 92), (9, 90)
        # BOS: close=91 < bos_target=92 (swing low at idx 4, before swing high at idx 7)
        lows =  [95, 96, 100, 96, 92, 93, 94, 98, 94, 90, 93, 91, 90]
        highs = [100, 101, 105, 101, 97, 98, 99, 103, 99, 95, 98, 96, 95]
        close = [97, 98, 103, 98, 94, 95, 96, 100, 96, 91, 95, 93, 91]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'SHORT', 110.0, left_bars=2, right_bars=1
        )
        assert result == 103.0

    def test_short_no_bos(self):
        """SHORT: LH exists but close > swing low -> None"""
        lows =  [95, 96, 100, 96, 92, 93, 94, 98, 94, 90, 93, 91, 90]
        highs = [100, 101, 105, 101, 97, 98, 99, 103, 99, 95, 98, 96, 95]
        close = [97, 98, 103, 98, 94, 95, 96, 100, 96, 91, 95, 93, 93]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'SHORT', 110.0, left_bars=2, right_bars=1
        )
        assert result is None

    def test_short_no_lh(self):
        """SHORT: swing high above current SL -> None"""
        lows =  [95, 96, 100, 96, 92, 93, 94, 98, 94, 90, 93, 91, 90]
        highs = [100, 101, 105, 101, 97, 98, 99, 103, 99, 95, 98, 96, 95]
        close = [97, 98, 103, 98, 94, 95, 96, 100, 96, 91, 95, 93, 91]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'SHORT', 102.0, left_bars=2, right_bars=1
        )
        assert result is None

    # ==================== Edge Cases ====================

    def test_insufficient_data(self):
        """data too short -> None"""
        df = pd.DataFrame({
            'open': [100], 'high': [101], 'low': [99],
            'close': [100], 'volume': [1000],
        })
        result = StructureAnalysis.get_validated_trailing_swing(df, 'LONG', 90.0)
        assert result is None

    def test_no_swing_points(self):
        """flat data, no swing points -> None"""
        n = 15
        df = pd.DataFrame({
            'open': [100]*n, 'high': [101]*n, 'low': [99]*n,
            'close': [100]*n, 'volume': [1000]*n,
        })
        result = StructureAnalysis.get_validated_trailing_swing(df, 'LONG', 90.0, 2, 1)
        assert result is None

    def test_temporal_order_enforced(self):
        """BOS target (swing high) must form BEFORE HL (swing low)"""
        # Swing lows: (2, 95), (6, 97) | Swing high: only (7, 110) — AFTER swing low!
        # close=111 > 110, but temporal order wrong -> None
        lows =  [100, 99, 95, 99, 100, 99, 97, 101, 103, 102, 104, 105, 106]
        highs = [103, 103, 100, 103, 103, 103, 102, 110, 107, 106, 108, 109, 112]
        close = [102, 101, 97, 101, 102, 101, 100, 108, 105, 104, 107, 108, 111]

        result = StructureAnalysis.get_validated_trailing_swing(
            _df(lows, highs, close), 'LONG', 90.0, left_bars=2, right_bars=1
        )
        assert result is None
