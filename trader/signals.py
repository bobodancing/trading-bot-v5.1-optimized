"""
V6.0 信號偵測模組

升級版 2B 偵測：用真正的 Swing Point Pivot（左右側確認）取代 V5.3 的 rolling min/max。
回傳含 neckline 的信號詳情，供 PositionManager Stage 2 觸發使用。
"""

import logging
import pandas as pd
from typing import Tuple, Optional, Dict

from trader.config import Config
from trader.structure import StructureAnalysis

logger = logging.getLogger(__name__)


def detect_2b_with_pivots(
    df: pd.DataFrame,
    left_bars: int = 5,
    right_bars: int = 2,
    vol_minimum_threshold: float = 0.7,
    accept_weak_signals: bool = True,
    enable_volume_grading: bool = True,
    vol_explosive_threshold: float = 2.5,
    vol_strong_threshold: float = 1.5,
    vol_moderate_threshold: float = 1.0,
    min_fakeout_atr: float = 0.3,
) -> Tuple[bool, Optional[Dict]]:
    """
    升級版 2B 偵測（V6.0）

    核心改動：
    1. 用 confirmed swing points（左右側驗證）取代 rolling min/max
    2. 回傳 neckline（反向 confirmed swing point）
    3. 保留 V5.3 的量能分級系統

    2B 定義：
    - Bullish 2B: 價格跌破 confirmed swing low 後放量收回
    - Bearish 2B: 價格突破 confirmed swing high 後放量收回

    Args:
        df: 1H OHLCV DataFrame（需含 atr, vol_ma columns）
        left_bars: Swing point 左側 lookback
        right_bars: Swing point 右側確認
        vol_minimum_threshold: 最低量比門檻
        accept_weak_signals: 是否接受弱量信號
        enable_volume_grading: 啟用量能分級
        vol_explosive_threshold: 爆量門檻
        vol_strong_threshold: 強量門檻
        vol_moderate_threshold: 中量門檻

    Returns:
        (has_signal, signal_details)
        signal_details 含 neckline 欄位
    """
    min_bars = left_bars + right_bars + 5  # 至少需要夠多 K 線
    if df is None or len(df) < min_bars:
        return False, None

    # === 1. 找 confirmed swing points ===
    swings = StructureAnalysis.find_swing_points(df, left_bars, right_bars)

    last_swing_low = swings['last_swing_low']
    last_swing_high = swings['last_swing_high']

    # 需要至少一個 swing point 才能偵測 2B
    if last_swing_low is None and last_swing_high is None:
        return False, None

    current = df.iloc[-1]
    close = current['close']
    low = current['low']
    high = current['high']
    atr = current.get('atr', 0)
    volume = current.get('volume', 0)
    vol_ma = current.get('vol_ma', 0)

    signal_side = None
    signal_details = {}

    # === 2. Bullish 2B 偵測 ===
    # 價格跌破 confirmed swing low 後收回（收盤在 swing low 上方）
    if last_swing_low is not None:
        if low < last_swing_low and close > last_swing_low:
            signal_side = 'LONG'

            # Neckline = entry 以上最近的 confirmed swing high（阻力位）
            neckline = StructureAnalysis.find_neckline(df, 'LONG', swings, left_bars, right_bars, entry_price=close)

            signal_details = {
                'side': 'LONG',
                'entry_price': close,
                'lowest_point': low,
                'stop_level': last_swing_low,  # 止損在 swing low
                'target_ref': last_swing_high,  # 目標參考
                'prev_low': last_swing_low,
                'prev_high': last_swing_high,
                'neckline': neckline,  # V6.0 新增
                'atr': atr,
                'volume': volume,
                'vol_ma': vol_ma,
                'signal_time': current.get('timestamp'),
                'candle_confirmed': close > current['open'],
                'detection_method': 'swing_pivot',  # 標記為 V6.0 方法
            }

    # === 3. Bearish 2B 偵測 ===
    # 價格突破 confirmed swing high 後收回（收盤在 swing high 下方）
    if signal_side is None and last_swing_high is not None:
        if high > last_swing_high and close < last_swing_high:
            signal_side = 'SHORT'

            # Neckline = entry 以下最近的 confirmed swing low（支撐位）
            neckline = StructureAnalysis.find_neckline(df, 'SHORT', swings, left_bars, right_bars, entry_price=close)

            signal_details = {
                'side': 'SHORT',
                'entry_price': close,
                'highest_point': high,
                'stop_level': last_swing_high,  # 止損在 swing high
                'target_ref': last_swing_low,  # 目標參考
                'prev_low': last_swing_low,
                'prev_high': last_swing_high,
                'neckline': neckline,  # V6.0 新增
                'atr': atr,
                'volume': volume,
                'vol_ma': vol_ma,
                'signal_time': current.get('timestamp'),
                'candle_confirmed': close < current['open'],
                'detection_method': 'swing_pivot',
            }

    if signal_side is None:
        return False, None

    # === 4. 量能分級（沿用 V5.3）===
    vol_ratio = volume / vol_ma if vol_ma > 0 else 0

    if vol_ratio >= vol_explosive_threshold:
        signal_strength = 'explosive'
    elif vol_ratio >= vol_strong_threshold:
        signal_strength = 'strong'
    elif vol_ratio >= vol_moderate_threshold:
        signal_strength = 'moderate'
    else:
        signal_strength = 'weak'

    signal_details['vol_ratio'] = vol_ratio
    signal_details['signal_strength'] = signal_strength

    # === 5. 量能過濾（沿用 V5.3 邏輯）===
    if enable_volume_grading:
        if vol_ratio < vol_minimum_threshold:
            logger.debug(
                f"2B {signal_side} filtered: vol {vol_ratio:.2f}x < min {vol_minimum_threshold}x"
            )
            return False, None

        if not accept_weak_signals and signal_strength == 'weak':
            logger.debug(
                f"2B {signal_side} filtered: weak signal ({vol_ratio:.2f}x), weak signals disabled"
            )
            return False, None
    else:
        if volume <= vol_ma:
            return False, None

    # === 6. 深度過濾（最小 min_fakeout_atr ATR，最大 3 ATR）===
    if signal_side == 'LONG':
        fakeout_depth = abs(low - last_swing_low)
    else:
        fakeout_depth = abs(high - last_swing_high)

    fakeout_depth_atr = round(fakeout_depth / atr, 3) if atr > 0 else 0.0

    # 下限：穿透太淺視為噪音（非真正流動性獵殺）
    if atr > 0 and fakeout_depth < atr * min_fakeout_atr:
        logger.debug(
            f"2B {signal_side} filtered: penetration too shallow "
            f"({fakeout_depth_atr:.2f}x ATR < {min_fakeout_atr}x ATR)"
        )
        return False, None

    # 上限：穿透太深視為無效
    if atr > 0 and fakeout_depth > atr * Config.MAX_FAKEOUT_ATR:
        logger.debug(
            f"2B {signal_side} filtered: fakeout too deep "
            f"({fakeout_depth:.2f} > {atr * Config.MAX_FAKEOUT_ATR:.2f})"
        )
        return False, None

    signal_details['fakeout_depth_atr'] = fakeout_depth_atr

    # === 7. 計算止損距離（用 swing point + ATR buffer）===
    if signal_side == 'LONG':
        # 止損 = swing low - 0.5 * ATR（給緩衝）
        sl_buffer = atr * Config.SL_ATR_BUFFER_SIGNAL if atr > 0 else 0
        signal_details['stop_loss'] = last_swing_low - sl_buffer
    else:
        sl_buffer = atr * Config.SL_ATR_BUFFER_SIGNAL if atr > 0 else 0
        signal_details['stop_loss'] = last_swing_high + sl_buffer

    neck_str = f"${signal_details['neckline']:.2f}" if signal_details['neckline'] else 'N/A'
    swing_type = 'low' if signal_side == 'LONG' else 'high'
    logger.info(
        f"[V6] 2B {signal_side} detected: "
        f"price=${close:.2f} | swing_{swing_type}="
        f"${signal_details['stop_level']:.2f} | "
        f"neckline={neck_str} | "
        f"vol={vol_ratio:.2f}x ({signal_strength})"
    )

    return True, signal_details


