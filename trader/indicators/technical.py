"""
技術指標層

包含純數學計算函數（_ema, _sma, _atr, _adx）與所有技術分析類別：
- TechnicalAnalysis：指標計算、趨勢判斷、信號偵測
- DynamicThresholdManager：根據市場狀態動態調整 ADX/ATR 閾值
- MTFConfirmation：多時間框架確認
- MarketFilter：市場狀態過濾

從 v6/core.py 提取，業務邏輯不變。
"""

import logging
import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple

try:
    import pandas_ta as ta
except ImportError:
    ta = None

from trader.config import Config

logger = logging.getLogger(__name__)


# ==================== pandas_ta 備用實現 ====================

def _ema(series: pd.Series, length: int) -> pd.Series:
    if ta is not None:
        return ta.ema(series, length=length)
    return series.ewm(span=length, adjust=False).mean()


def _sma(series: pd.Series, length: int) -> pd.Series:
    if ta is not None:
        return ta.sma(series, length=length)
    return series.rolling(window=length).mean()


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, length: int) -> pd.Series:
    if ta is not None:
        return ta.atr(high, low, close, length=length)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(window=length).mean()


def _adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int):
    if ta is not None:
        return ta.adx(high, low, close, length=length)
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs()
    ], axis=1).max(axis=1)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)
    atr_val = tr.rolling(window=length).mean().replace(0, np.nan)
    plus_di = 100 * (plus_dm.rolling(window=length).mean() / atr_val)
    minus_di = 100 * (minus_dm.rolling(window=length).mean() / atr_val)
    dx = (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan) * 100
    adx_val = dx.rolling(window=length).mean()
    result = pd.DataFrame({
        f'ADX_{length}': adx_val,
        f'DMP_{length}': plus_di,
        f'DMN_{length}': minus_di
    })
    return result


# ==================== 技術分析 ====================

class TechnicalAnalysis:
    """技術分析工具類"""

    @staticmethod
    def extract_adx_series(df: pd.DataFrame, length: int = 14) -> Optional[pd.Series]:
        """安全提取 ADX Series"""
        adx_data = _adx(df['high'], df['low'], df['close'], length=length)
        if adx_data is None or adx_data.empty:
            return None
        if isinstance(adx_data, pd.DataFrame):
            adx_cols = [c for c in adx_data.columns if c.startswith('ADX')]
            return adx_data[adx_cols[0]] if adx_cols else None
        return adx_data

    @staticmethod
    def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """計算所有必要的技術指標"""
        if df.empty or len(df) < 50:
            return df

        required_columns = ['open', 'high', 'low', 'close', 'volume']
        missing = [col for col in required_columns if col not in df.columns]
        if missing:
            logger.error(f"DataFrame 缺少必要欄位: {missing}")
            return df

        ema_period = getattr(Config, 'EMA_TREND', 200)
        df['ema_trend'] = _ema(df['close'], length=ema_period)
        df['vol_ma'] = _sma(df['volume'], length=Config.VOLUME_MA_PERIOD)
        df['atr'] = _atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)

        df['ema_fast'] = _ema(df['close'], length=Config.EMA_PULLBACK_FAST)
        df['ema_slow'] = _ema(df['close'], length=Config.EMA_PULLBACK_SLOW)

        adx_series = TechnicalAnalysis.extract_adx_series(df)
        if adx_series is not None:
            df['adx'] = adx_series

        return df

    @staticmethod
    def check_trend(df: pd.DataFrame, side: str) -> Tuple[bool, str]:
        """檢查趨勢（雙向版本）"""
        ema_period = getattr(Config, 'EMA_TREND', 200)

        if len(df) < ema_period:
            return False, "數據不足"

        latest = df.iloc[-1]
        if 'ema_trend' not in latest or pd.isna(latest['ema_trend']):
            return False, "EMA 計算失敗"

        if side == 'LONG':
            if latest['close'] > latest['ema_trend']:
                return True, "多頭趨勢"
            else:
                return False, "空頭趨勢"
        else:
            if latest['close'] < latest['ema_trend']:
                return True, "空頭趨勢"
            else:
                return False, "多頭趨勢"

    @staticmethod
    def check_structure_break(df: pd.DataFrame, current_price: float, side: str) -> bool:
        """檢查結構是否破壞（雙向版本）"""
        lookback = Config.STRUCTURE_BREAK_LOOKBACK
        if not Config.ENABLE_STRUCTURE_BREAK_EXIT or len(df) < lookback:
            return False

        tol = Config.STRUCTURE_BREAK_TOLERANCE
        if side == 'LONG':
            swing_low = df['low'].iloc[-lookback:-1].min()
            return current_price < swing_low * (1 - tol)
        else:
            swing_high = df['high'].iloc[-lookback:-1].max()
            return current_price > swing_high * (1 + tol)


# ==================== 動態閾值管理器 ====================

