# trader/tests/test_bbw.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import pytest
import pandas as pd
import numpy as np
from trader.indicators.technical import _bbw


class TestBBW:
    def _make_series(self, values):
        return pd.Series(values, dtype=float)

    def test_bbw_basic(self):
        """BBW should be positive when price has variance"""
        np.random.seed(42)
        prices = self._make_series(np.cumsum(np.random.randn(50)) + 100)
        result = _bbw(prices, length=20, std_dev=2.0)
        assert not result.dropna().empty
        assert (result.dropna() > 0).all()

    def test_bbw_constant_price(self):
        """BBW should be ~0 for constant price"""
        prices = self._make_series([100.0] * 50)
        result = _bbw(prices, length=20, std_dev=2.0)
        valid = result.dropna()
        assert (valid < 0.001).all()

    def test_bbw_volatile_vs_calm(self):
        """BBW should be larger for more volatile series"""
        calm = self._make_series([100 + 0.1 * np.sin(i) for i in range(50)])
        volatile = self._make_series([100 + 5 * np.sin(i) for i in range(50)])
        bbw_calm = _bbw(calm, length=20).dropna().mean()
        bbw_volatile = _bbw(volatile, length=20).dropna().mean()
        assert bbw_volatile > bbw_calm