def detect_ema_pullback(
    df: pd.DataFrame,
    ema_pullback_threshold: float = 0.02,
) -> Tuple[bool, Optional[Dict]]:
    """
    EMA 回撤信號偵測

    邏輯：
    - 多頭趨勢（ema_fast > ema_slow）中，價格回撤觸及 ema_fast 後反彈 → LONG
    - 空頭趨勢（ema_fast < ema_slow）中，價格反彈觸及 ema_fast 後回落 → SHORT

    注意：signal_details 必須包含 lowest_point / highest_point（raw 價格），
    因為 _execute_trade V5.3 路徑會用它當 extreme，再由 risk_manager 計算止損。
    不要預先加 ATR buffer，否則會雙重 buffer。

    Returns:
        (has_signal, signal_details)
    """
    if df is None or len(df) < 30:
        return False, None

    if 'ema_fast' not in df.columns or 'ema_slow' not in df.columns:
        return False, None

    current = df.iloc[-1]
    prev = df.iloc[-2]

    ema_fast = current['ema_fast']
    ema_slow = current['ema_slow']
    price = current['close']
    atr = current.get('atr', 0)
    volume = current.get('volume', 0)
    vol_ma = current.get('vol_ma', 0)

    threshold = ema_fast * ema_pullback_threshold

    signal_side = None
    signal_details = {}

    # 多頭趨勢：價格回撤到 ema_fast 附近後反彈
    if ema_fast > ema_slow:
        if abs(prev['low'] - ema_fast) < threshold and price > ema_fast:
            signal_side = 'LONG'
            signal_details = {
                'side': 'LONG',
                'entry_price': price,
                'lowest_point': prev['low'],               # raw（給 _execute_trade 用）
                'stop_level': min(prev['low'], ema_slow) - atr * Config.SL_ATR_BUFFER_SIGNAL,
                'target_ref': df['high'].iloc[-20:].max(),
                'atr': atr,
                'volume': volume,
                'vol_ma': vol_ma,
                'signal_type': 'EMA_PULLBACK',
                'candle_confirmed': price > current['open'],
                'neckline': None,
                'fakeout_depth_atr': 0.0,
                'detection_method': 'ema_pullback',
            }

    # 空頭趨勢：價格反彈到 ema_fast 附近後回落
    elif ema_fast < ema_slow:
        if abs(prev['high'] - ema_fast) < threshold and price < ema_fast:
            signal_side = 'SHORT'
            signal_details = {
                'side': 'SHORT',
                'entry_price': price,
                'highest_point': prev['high'],              # raw（給 _execute_trade 用）
                'stop_level': max(prev['high'], ema_slow) + atr * Config.SL_ATR_BUFFER_SIGNAL,
                'target_ref': df['low'].iloc[-20:].min(),
                'atr': atr,
                'volume': volume,
                'vol_ma': vol_ma,
                'signal_type': 'EMA_PULLBACK',
                'candle_confirmed': price < current['open'],
                'neckline': None,
                'fakeout_depth_atr': 0.0,
                'detection_method': 'ema_pullback',
            }

    if signal_side is None:
        return False, None

    # 量能過濾（原始邏輯：hardcoded 0.6 門檻，signal_strength 固定 moderate）
    vol_ratio = volume / vol_ma if vol_ma > 0 else 0
    if vol_ratio < Config.VOLUME_PULLBACK_MIN_RATIO:
        return False, None

    signal_details['vol_ratio'] = vol_ratio
    signal_details['signal_strength'] = 'moderate'

    logger.info(f"📈 發現 EMA 回撤信號: {signal_side}")

    return True, signal_details


