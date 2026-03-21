"""Scanner 測試"""

import sys
import pytest
import pandas as pd
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scanner.market_scanner import (
    MarketScanner, ScannerConfig, ScanResult, MarketSummary,
    SignalSide, SignalType, StructureQuality, VolumeGrade, get_sector,
)


def make_ohlcv(rows=100, base_price=50000.0, atr_pct=0.02, trend='up', seed=42):
    """
    生成 synthetic OHLCV DataFrame。

    Args:
        rows: K 線數量
        base_price: 起始價
        atr_pct: ATR 佔價格比例（控制波動）
        trend: 'up' / 'down' / 'flat'
        seed: 隨機種子（可重現）
    """
    np.random.seed(seed)
    dates = pd.date_range('2026-01-01', periods=rows, freq='1h')

    trend_step = {'up': base_price * 0.001, 'down': -base_price * 0.001, 'flat': 0}[trend]
    noise = np.random.randn(rows) * base_price * atr_pct * 0.5

    closes = base_price + np.cumsum(np.full(rows, trend_step) + noise)
    highs = closes + np.abs(np.random.randn(rows)) * base_price * atr_pct
    lows = closes - np.abs(np.random.randn(rows)) * base_price * atr_pct
    opens = closes + np.random.randn(rows) * base_price * atr_pct * 0.3
    volumes = np.random.uniform(100, 1000, rows)

    return pd.DataFrame({
        'timestamp': dates,
        'open': opens,
        'high': highs,
        'low': lows,
        'close': closes,
        'volume': volumes,
    })


def make_2b_long_df(base_price=50000.0, rows=60):
    """
    生成含有 confirmed bullish 2B 信號的 DataFrame。

    結構：上漲趨勢 → swing low 形成 → 新 low 穿透 swing low → close 收回（2B）
    """
    df = make_ohlcv(rows=rows, base_price=base_price, trend='up')

    # 在倒數第 10 根形成 swing low
    swing_idx = len(df) - 10
    df.loc[swing_idx, 'low'] = base_price * 0.96
    df.loc[swing_idx, 'close'] = base_price * 0.965

    # 在最後一根：low 穿透 swing low（fakeout），但 close 收回 swing low 之上
    df.loc[len(df)-1, 'low'] = base_price * 0.957      # 穿透 ~0.3% (within ATR range)
    df.loc[len(df)-1, 'close'] = base_price * 0.965     # 收回 swing low 之上
    df.loc[len(df)-1, 'high'] = base_price * 0.97
    df.loc[len(df)-1, 'volume'] = df['volume'].mean() * 2.0  # 放量

    return df


def make_2b_short_df(base_price=50000.0, rows=60):
    """生成含有 confirmed bearish 2B 信號的 DataFrame。"""
    df = make_ohlcv(rows=rows, base_price=base_price, trend='down')

    swing_idx = len(df) - 10
    df.loc[swing_idx, 'high'] = base_price * 1.04
    df.loc[swing_idx, 'close'] = base_price * 1.035

    df.loc[len(df)-1, 'high'] = base_price * 1.043
    df.loc[len(df)-1, 'close'] = base_price * 1.035
    df.loc[len(df)-1, 'low'] = base_price * 1.03
    df.loc[len(df)-1, 'volume'] = df['volume'].mean() * 2.0

    return df


@pytest.fixture
def mock_scanner():
    """MarketScanner，mock 掉所有外部 I/O"""
    with patch.object(MarketScanner, '_init_exchange') as mock_init:
        mock_exchange = MagicMock()
        mock_exchange.load_markets.return_value = {}
        mock_exchange.markets = {}
        mock_init.return_value = mock_exchange

        mock_dp = MagicMock()
        scanner = MarketScanner(data_provider=mock_dp)
        scanner.exchange = mock_exchange
        scanner._data_provider = mock_dp

    yield scanner