class DynamicThresholdManager:
    """動態閾值管理器"""

    @staticmethod
    def get_adx_threshold(df: pd.DataFrame) -> float:
        """根據近期市場狀態動態調整 ADX 閾值"""
        if not Config.ENABLE_DYNAMIC_THRESHOLDS:
            return Config.ADX_THRESHOLD

        adx_series = TechnicalAnalysis.extract_adx_series(df)
        if adx_series is None:
            return Config.ADX_THRESHOLD
        adx_series = adx_series.dropna()

        if len(adx_series) < 20:
            return Config.ADX_THRESHOLD

        avg_adx = adx_series.iloc[-20:].mean()

        if avg_adx < 20:
            return Config.ADX_BASE_THRESHOLD
        elif avg_adx > 30:
            return Config.ADX_STRONG_THRESHOLD
        else:
            return Config.ADX_THRESHOLD

    @staticmethod
    def get_atr_multiplier(df: pd.DataFrame) -> float:
        """根據近期波動率動態調整 ATR 乘數"""
        if not Config.ENABLE_DYNAMIC_THRESHOLDS:
            return Config.ATR_MULTIPLIER

        if 'atr' not in df.columns or len(df) < 20:
            return Config.ATR_MULTIPLIER

        recent_atr = df['atr'].iloc[-5:].mean()
        historical_atr = df['atr'].iloc[-20:-5].mean()

        if historical_atr == 0:
            return Config.ATR_MULTIPLIER

        atr_ratio = recent_atr / historical_atr

        if atr_ratio < Config.ATR_QUIET_RATIO:
            return Config.ATR_QUIET_MULTIPLIER
        elif atr_ratio > Config.ATR_VOLATILE_RATIO:
            return Config.ATR_VOLATILE_MULTIPLIER
        else:
            return Config.ATR_NORMAL_MULTIPLIER


# ==================== 多時間框架確認器 ====================

class MTFConfirmation:
    """多時間框架確認系統"""

    @staticmethod
    def check_mtf_alignment(df_mtf: pd.DataFrame, side: str) -> Tuple[bool, str]:
        """檢查中間時間框架（4H）是否與交易方向一致"""
        if not Config.ENABLE_MTF_CONFIRMATION or df_mtf.empty:
            return True, "MTF 確認已關閉"

        if len(df_mtf) < Config.MTF_EMA_SLOW:
            return True, "MTF 數據不足"

        ema_fast = _ema(df_mtf['close'], length=Config.MTF_EMA_FAST)
        ema_slow = _ema(df_mtf['close'], length=Config.MTF_EMA_SLOW)

        if ema_fast is None or ema_slow is None:
            return True, "MTF 指標計算失敗"

        current_fast = ema_fast.iloc[-1]
        current_slow = ema_slow.iloc[-1]
        current_price = df_mtf['close'].iloc[-1]

        if side == 'LONG':
            aligned = current_price > current_fast and current_fast > current_slow
            if aligned:
                return True, "MTF 多頭排列確認 ✅"
            else:
                return False, "MTF 未完全多頭排列"
        else:
            aligned = current_price < current_fast and current_fast < current_slow
            if aligned:
                return True, "MTF 空頭排列確認 ✅"
            else:
                return False, "MTF 未完全空頭排列"


# ==================== 市場過濾器 ====================

class MarketFilter:
    """市場狀態過濾器"""

    @staticmethod
    def check_market_condition(df_trend: pd.DataFrame, symbol: str) -> Tuple[bool, str, bool]:
        """
        檢查市場是否適合交易
        返回: (是否可交易, 原因, 是否強勢市場)
        """
        if not Config.ENABLE_MARKET_FILTER:
            return True, "過濾器已關閉", True

        min_data_required = max(50, Config.EMA_TREND)
        if len(df_trend) < min_data_required:
            return False, f"數據不足（需要至少 {min_data_required} 根）", False

        dynamic_adx_threshold = DynamicThresholdManager.get_adx_threshold(df_trend)

        adx_series = TechnicalAnalysis.extract_adx_series(df_trend)
        if adx_series is None:
            logger.warning(f"{symbol} ADX 計算失敗")
            return False, "ADX 計算失敗", False
        current_adx = adx_series.iloc[-1]

        is_strong_market = current_adx >= Config.ADX_STRONG_THRESHOLD

        if current_adx < dynamic_adx_threshold:
            return False, f"趨勢不足 (ADX={current_adx:.1f}, 閾值={dynamic_adx_threshold:.1f})", False

        if 'atr' in df_trend.columns:
            current_atr = df_trend['atr'].iloc[-1]
            lookback = min(10, len(df_trend) - 1)
            avg_atr = df_trend['atr'].iloc[-lookback-1:-1].mean()

            if pd.notna(avg_atr) and avg_atr > 0:
                if current_atr > avg_atr * Config.ATR_SPIKE_MULTIPLIER:
                    return False, f"波動過大 (ATR={current_atr/avg_atr:.1f}x)", False

        ema_10 = _ema(df_trend['close'], length=10)
        ema_20 = _ema(df_trend['close'], length=20)

        if ema_10 is not None and ema_20 is not None and len(ema_10) > 0 and len(ema_20) > 0:
            if pd.notna(ema_10.iloc[-1]) and pd.notna(ema_20.iloc[-1]) and ema_20.iloc[-1] != 0:
                ema_diff = abs(ema_10.iloc[-1] - ema_20.iloc[-1]) / ema_20.iloc[-1]

                if ema_diff < Config.EMA_ENTANGLEMENT_THRESHOLD:
                    return False, f"均線糾纏 (差距={ema_diff*100:.1f}%)", False

        logger.debug(f"✅ {symbol} 市場狀態良好 (ADX={current_adx:.1f}, 動態閾值={dynamic_adx_threshold:.1f})")
        return True, "市場狀態良好", is_strong_market
