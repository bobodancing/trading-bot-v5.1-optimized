import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from trader.config import Config


def _make_btc_df(ema20_above_ema50: bool, rows: int = 60) -> pd.DataFrame:
    closes = (
        np.linspace(80000.0, 90000.0, rows)
        if ema20_above_ema50
        else np.linspace(90000.0, 80000.0, rows)
    )
    timestamps = pd.date_range('2026-04-01', periods=rows, freq='1D')
    return pd.DataFrame({
        'timestamp': timestamps,
        'open': closes,
        'high': closes * 1.01,
        'low': closes * 0.99,
        'close': closes,
        'volume': np.full(rows, 1000.0),
    }, index=timestamps)


class TestBtcTrendRouting:
    def test_regime_fetch_empty_falls_back_to_daily(self, mock_bot, caplog):
        with patch.object(mock_bot.data_provider, 'fetch_ohlcv', side_effect=[pd.DataFrame(), _make_btc_df(False)]), \
             patch.object(Config, 'ENABLE_GRID_TRADING', True):
            caplog.set_level('INFO')
            regime_context = mock_bot._update_btc_regime_context()
            resolved = mock_bot._resolve_btc_trend_context(log_event=True)

        assert regime_context['reason'] == 'regime_fetch_empty'
        assert resolved['trend'] == 'SHORT'
        assert resolved['source'] == '1d_fallback'
        assert 'source=1d_fallback' in caplog.text

    def test_ambiguous_regime_keeps_4h_direction(self, mock_bot):
        mock_bot._btc_regime_context = mock_bot._make_btc_context(
            source='regime',
            regime='TRENDING',
            detected=None,
            direction='SHORT',
            candle_time=pd.Timestamp('2026-04-03 16:00:00'),
            reason='regime_updated',
        )

        with patch.object(Config, 'ENABLE_GRID_TRADING', True):
            resolved = mock_bot._resolve_btc_trend_context()

        assert resolved['trend'] == 'SHORT'
        assert resolved['source'] == 'regime'
        assert resolved['reason'] == 'ambiguous_regime_keep_direction'

    def test_both_paths_missing_stay_unknown(self, mock_bot, caplog):
        with patch.object(mock_bot.data_provider, 'fetch_ohlcv', side_effect=[pd.DataFrame(), pd.DataFrame()]), \
             patch.object(Config, 'ENABLE_GRID_TRADING', True):
            caplog.set_level('INFO')
            mock_bot._update_btc_regime_context()
            resolved = mock_bot._resolve_btc_trend_context(log_event=True)

        assert resolved['trend'] is None
        assert resolved['source'] == 'none'
        assert 'source=none' in caplog.text

    def test_ranging_regime_does_not_fallback(self, mock_bot):
        mock_bot._btc_regime_context = mock_bot._make_btc_context(
            source='regime',
            regime='RANGING',
            detected='RANGING',
            direction=None,
            candle_time=pd.Timestamp('2026-04-03 16:00:00'),
            reason='regime_updated',
        )

        with patch.object(Config, 'ENABLE_GRID_TRADING', True), \
             patch.object(mock_bot, '_get_daily_btc_trend_context') as daily_fallback:
            resolved = mock_bot._resolve_btc_trend_context()

        daily_fallback.assert_not_called()
        assert resolved['trend'] == 'RANGING'
        assert resolved['source'] == 'regime'