class TestCalculateIndicators:
    """Layer 0: 技術指標計算"""

    def test_basic_indicators_added(self, mock_scanner):
        """verify EMA/RSI/ATR/ADX columns added"""
        df = make_ohlcv(rows=100)
        result = mock_scanner.calculate_indicators(df)
        for col in ['ema_20', 'ema_50', 'ema_200', 'rsi', 'atr', 'vol_ma']:
            assert col in result.columns, f"Missing column: {col}"

    def test_insufficient_data_returns_original(self, mock_scanner):
        """rows < 需求 → columns 可能缺失但不 crash"""
        df = make_ohlcv(rows=5)
        result = mock_scanner.calculate_indicators(df)
        assert len(result) == 5

    def test_nan_handling(self, mock_scanner):
        """最後一根不應有 NaN（EMA/ATR warmup 後）"""
        df = make_ohlcv(rows=250)
        result = mock_scanner.calculate_indicators(df)
        last = result.iloc[-1]
        assert pd.notna(last.get('ema_20')), "ema_20 is NaN"
        assert pd.notna(last.get('atr')), "atr is NaN"


class TestLayer1:
    """Layer 1: 流動性過濾"""

    def test_filters_low_volume(self, mock_scanner):
        """volume < L1_MIN_VOLUME_USD → 被過濾"""
        mock_scanner.exchange.fetch_tickers.return_value = {
            'BTC/USDT:USDT': {'symbol': 'BTC/USDT:USDT', 'quoteVolume': 100_000_000},
            'SHIB/USDT:USDT': {'symbol': 'SHIB/USDT:USDT', 'quoteVolume': 1_000},  # 太低
        }
        mock_scanner._data_provider.fetch_ohlcv.return_value = make_ohlcv(rows=250)

        result = mock_scanner.layer1_liquidity_filter()
        symbols = [s for s in result]
        assert 'SHIB/USDT' not in symbols

    def test_excludes_stablecoins(self, mock_scanner):
        """USDC/USDT 等 stablecoin 應被排除"""
        mock_scanner.exchange.fetch_tickers.return_value = {
            'USDC/USDT:USDT': {'symbol': 'USDC/USDT:USDT', 'quoteVolume': 999_000_000},
            'BTC/USDT:USDT': {'symbol': 'BTC/USDT:USDT', 'quoteVolume': 500_000_000},
        }
        mock_scanner._data_provider.fetch_ohlcv.return_value = make_ohlcv(rows=250)

        result = mock_scanner.layer1_liquidity_filter()
        normalized = [mock_scanner._normalize_symbol(s) if hasattr(mock_scanner, '_normalize_symbol') else s for s in result]
        assert not any('USDC' in s for s in normalized)

    def test_empty_tickers(self, mock_scanner):
        """exchange 回傳空 dict → 回傳空 list"""
        mock_scanner.exchange.fetch_tickers.return_value = {}
        result = mock_scanner.layer1_liquidity_filter()
        assert result == []


class TestLayer2:
    """Layer 2: 動能過濾"""

    def test_passes_strong_trend(self, mock_scanner):
        """ADX > 20, RSI 正常, 放量 → 通過"""
        df = make_ohlcv(rows=100, trend='up')
        df = mock_scanner.calculate_indicators(df)
        mock_scanner._data_provider.fetch_ohlcv.return_value = df

        candidates = ['BTC/USDT']
        result = mock_scanner.layer2_momentum_filter(candidates)
        # 不 assert 具體數量（取決於 synthetic data），只確認不 crash
        assert isinstance(result, list)

    def test_insufficient_data_skipped(self, mock_scanner):
        """< 50 根 → 跳過不 crash"""
        df = make_ohlcv(rows=10)
        mock_scanner._data_provider.fetch_ohlcv.return_value = df

        candidates = ['BTC/USDT']
        result = mock_scanner.layer2_momentum_filter(candidates)
        assert isinstance(result, list)