def detect_volume_breakout(
    df: pd.DataFrame,
    volume_breakout_mult: float = 2.0,
) -> Tuple[bool, Optional[Dict]]:
    """
    量能突破信號偵測

    邏輯：
    - 量比超過 volume_breakout_mult 倍
    - 價格突破近 10 根 K 線高點 + 陽線確認 → LONG
    - 價格跌破近 10 根 K 線低點 + 陰線確認 → SHORT

    注意：signal_details 必須包含 lowest_point / highest_point（raw 價格），
    因為 _execute_trade V5.3 路徑會用它當 extreme，再由 risk_manager 計算止損。

    Returns:
        (has_signal, signal_details)
    """
    if df is None or len(df) < 30:
        return False, None

    current = df.iloc[-1]
    volume = current.get('volume', 0)
    vol_ma = current.get('vol_ma', 0)
    atr = current.get('atr', 0)

    vol_ratio = volume / vol_ma if vol_ma > 0 else 0

    if vol_ratio < volume_breakout_mult:
        return False, None

    recent_high = df['high'].iloc[-10:-1].max()
    recent_low = df['low'].iloc[-10:-1].min()

    price = current['close']
    signal_side = None
    signal_details = {}

    # 量能突破 + 突破高點 + 陽線確認
    if price > recent_high and price > current['open']:
        signal_side = 'LONG'
        signal_details = {
            'side': 'LONG',
            'entry_price': price,
            'lowest_point': recent_low,                # raw（給 _execute_trade 用）
            'stop_level': recent_low - atr * 0.5,
            'target_ref': price + (price - recent_low),
            'atr': atr,
            'volume': volume,
            'vol_ma': vol_ma,
            'signal_type': 'VOLUME_BREAKOUT',
            'candle_confirmed': True,
            'neckline': None,
            'fakeout_depth_atr': 0.0,
            'detection_method': 'volume_breakout',
        }
    # 量能突破 + 跌破低點 + 陰線確認
    elif price < recent_low and price < current['open']:
        signal_side = 'SHORT'
        signal_details = {
            'side': 'SHORT',
            'entry_price': price,
            'highest_point': recent_high,              # raw（給 _execute_trade 用）
            'stop_level': recent_high + atr * 0.5,
            'target_ref': price - (recent_high - price),
            'atr': atr,
            'volume': volume,
            'vol_ma': vol_ma,
            'signal_type': 'VOLUME_BREAKOUT',
            'candle_confirmed': True,
            'neckline': None,
            'fakeout_depth_atr': 0.0,
            'detection_method': 'volume_breakout',
        }

    if signal_side is None:
        return False, None

    # 量能分級（原始邏輯：signal_strength 固定 strong）
    signal_details['vol_ratio'] = vol_ratio
    signal_details['signal_strength'] = 'strong'

    logger.info(f"📊 發現量能突破信號: {signal_side} (量能 {vol_ratio:.2f}x)")

    return True, signal_details
