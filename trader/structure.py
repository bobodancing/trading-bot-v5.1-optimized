"""
Swing Point 結構分析模組

提取自 scanner，供 V6.0 engine 和 scanner 共用。
實現真正的 Swing Point Pivot 偵測（左右側確認）+ Neckline 識別。
"""

import pandas as pd
from typing import Dict, List, Tuple, Optional


class StructureAnalysis:
    """結構分析工具"""

    @staticmethod
    def find_swing_points(df: pd.DataFrame, left_bars: int = 5, right_bars: int = 2) -> Dict:
        """
        找出已確認的 Swing High/Low（Pivot Points）

        算法：
        - Swing Low: 該 K 線的 low 必須低於左側 left_bars 根 K 線和右側 right_bars 根 K 線
        - Swing High: 該 K 線的 high 必須高於左側 left_bars 根 K 線和右側 right_bars 根 K 線

        重要：右側確認機制確保 pivot 是「已確認」的（right_bars 根 K 線已收盤完成）

        Args:
            df: OHLCV DataFrame
            left_bars: 左側 lookback 範圍（預設 5）
            right_bars: 右側確認範圍（預設 2，V6.0 要求至少 2 根確認）

        Returns:
            {
                'swing_lows': [(index, price), ...],
                'swing_highs': [(index, price), ...],
                'last_swing_low': float or None,
                'last_swing_high': float or None,
                'second_last_swing_low': float or None,
                'second_last_swing_high': float or None,
            }
        """
        if len(df) < left_bars + right_bars + 1:
            return {
                'swing_lows': [],
                'swing_highs': [],
                'last_swing_low': None,
                'last_swing_high': None,
                'second_last_swing_low': None,
                'second_last_swing_high': None,
            }

        swing_lows = []
        swing_highs = []

        # 遍歷可驗證範圍（排除頭尾無法確認的 K 線）
        for i in range(left_bars, len(df) - right_bars):
            current_low = df['low'].iloc[i]
            current_high = df['high'].iloc[i]

            # === Swing Low 檢查 ===
            is_swing_low = True
            # 左側驗證：左邊 left_bars 根 K 線都更高
            for j in range(1, left_bars + 1):
                if df['low'].iloc[i - j] <= current_low:
                    is_swing_low = False
                    break
            # 右側驗證：右邊 right_bars 根 K 線都更高
            if is_swing_low:
                for j in range(1, right_bars + 1):
                    if df['low'].iloc[i + j] <= current_low:
                        is_swing_low = False
                        break
            if is_swing_low:
                swing_lows.append((i, current_low))

            # === Swing High 檢查 ===
            is_swing_high = True
            # 左側驗證：左邊 left_bars 根 K 線都更低
            for j in range(1, left_bars + 1):
                if df['high'].iloc[i - j] >= current_high:
                    is_swing_high = False
                    break
            # 右側驗證：右邊 right_bars 根 K 線都更低
            if is_swing_high:
                for j in range(1, right_bars + 1):
                    if df['high'].iloc[i + j] >= current_high:
                        is_swing_high = False
                        break
            if is_swing_high:
                swing_highs.append((i, current_high))

        return {
            'swing_lows': swing_lows,
            'swing_highs': swing_highs,
            'last_swing_low': swing_lows[-1][1] if swing_lows else None,
            'last_swing_high': swing_highs[-1][1] if swing_highs else None,
            'second_last_swing_low': swing_lows[-2][1] if len(swing_lows) >= 2 else None,
            'second_last_swing_high': swing_highs[-2][1] if len(swing_highs) >= 2 else None,
        }

    @staticmethod
    def get_confirmed_pivots(df: pd.DataFrame, left: int = 5, right: int = 2) -> Dict[str, List[Tuple[int, float]]]:
        """
        只回傳「已確認」的 pivot points（右側 K 線已收盤完成）

        這是 find_swing_points 的簡化版本，專注於取得確認的 pivot 列表。
        V6.0 用於檢查最新的 confirmed swing point 來設定止損。

        Args:
            df: OHLCV DataFrame
            left: 左側 lookback
            right: 右側確認（至少 2）

        Returns:
            {
                'lows': [(index, price), ...],   # 按時間順序排列
                'highs': [(index, price), ...],
            }
        """
        result = StructureAnalysis.find_swing_points(df, left_bars=left, right_bars=right)
        return {
            'lows': result['swing_lows'],
            'highs': result['swing_highs'],
        }

    @staticmethod
    def find_neckline(
        df: pd.DataFrame,
        signal_side: str,
        swing_points: Optional[Dict] = None,
        left_bars: int = 5,
        right_bars: int = 2,
        entry_price: Optional[float] = None,
    ) -> Optional[float]:
        """
        找出 2B 信號的 Neckline（頸線）

        定義：距離 entry 最近的反向 confirmed swing point
        - LONG: entry 以上的所有 swing highs 中，price 最低者（最近阻力）
        - SHORT: entry 以下的所有 swing lows 中，price 最高者（最近支撐）

        若 entry_price=None（向後相容）或找不到符合條件的 swing，
        fallback 到原本邏輯（last_swing_high/low）。

        Args:
            df: OHLCV DataFrame
            signal_side: 'LONG' 或 'SHORT'
            swing_points: 預先計算的 swing points（可選，傳入可省計算）
            left_bars: Swing point 左側範圍
            right_bars: Swing point 右側確認範圍
            entry_price: 入場價格（用於選最近的 swing）

        Returns:
            neckline price (float) or None
        """
        if swing_points is None:
            swing_points = StructureAnalysis.find_swing_points(df, left_bars, right_bars)

        if signal_side == 'LONG':
            if entry_price is not None:
                # entry 以上的 swing highs 中，取 price 最低（距 entry 最近的阻力）
                candidates = [p for (_, p) in swing_points.get('swing_highs', []) if p > entry_price]
                if candidates:
                    return min(candidates)
            return swing_points.get('last_swing_high')

        elif signal_side == 'SHORT':
            if entry_price is not None:
                # entry 以下的 swing lows 中，取 price 最高（距 entry 最近的支撐）
                candidates = [p for (_, p) in swing_points.get('swing_lows', []) if p < entry_price]
                if candidates:
                    return max(candidates)
            return swing_points.get('last_swing_low')

        return None

    @staticmethod
    def get_validated_trailing_swing(
        df: pd.DataFrame,
        side: str,
        current_sl: float,
        left_bars: int = 5,
        right_bars: int = 2
    ) -> Optional[float]:
        """
        尋找符合 Temporal BOS + HL/LH 條件的結構移損點

        做多 (LONG):
        1. 找到最新 confirmed swing low（候選 HL）
        2. 條件 A (HL): swing_low > current_sl
        3. 條件 B (Temporal BOS): 找到該 swing low 之前的 swing high，
           且 current_close 已突破該 swing high

        做空 (SHORT): 鏡像邏輯
        1. 找到最新 confirmed swing high（候選 LH）
        2. 條件 A (LH): swing_high < current_sl
        3. 條件 B (Temporal BOS): 找到該 swing high 之前的 swing low，
           且 current_close 已跌破該 swing low
        """
        swings = StructureAnalysis.find_swing_points(df, left_bars, right_bars)
        lows = swings['swing_lows']
        highs = swings['swing_highs']

        if not lows or not highs:
            return None

        current_close = df['close'].iloc[-1]

        if side == 'LONG':
            last_low_idx, last_low_price = lows[-1]

            # 條件 A: Higher Low（必須高於當前 SL，確保棘輪只上不下）
            if last_low_price <= current_sl:
                return None

            # 條件 B: Temporal BOS — swing high 必須在 swing low 之前
            bos_target = None
            for h_idx, h_price in reversed(highs):
                if h_idx < last_low_idx:
                    bos_target = h_price
                    break

            if bos_target is None:
                return None

            # 價格已突破 BOS target
            if current_close > bos_target:
                return last_low_price

        elif side == 'SHORT':
            last_high_idx, last_high_price = highs[-1]

            # 條件 A: Lower High（必須低於當前 SL）
            if last_high_price >= current_sl:
                return None

            # 條件 B: Temporal BOS — swing low 必須在 swing high 之前
            bos_target = None
            for l_idx, l_price in reversed(lows):
                if l_idx < last_high_idx:
                    bos_target = l_price
                    break

            if bos_target is None:
                return None

            # 價格已跌破 BOS target
            if current_close < bos_target:
                return last_high_price

        return None

    @staticmethod
    def find_latest_confirmed_swing(
        df: pd.DataFrame,
        direction: str,
        left_bars: int = 5,
        right_bars: int = 2
    ) -> Optional[float]:
        """
        找出最新的 confirmed swing point（用於 Stage 3 移損）

        Args:
            df: OHLCV DataFrame
            direction: 'low' 或 'high'
            left_bars: 左側範圍
            right_bars: 右側確認範圍

        Returns:
            最新的 swing low/high price or None
        """
        swing_points = StructureAnalysis.find_swing_points(df, left_bars, right_bars)

        if direction == 'low':
            return swing_points.get('last_swing_low')
        elif direction == 'high':
            return swing_points.get('last_swing_high')
        else:
            return None