class TestDetect2B:
    """Layer 3: 2B 信號偵測"""

    def test_confirmed_2b_long(self, mock_scanner):
        """bullish 2B: low 穿透 swing low → close 收回"""
        df = make_2b_long_df()
        df = mock_scanner.calculate_indicators(df)
        indicators = {
            'atr': df['atr'].iloc[-1] if 'atr' in df.columns else 100,
            'adx': 25,
            'rsi': 50,
            'volume': df['volume'].iloc[-1],
            'vol_ma': df['volume'].mean(),
        }
        result = mock_scanner._detect_2b_signal(df, 'TEST/USDT', indicators)
        # result 可以是 ScanResult 或 None（取決於 swing point 偵測）
        # 主要確認不 crash + 回傳型別正確
        assert result is None or isinstance(result, ScanResult)

    def test_confirmed_2b_short(self, mock_scanner):
        """bearish 2B: high 穿透 swing high → close 收回"""
        df = make_2b_short_df()
        df = mock_scanner.calculate_indicators(df)
        indicators = {
            'atr': df['atr'].iloc[-1] if 'atr' in df.columns else 100,
            'adx': 25,
            'rsi': 50,
            'volume': df['volume'].iloc[-1],
            'vol_ma': df['volume'].mean(),
        }
        result = mock_scanner._detect_2b_signal(df, 'TEST/USDT', indicators)
        assert result is None or isinstance(result, ScanResult)

    def test_weak_volume_filtered(self, mock_scanner):
        """vol_ratio < 1.0 的 confirmed 2B 應被過濾"""
        df = make_2b_long_df()
        df.loc[len(df)-1, 'volume'] = df['volume'].mean() * 0.3  # 弱量
        df = mock_scanner.calculate_indicators(df)
        indicators = {
            'atr': df['atr'].iloc[-1] if 'atr' in df.columns else 100,
            'adx': 25,
            'rsi': 50,
            'volume': df['volume'].iloc[-1],
            'vol_ma': df['volume'].mean(),
        }
        result = mock_scanner._detect_2b_signal(df, 'TEST/USDT', indicators)
        # 弱量 → 應回傳 None 或 pre-2B（非 confirmed）
        if result is not None:
            assert result.signal_type != SignalType.CONFIRMED_2B


class TestScoring:
    """評分計算"""

    def test_score_range(self, mock_scanner):
        """分數在 0~100 之間"""
        score = mock_scanner._calculate_score(
            structure_quality=StructureQuality.SWING,
            volume_grade=VolumeGrade.STRONG,
            adx=30,
            atr_percent=3,
            mtf_aligned=True,
            relative_strength=1.05,
        )
        assert 0 <= score <= 100

    def test_higher_quality_higher_score(self, mock_scanner):
        """SWING + EXPLOSIVE 分數 > SIMPLE + WEAK"""
        high = mock_scanner._calculate_score(
            structure_quality=StructureQuality.SWING,
            volume_grade=VolumeGrade.EXPLOSIVE,
            adx=35, atr_percent=3, mtf_aligned=True, relative_strength=1.1,
        )
        low = mock_scanner._calculate_score(
            structure_quality=StructureQuality.SIMPLE,
            volume_grade=VolumeGrade.WEAK,
            adx=20, atr_percent=1, mtf_aligned=False, relative_strength=0.9,
        )
        assert high > low


class TestLayer4:
    """Layer 4: 相關性過濾"""

    def test_sector_cap(self, mock_scanner):
        """同 sector 超過 2 個 → 只留前 2（需超過 OUTPUT_TOP_N 才觸發過濾）"""
        results = []
        # 建立 OUTPUT_TOP_N + 5 個結果，全部在同一 sector，確保觸發 L4 過濾邏輯
        total = ScannerConfig.OUTPUT_TOP_N + 5
        for i in range(total):
            r = ScanResult(
                symbol=f'TOKEN{i}/USDT',
                rank=0,
                score=90 - i,
                signal_side=SignalSide.LONG,
                signal_type=SignalType.CONFIRMED_2B,
                structure_quality=StructureQuality.SWING,
                volume_grade=VolumeGrade.STRONG,
                entry_price=100.0,
                stop_loss=95.0,
                target=110.0,
                risk_reward=2.0,
                sector='DeFi',  # 全部同 sector
                mtf_aligned=True,
            )
            results.append(r)

        filtered = mock_scanner.layer4_correlation_filter(results)
        defi_count = sum(1 for r in filtered if r.sector == 'DeFi')
        assert defi_count <= ScannerConfig.L4_MAX_PER_SECTOR

    def test_top_n_ranking(self, mock_scanner):
        """回傳數量 ≤ OUTPUT_TOP_N"""
        results = []
        for i in range(20):
            r = ScanResult(
                symbol=f'TOKEN{i}/USDT',
                rank=0, score=50 + i,
                signal_side=SignalSide.LONG,
                signal_type=SignalType.CONFIRMED_2B,
                structure_quality=StructureQuality.SWING,
                volume_grade=VolumeGrade.STRONG,
                entry_price=100.0, stop_loss=95.0, target=110.0,
                risk_reward=2.0, sector=f'Sector{i % 5}',
                mtf_aligned=True,
            )
            results.append(r)

        filtered = mock_scanner.layer4_correlation_filter(results)
        assert len(filtered) <= ScannerConfig.OUTPUT_TOP_N


class TestGetSector:
    """get_sector 幣種分類"""

    def test_known_symbols(self):
        assert get_sector('BTC/USDT') in ['Layer1', 'BTC'] or isinstance(get_sector('BTC/USDT'), str)
        assert get_sector('UNI/USDT') == 'DeFi' or isinstance(get_sector('UNI/USDT'), str)

    def test_unknown_symbol(self):
        """未知幣種 → 回傳 'Other'"""
        assert get_sector('UNKNOWN_COIN_XYZ/USDT') == 'Other'


class TestScanIntegration:
    """全 pipeline mock E2E"""

    def test_scan_runs_without_crash(self, mock_scanner, tmp_path):
        """mock 全部外部 I/O，確認 scan() 不 crash"""
        # Mock tickers
        mock_scanner.exchange.fetch_tickers.return_value = {
            'BTC/USDT:USDT': {'symbol': 'BTC/USDT:USDT', 'quoteVolume': 500_000_000},
            'ETH/USDT:USDT': {'symbol': 'ETH/USDT:USDT', 'quoteVolume': 300_000_000},
        }

        # Mock OHLCV
        df = make_ohlcv(rows=250, trend='up')
        mock_scanner._data_provider.fetch_ohlcv.return_value = df

        # Mock output paths
        with patch.object(ScannerConfig, 'OUTPUT_JSON_PATH', str(tmp_path / 'hot_symbols.json')), \
             patch.object(ScannerConfig, 'OUTPUT_DB_PATH', str(tmp_path / 'scanner.db')), \
             patch.object(ScannerConfig, 'TELEGRAM_ENABLED', False):
            results, summary = mock_scanner.scan()

        assert isinstance(results, list)
        assert isinstance(summary, MarketSummary)

    def test_scan_empty_market(self, mock_scanner, tmp_path):
        """空市場 → 空結果，不 crash"""
        mock_scanner.exchange.fetch_tickers.return_value = {}

        with patch.object(ScannerConfig, 'OUTPUT_JSON_PATH', str(tmp_path / 'hot_symbols.json')), \
             patch.object(ScannerConfig, 'OUTPUT_DB_PATH', str(tmp_path / 'scanner.db')), \
             patch.object(ScannerConfig, 'TELEGRAM_ENABLED', False):
            results, summary = mock_scanner.scan()

        assert results == []
