# -*- coding: utf-8 -*-
"""
交易機器人 v5.3 - 出場機制重構版
基於 v5.2 架構，重構出場邏輯：
1. 統一出場 SOP（取消 A/B/C 分流，所有等級共用同一套出場流程）
2. 漸進式移損保護（1.0R→+0.3R, 1.5R→+0.5R, 2.5R→+1.5R）
3. 時間退出機制（超時未達目標自動平倉）
4. ATR 追蹤止損（2.5R 後啟動）

v5.2 功能：硬止損單、移動止損、防重複開倉
v5.1 功能：MTF、動態閾值、分級入場、互補策略
"""

import ccxt
import pandas as pd
import pandas_ta as ta
import time
import logging
from logging.handlers import RotatingFileHandler
import requests
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from decimal import Decimal, ROUND_DOWN

# ==================== 配置區 ====================
class Config:
    """配置管理類 - v5.3 出場重構版"""
    # 基本設置
    EXCHANGE = 'binance'
    API_KEY = 'your_api_key_here'
    API_SECRET = 'your_api_secret_here'
    SANDBOX_MODE = True

    # 交易模式
    TRADING_MODE = 'future'
    TRADING_DIRECTION = 'both'
    LEVERAGE = 5
    USE_HARD_STOP_LOSS = True

    # Telegram
    TELEGRAM_ENABLED = False
    TELEGRAM_BOT_TOKEN = ''
    TELEGRAM_CHAT_ID = ''

    # 交易標的
    SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

    # 風險管理
    RISK_PER_TRADE = 0.01
    MAX_TOTAL_RISK = 0.05
    MAX_POSITIONS_PER_GROUP = 2
    MAX_POSITION_PERCENT = 0.3  # 單筆倉位最多使用帳戶餘額的 30%（考慮槓桿後）

    # 技術指標
    LOOKBACK_PERIOD = 20
    VOLUME_MA_PERIOD = 20
    ATR_PERIOD = 14
    ATR_MULTIPLIER = 1.5

    # 時間框架
    TIMEFRAME_TREND = '1d'
    TIMEFRAME_SIGNAL = '1h'
    EMA_TREND = 200

    # ============ v5.1 新增：多時間框架確認 ============
    ENABLE_MTF_CONFIRMATION = True  # 開啟多時間框架確認
    TIMEFRAME_MTF = '4h'  # 中間時間框架
    MTF_EMA_FAST = 20
    MTF_EMA_SLOW = 50
    
    # ============ v5.1 新增：動態閾值系統 ============
    ENABLE_DYNAMIC_THRESHOLDS = True
    # ADX 動態調整
    ADX_BASE_THRESHOLD = 18  # v5.3: 動態閾值下限（從 15 提高到 18）
    ADX_STRONG_THRESHOLD = 25  # 強趨勢閾值
    # ATR 動態調整
    ATR_QUIET_MULTIPLIER = 1.2  # 低波動時期
    ATR_NORMAL_MULTIPLIER = 1.5  # 正常時期
    ATR_VOLATILE_MULTIPLIER = 2.0  # 高波動時期
    
    # ============ v5.1 新增：分級入場系統 ============
    ENABLE_TIERED_ENTRY = True
    # A 級信號（最佳）: 全部條件滿足，正常倉位
    # B 級信號（良好）: 放寬部分條件，減少倉位
    # C 級信號（可接受）: 最低標準，最小倉位
    TIER_A_POSITION_MULT = 1.0
    TIER_B_POSITION_MULT = 0.7
    TIER_C_POSITION_MULT = 0.5
    
    # ============ v5.1 新增：互補策略模組 ============
    ENABLE_EMA_PULLBACK = True  # EMA 回撤策略
    EMA_PULLBACK_FAST = 10
    EMA_PULLBACK_SLOW = 20
    EMA_PULLBACK_THRESHOLD = 0.02  # 回撤到 EMA 的 2% 範圍內
    
    ENABLE_VOLUME_BREAKOUT = True  # 量能突破策略
    VOLUME_BREAKOUT_MULT = 2.0  # 量能需達均量 2 倍
    
    # v4 市場過濾器
    ENABLE_MARKET_FILTER = True
    ADX_THRESHOLD = 20  # v5.3: 提高 ADX 閾值（從 15 提高到 20）
    ATR_SPIKE_MULTIPLIER = 2.0  # 放寬波動限制（從 1.5 提高到 2.0）
    EMA_ENTANGLEMENT_THRESHOLD = 0.02  # 放寬糾纏閾值（從 0.01 提高到 0.02）

    # v4.1 量能分級（優化版）
    ENABLE_VOLUME_GRADING = True
    VOL_EXPLOSIVE_THRESHOLD = 2.5
    VOL_STRONG_THRESHOLD = 1.5  # 降低強勢門檻（從 1.8 降到 1.5）
    VOL_MODERATE_THRESHOLD = 1.0  # 降低中等門檻（從 1.2 降到 1.0）
    VOL_MINIMUM_THRESHOLD = 0.7  # 降低最低門檻（從 0.8 降到 0.7）
    ACCEPT_WEAK_SIGNALS = True

    # ============ v5.3 統一出場 SOP ============
    FIRST_PARTIAL_PCT = 30     # 1.5R 減倉比例
    SECOND_PARTIAL_PCT = 30    # 2.5R 減倉比例
    # 尾倉 = 100 - 30 - 30 = 40%，由追蹤止損管理
    APLUS_TRAILING_ATR_MULT = 1.5  # 追蹤止損 ATR 乘數
    MAX_HOLD_HOURS = 24  # 最大持倉時間（小時），未達首次減倉則市價出場

    # Scanner 整合設定
    USE_SCANNER_SYMBOLS = False  # 是否使用掃描結果作為交易標的
    SCANNER_JSON_PATH = "hot_symbols.json"  # 掃描結果檔案
    SCANNER_MAX_AGE_MINUTES = 30  # 掃描結果最大有效期（分鐘）

    # 其他
    ENABLE_STRUCTURE_BREAK_EXIT = True  # v5.3: 預設開啟結構破壞出場
    CHECK_INTERVAL = 300
    MAX_RETRY = 3
    RETRY_DELAY = 5
    TREND_CACHE_HOURS = 4

    # JSON key → Config 屬性的映射表（snake_case → UPPER_SNAKE_CASE）
    _KEY_MAP = {
        # API 設置
        'api_key': 'API_KEY', 'api_secret': 'API_SECRET',
        'exchange': 'EXCHANGE', 'sandbox_mode': 'SANDBOX_MODE',
        # 交易模式
        'trading_mode': 'TRADING_MODE', 'trading_direction': 'TRADING_DIRECTION',
        'leverage': 'LEVERAGE', 'use_hard_stop_loss': 'USE_HARD_STOP_LOSS',
        # Telegram
        'telegram_enabled': 'TELEGRAM_ENABLED',
        'telegram_bot_token': 'TELEGRAM_BOT_TOKEN', 'telegram_chat_id': 'TELEGRAM_CHAT_ID',
        # 交易標的
        'symbols': 'SYMBOLS',
        # 風險管理
        'risk_per_trade': 'RISK_PER_TRADE', 'max_total_risk': 'MAX_TOTAL_RISK',
        'max_positions_per_group': 'MAX_POSITIONS_PER_GROUP',
        'max_position_percent': 'MAX_POSITION_PERCENT',
        # 技術參數
        'lookback_period': 'LOOKBACK_PERIOD', 'volume_ma_period': 'VOLUME_MA_PERIOD',
        'atr_period': 'ATR_PERIOD', 'atr_multiplier': 'ATR_MULTIPLIER',
        # 市場過濾
        'enable_market_filter': 'ENABLE_MARKET_FILTER', 'adx_threshold': 'ADX_THRESHOLD',
        'atr_spike_multiplier': 'ATR_SPIKE_MULTIPLIER',
        'ema_entanglement_threshold': 'EMA_ENTANGLEMENT_THRESHOLD',
        # v4.1 量能分級
        'enable_volume_grading': 'ENABLE_VOLUME_GRADING',
        'vol_explosive_threshold': 'VOL_EXPLOSIVE_THRESHOLD',
        'vol_strong_threshold': 'VOL_STRONG_THRESHOLD',
        'vol_moderate_threshold': 'VOL_MODERATE_THRESHOLD',
        'vol_minimum_threshold': 'VOL_MINIMUM_THRESHOLD',
        'accept_weak_signals': 'ACCEPT_WEAK_SIGNALS',
        # v5.1 新增
        'enable_mtf_confirmation': 'ENABLE_MTF_CONFIRMATION',
        'enable_dynamic_thresholds': 'ENABLE_DYNAMIC_THRESHOLDS',
        'enable_tiered_entry': 'ENABLE_TIERED_ENTRY',
        'enable_ema_pullback': 'ENABLE_EMA_PULLBACK',
        'enable_volume_breakout': 'ENABLE_VOLUME_BREAKOUT',
        # v5.1 分級倉位
        'tier_a_position_mult': 'TIER_A_POSITION_MULT',
        'tier_b_position_mult': 'TIER_B_POSITION_MULT',
        'tier_c_position_mult': 'TIER_C_POSITION_MULT',
        # v5.3 統一出場 SOP
        'first_partial_pct': 'FIRST_PARTIAL_PCT',
        'second_partial_pct': 'SECOND_PARTIAL_PCT',
        'aplus_trailing_atr_mult': 'APLUS_TRAILING_ATR_MULT',
        'max_hold_hours': 'MAX_HOLD_HOURS',
        # v5.2 Scanner 整合
        'use_scanner_symbols': 'USE_SCANNER_SYMBOLS',
        'scanner_json_path': 'SCANNER_JSON_PATH',
        'scanner_max_age_minutes': 'SCANNER_MAX_AGE_MINUTES',
        # 其他
        'enable_structure_break_exit': 'ENABLE_STRUCTURE_BREAK_EXIT',
        'check_interval': 'CHECK_INTERVAL',
    }

    @classmethod
    def load_from_json(cls, config_file: str = "bot_config.json"):
        """從 JSON 配置文件加載設置"""
        if not os.path.exists(config_file):
            logger.warning(f"⚠️ 配置文件 {config_file} 不存在，使用默認配置")
            return

        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            loaded_count = 0
            for json_key, attr_name in cls._KEY_MAP.items():
                if json_key in config_data:
                    setattr(cls, attr_name, config_data[json_key])
                    loaded_count += 1

            logger.info(f"✅ 已從 {config_file} 加載 {loaded_count} 項配置")

        except Exception as e:
            logger.error(f"❌ 加載配置文件失敗: {e}")
            logger.info("⚠️ 將使用默認配置")

# ==================== 日誌設置 ====================
class SafeStreamWrapper:
    """安全的 Stream Wrapper，自動處理 Unicode 編碼錯誤"""
    def __init__(self, stream):
        self.stream = stream
        self.encoding = 'utf-8'

    def write(self, msg):
        try:
            self.stream.write(msg)
        except UnicodeEncodeError:
            safe_msg = msg.encode(self.stream.encoding, errors='replace').decode(self.stream.encoding)
            self.stream.write(safe_msg)

    def flush(self):
        self.stream.flush()

# 設置文件日誌
file_handler = RotatingFileHandler(
    'trading_bot.log', maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
)
file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

# 設置終端日誌
safe_stdout = SafeStreamWrapper(sys.stdout)
stream_handler = logging.StreamHandler(safe_stdout)
stream_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)
logger = logging.getLogger(__name__)

# ==================== Telegram 通知 ====================
class TelegramNotifier:
    """Telegram 推送通知類"""

    @staticmethod
    def send_message(message: str):
        if not Config.TELEGRAM_ENABLED:
            return

        try:
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            payload = {
                'chat_id': Config.TELEGRAM_CHAT_ID,
                'text': message,
                'parse_mode': 'HTML'
            }
            requests.post(url, data=payload, timeout=10)
        except Exception as e:
            logger.error(f"Telegram 發送失敗: {e}")

    @staticmethod
    def notify_signal(symbol: str, details: Dict):
        """通知交易信號（v5.1 新增信號等級）"""
        strength_emoji = {
            'explosive': '🔥🔥🔥',
            'strong': '💪💪',
            'moderate': '✅',
            'weak': '⚠️'
        }
        tier_emoji = {
            'A': '🏆',
            'B': '🥈',
            'C': '🥉'
        }
        strength = details.get('signal_strength', 'unknown')
        tier = details.get('signal_tier', 'B')
        emoji = strength_emoji.get(strength, '🚀')
        side = details.get('side', 'LONG')

        msg = f"""
{emoji} <b>交易信號 - {strength.upper()} ({side})</b>
{tier_emoji.get(tier, '')} 信號等級: {tier}
──────────────────
幣種: {symbol}
方向: {side}
市場狀態: {details.get('market_state', 'N/A')}
量能強度: {details.get('vol_ratio', 0):.2f}x 均量
入場價: ${details['entry_price']:.2f}
止損價: ${details['stop_loss']:.2f}
目標位: ${details.get('target_ref', 'N/A')}
倉位: {details['position_size']:.6f}
1.5R: ${details.get('r15_target', 'N/A')}
──────────────────
        """
        TelegramNotifier.send_message(msg.strip())

    @staticmethod
    def notify_action(symbol: str, action: str, price: float, details: str = ""):
        emoji_map = {
            '1.5R移損': '🛡',
            '目標減倉': '💰',
            '止損出場': '🚨',
            '結構破壞': '⚠️',
            '硬止損觸發': '🔴'
        }
        emoji = emoji_map.get(action, '🔔')

        msg = f"{emoji} <b>{action}</b>\n幣種: {symbol}\n價格: ${price:.2f}"
        if details:
            msg += f"\n{details}"
        TelegramNotifier.send_message(msg)


# ==================== v5.1 動態閾值管理器 ====================
class DynamicThresholdManager:
    """v5.1 新增：動態閾值管理器"""
    
    @staticmethod
    def get_adx_threshold(df: pd.DataFrame) -> float:
        """根據近期市場狀態動態調整 ADX 閾值"""
        if not Config.ENABLE_DYNAMIC_THRESHOLDS:
            return Config.ADX_THRESHOLD
            
        # 計算近期 ADX 的平均值和標準差
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is None or adx_data.empty:
            return Config.ADX_THRESHOLD
            
        if isinstance(adx_data, pd.DataFrame):
            adx_col = [col for col in adx_data.columns if col.startswith('ADX')]
            if adx_col:
                adx_series = adx_data[adx_col[0]].dropna()
            else:
                return Config.ADX_THRESHOLD
        else:
            adx_series = adx_data.dropna()
            
        if len(adx_series) < 20:
            return Config.ADX_THRESHOLD
            
        avg_adx = adx_series.iloc[-20:].mean()
        
        # 如果市場整體趨勢偏弱，降低閾值以捕捉機會
        if avg_adx < 20:
            return Config.ADX_BASE_THRESHOLD  # 使用較低的基礎閾值
        elif avg_adx > 30:
            return Config.ADX_STRONG_THRESHOLD  # 強趨勢市場使用較高閾值
        else:
            return Config.ADX_THRESHOLD  # 正常閾值
    
    @staticmethod
    def get_atr_multiplier(df: pd.DataFrame) -> float:
        """根據近期波動率動態調整 ATR 乘數"""
        if not Config.ENABLE_DYNAMIC_THRESHOLDS:
            return Config.ATR_MULTIPLIER
            
        if 'atr' not in df.columns or len(df) < 20:
            return Config.ATR_MULTIPLIER
            
        # 計算近期 ATR 變化
        recent_atr = df['atr'].iloc[-5:].mean()
        historical_atr = df['atr'].iloc[-20:-5].mean()
        
        if historical_atr == 0:
            return Config.ATR_MULTIPLIER
            
        atr_ratio = recent_atr / historical_atr
        
        # 動態調整
        if atr_ratio < 0.8:  # 低波動期
            return Config.ATR_QUIET_MULTIPLIER
        elif atr_ratio > 1.5:  # 高波動期
            return Config.ATR_VOLATILE_MULTIPLIER
        else:
            return Config.ATR_NORMAL_MULTIPLIER


# ==================== v5.1 多時間框架確認器 ====================
class MTFConfirmation:
    """v5.1 新增：多時間框架確認系統"""
    
    @staticmethod
    def check_mtf_alignment(df_mtf: pd.DataFrame, side: str) -> Tuple[bool, str]:
        """
        檢查中間時間框架（4H）是否與交易方向一致
        這不作為硬性條件，而是用於信號分級
        """
        if not Config.ENABLE_MTF_CONFIRMATION or df_mtf.empty:
            return True, "MTF 確認已關閉"
            
        if len(df_mtf) < Config.MTF_EMA_SLOW:
            return True, "MTF 數據不足"
            
        # 計算 MTF 的快慢均線
        ema_fast = ta.ema(df_mtf['close'], length=Config.MTF_EMA_FAST)
        ema_slow = ta.ema(df_mtf['close'], length=Config.MTF_EMA_SLOW)
        
        if ema_fast is None or ema_slow is None:
            return True, "MTF 指標計算失敗"
            
        current_fast = ema_fast.iloc[-1]
        current_slow = ema_slow.iloc[-1]
        current_price = df_mtf['close'].iloc[-1]
        
        if side == 'LONG':
            # 做多：價格在快線上方，快線在慢線上方
            aligned = current_price > current_fast and current_fast > current_slow
            if aligned:
                return True, "MTF 多頭排列確認 ✅"
            else:
                return False, "MTF 未完全多頭排列"
        else:  # SHORT
            aligned = current_price < current_fast and current_fast < current_slow
            if aligned:
                return True, "MTF 空頭排列確認 ✅"
            else:
                return False, "MTF 未完全空頭排列"


# ==================== v5.1 信號分級系統 ====================
class SignalTierSystem:
    """v5.1 新增：信號分級系統"""
    
    @staticmethod
    def calculate_signal_tier(
        signal_details: Dict,
        mtf_aligned: bool,
        market_strong: bool,
        volume_grade: str
    ) -> Tuple[str, float]:
        """
        計算信號等級並返回對應的倉位乘數
        A 級：所有條件滿足
        B 級：大部分條件滿足
        C 級：基本條件滿足
        """
        if not Config.ENABLE_TIERED_ENTRY:
            return 'B', Config.TIER_B_POSITION_MULT
            
        score = 0
        
        # MTF 對齊 +2 分
        if mtf_aligned:
            score += 2
            
        # 市場狀態強勢 +2 分
        if market_strong:
            score += 2
            
        # 量能評分
        if volume_grade in ['explosive', 'strong']:
            score += 2
        elif volume_grade == 'moderate':
            score += 1
            
        # K 線形態確認 +1 分
        if signal_details.get('candle_confirmed', False):
            score += 1
            
        # 根據分數決定等級
        if score >= 6:
            return 'A', Config.TIER_A_POSITION_MULT
        elif score >= 4:
            return 'B', Config.TIER_B_POSITION_MULT
        else:
            return 'C', Config.TIER_C_POSITION_MULT


# ==================== v4 市場過濾器（優化版）====================
class MarketFilter:
    """市場狀態過濾器 - v5.1 優化版"""

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

        # v5.1: 使用動態 ADX 閾值
        dynamic_adx_threshold = DynamicThresholdManager.get_adx_threshold(df_trend)
        
        # 過濾 1: ADX 趨勢強度
        adx_data = ta.adx(df_trend['high'], df_trend['low'], df_trend['close'], length=14)

        if adx_data is None or adx_data.empty:
            logger.warning(f"{symbol} ADX 計算失敗")
            return False, "ADX 計算失敗", False

        if isinstance(adx_data, pd.DataFrame):
            adx_col = [col for col in adx_data.columns if col.startswith('ADX')]
            if adx_col:
                current_adx = adx_data[adx_col[0]].iloc[-1]
            else:
                return False, "ADX 數據格式錯誤", False
        else:
            current_adx = adx_data.iloc[-1]

        is_strong_market = current_adx >= Config.ADX_STRONG_THRESHOLD
        
        if current_adx < dynamic_adx_threshold:
            return False, f"趨勢不足 (ADX={current_adx:.1f}, 閾值={dynamic_adx_threshold:.1f})", False

        # 過濾 2: ATR 波動性異常
        if 'atr' in df_trend.columns:
            current_atr = df_trend['atr'].iloc[-1]
            lookback = min(10, len(df_trend) - 1)
            avg_atr = df_trend['atr'].iloc[-lookback-1:-1].mean()

            if pd.notna(avg_atr) and avg_atr > 0:
                if current_atr > avg_atr * Config.ATR_SPIKE_MULTIPLIER:
                    return False, f"波動過大 (ATR={current_atr/avg_atr:.1f}x)", False

        # 過濾 3: 均線糾纏
        ema_10 = ta.ema(df_trend['close'], length=10)
        ema_20 = ta.ema(df_trend['close'], length=20)

        if ema_10 is not None and ema_20 is not None and len(ema_10) > 0 and len(ema_20) > 0:
            if pd.notna(ema_10.iloc[-1]) and pd.notna(ema_20.iloc[-1]) and ema_20.iloc[-1] != 0:
                ema_diff = abs(ema_10.iloc[-1] - ema_20.iloc[-1]) / ema_20.iloc[-1]

                if ema_diff < Config.EMA_ENTANGLEMENT_THRESHOLD:
                    return False, f"均線糾纏 (差距={ema_diff*100:.1f}%)", False

        logger.debug(f"✅ {symbol} 市場狀態良好 (ADX={current_adx:.1f}, 動態閾值={dynamic_adx_threshold:.1f})")
        return True, "市場狀態良好", is_strong_market


# ==================== 技術分析（v5.1 增強版）====================
class TechnicalAnalysis:
    """技術分析工具類（v5.1 增強版）"""

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

        # 基礎指標
        ema_period = getattr(Config, 'EMA_TREND', 200)
        df['ema_trend'] = ta.ema(df['close'], length=ema_period)
        df['vol_ma'] = ta.sma(df['volume'], length=Config.VOLUME_MA_PERIOD)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=Config.ATR_PERIOD)

        # v5.1: 額外的 EMA 用於回撤策略
        df['ema_fast'] = ta.ema(df['close'], length=Config.EMA_PULLBACK_FAST)
        df['ema_slow'] = ta.ema(df['close'], length=Config.EMA_PULLBACK_SLOW)

        # ADX
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is not None and not adx_data.empty:
            if isinstance(adx_data, pd.DataFrame):
                adx_col = [col for col in adx_data.columns if col.startswith('ADX')]
                if adx_col:
                    df['adx'] = adx_data[adx_col[0]]
            else:
                df['adx'] = adx_data

        return df

    @staticmethod
    def check_trend(df: pd.DataFrame, side: str) -> Tuple[bool, str]:
        """
        檢查趨勢（雙向版本）
        side: 'LONG' 或 'SHORT'
        返回: (趨勢是否符合, 趨勢描述)
        """
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
    def detect_2B_signal(df: pd.DataFrame) -> Tuple[bool, Optional[Dict]]:
        """
        檢測雙向 2B 突破信號（v5.1 優化版 - 放寬條件）
        返回: (是否有信號, 信號詳情)
        """
        if len(df) < Config.LOOKBACK_PERIOD + 1:
            return False, None

        prev_low = df['low'].iloc[-(Config.LOOKBACK_PERIOD+1):-1].min()
        prev_high = df['high'].iloc[-(Config.LOOKBACK_PERIOD+1):-1].max()

        current = df.iloc[-1]

        signal_side = None
        signal_details = {}

        # === Bullish 2B (做多) ===
        is_bullish_fakeout = (current['low'] < prev_low) and (current['close'] > prev_low)

        if is_bullish_fakeout:
            signal_side = 'LONG'
            signal_details = {
                'side': 'LONG',
                'entry_price': current['close'],
                'lowest_point': current['low'],
                'stop_level': prev_low,
                'target_ref': prev_high,
                'prev_low': prev_low,
                'prev_high': prev_high,
                'atr': current['atr'],
                'volume': current['volume'],
                'vol_ma': current['vol_ma'],
                'signal_time': current.get('timestamp'),
                'candle_confirmed': current['close'] > current['open']  # 收陽線確認
            }

        # === Bearish 2B (做空) ===
        is_bearish_fakeout = (current['high'] > prev_high) and (current['close'] < prev_high)

        if is_bearish_fakeout:
            signal_side = 'SHORT'
            signal_details = {
                'side': 'SHORT',
                'entry_price': current['close'],
                'highest_point': current['high'],
                'stop_level': prev_high,
                'target_ref': prev_low,
                'prev_low': prev_low,
                'prev_high': prev_high,
                'atr': current['atr'],
                'volume': current['volume'],
                'vol_ma': current['vol_ma'],
                'signal_time': current.get('timestamp'),
                'candle_confirmed': current['close'] < current['open']  # 收陰線確認
            }

        if signal_side is None:
            return False, None

        # ========== v5.1 優化：量能分級系統（放寬標準）==========
        vol_ratio = current['volume'] / current['vol_ma'] if current['vol_ma'] > 0 else 0

        if vol_ratio >= Config.VOL_EXPLOSIVE_THRESHOLD:
            signal_strength = 'explosive'
            logger.info(f"🔥 量能爆發：{vol_ratio:.2f}x 均量")
        elif vol_ratio >= Config.VOL_STRONG_THRESHOLD:
            signal_strength = 'strong'
            logger.info(f"💪 量能強勢：{vol_ratio:.2f}x 均量")
        elif vol_ratio >= Config.VOL_MODERATE_THRESHOLD:
            signal_strength = 'moderate'
            logger.info(f"✅ 量能確認：{vol_ratio:.2f}x 均量")
        else:
            signal_strength = 'weak'
            logger.info(f"⚠️ 量能偏弱：{vol_ratio:.2f}x 均量")

        signal_details['vol_ratio'] = vol_ratio
        signal_details['signal_strength'] = signal_strength

        # v5.1: 量能過濾邏輯（放寬）
        if Config.ENABLE_VOLUME_GRADING:
            if vol_ratio < Config.VOL_MINIMUM_THRESHOLD:
                logger.debug(f"❌ 量能過低 ({vol_ratio:.2f}x < {Config.VOL_MINIMUM_THRESHOLD}x)，信號過濾")
                return False, None

            if not Config.ACCEPT_WEAK_SIGNALS and signal_strength == 'weak':
                logger.debug(f"❌ 弱勢信號已關閉（當前 {vol_ratio:.2f}x），信號過濾")
                return False, None
        else:
            if current['volume'] <= current['vol_ma']:
                return False, None

        # v5.1: 放寬深度過濾器
        depth_filter = abs(signal_details.get('lowest_point', signal_details.get('highest_point', 0)) -
                          signal_details['stop_level']) < (current['atr'] * 3)  # 從 2 放寬到 3

        # v5.1: K 線形態不再是硬性條件
        candle_filter = signal_details.get('candle_confirmed', False)
        
        # 即使 K 線形態未確認，只要深度過濾通過也可以進場（會降級信號等級）
        if depth_filter:
            return True, signal_details

        return False, None

    @staticmethod
    def detect_ema_pullback_signal(df: pd.DataFrame) -> Tuple[bool, Optional[Dict]]:
        """
        v5.1 新增：EMA 回撤策略
        價格回撤到 EMA 附近後反彈
        """
        if not Config.ENABLE_EMA_PULLBACK or len(df) < 30:
            return False, None
            
        current = df.iloc[-1]
        prev = df.iloc[-2]
        
        if 'ema_fast' not in current or 'ema_slow' not in current:
            return False, None
            
        ema_fast = current['ema_fast']
        ema_slow = current['ema_slow']
        price = current['close']
        
        # 計算回撤閾值
        threshold = ema_fast * Config.EMA_PULLBACK_THRESHOLD
        
        signal_side = None
        signal_details = {}
        
        # 多頭回撤：EMA 多頭排列，價格回撤到快線附近後反彈
        if ema_fast > ema_slow:
            # 價格曾接近或觸及 EMA，現在反彈
            if abs(prev['low'] - ema_fast) < threshold and price > ema_fast:
                signal_side = 'LONG'
                signal_details = {
                    'side': 'LONG',
                    'entry_price': price,
                    'lowest_point': prev['low'],
                    'stop_level': min(prev['low'], ema_slow) - current['atr'] * 0.5,
                    'target_ref': df['high'].iloc[-20:].max(),
                    'atr': current['atr'],
                    'volume': current['volume'],
                    'vol_ma': current['vol_ma'],
                    'signal_type': 'EMA_PULLBACK',
                    'candle_confirmed': price > current['open']
                }
                
        # 空頭回撤
        elif ema_fast < ema_slow:
            if abs(prev['high'] - ema_fast) < threshold and price < ema_fast:
                signal_side = 'SHORT'
                signal_details = {
                    'side': 'SHORT',
                    'entry_price': price,
                    'highest_point': prev['high'],
                    'stop_level': max(prev['high'], ema_slow) + current['atr'] * 0.5,
                    'target_ref': df['low'].iloc[-20:].min(),
                    'atr': current['atr'],
                    'volume': current['volume'],
                    'vol_ma': current['vol_ma'],
                    'signal_type': 'EMA_PULLBACK',
                    'candle_confirmed': price < current['open']
                }
        
        if signal_side is None:
            return False, None
            
        # 量能確認（EMA 回撤不需要太強的量能）
        vol_ratio = current['volume'] / current['vol_ma'] if current['vol_ma'] > 0 else 0
        if vol_ratio < 0.6:  # 回撤策略量能要求更低
            return False, None
            
        signal_details['vol_ratio'] = vol_ratio
        signal_details['signal_strength'] = 'moderate'  # 回撤信號固定為中等強度
        
        logger.info(f"📈 發現 EMA 回撤信號: {signal_side}")
        return True, signal_details

    @staticmethod
    def detect_volume_breakout_signal(df: pd.DataFrame) -> Tuple[bool, Optional[Dict]]:
        """
        v5.1 新增：量能突破策略
        當出現異常大量時尋找突破機會
        """
        if not Config.ENABLE_VOLUME_BREAKOUT or len(df) < 30:
            return False, None
            
        current = df.iloc[-1]
        
        vol_ratio = current['volume'] / current['vol_ma'] if current['vol_ma'] > 0 else 0
        
        # 需要明顯的放量
        if vol_ratio < Config.VOLUME_BREAKOUT_MULT:
            return False, None
            
        # 計算近期區間
        recent_high = df['high'].iloc[-10:-1].max()
        recent_low = df['low'].iloc[-10:-1].min()
        
        signal_side = None
        signal_details = {}
        
        # 放量突破高點
        if current['close'] > recent_high and current['close'] > current['open']:
            signal_side = 'LONG'
            signal_details = {
                'side': 'LONG',
                'entry_price': current['close'],
                'lowest_point': recent_low,
                'stop_level': recent_low - current['atr'] * 0.5,
                'target_ref': current['close'] + (current['close'] - recent_low),
                'atr': current['atr'],
                'volume': current['volume'],
                'vol_ma': current['vol_ma'],
                'signal_type': 'VOLUME_BREAKOUT',
                'candle_confirmed': True
            }
            
        # 放量突破低點
        elif current['close'] < recent_low and current['close'] < current['open']:
            signal_side = 'SHORT'
            signal_details = {
                'side': 'SHORT',
                'entry_price': current['close'],
                'highest_point': recent_high,
                'stop_level': recent_high + current['atr'] * 0.5,
                'target_ref': current['close'] - (recent_high - current['close']),
                'atr': current['atr'],
                'volume': current['volume'],
                'vol_ma': current['vol_ma'],
                'signal_type': 'VOLUME_BREAKOUT',
                'candle_confirmed': True
            }
            
        if signal_side is None:
            return False, None
            
        signal_details['vol_ratio'] = vol_ratio
        signal_details['signal_strength'] = 'strong'  # 量能突破固定為強勢
        
        logger.info(f"📊 發現量能突破信號: {signal_side} (量能 {vol_ratio:.2f}x)")
        return True, signal_details

    @staticmethod
    def check_structure_break(df: pd.DataFrame, current_price: float, side: str) -> bool:
        """
        v4 新增：檢查結構是否破壞（雙向版本）
        """
        if not Config.ENABLE_STRUCTURE_BREAK_EXIT or len(df) < 10:
            return False

        if side == 'LONG':
            swing_low = df['low'].iloc[-10:-1].min()
            return current_price < swing_low * 0.995
        else:
            swing_high = df['high'].iloc[-10:-1].max()
            return current_price > swing_high * 1.005


# ==================== 精度處理 ====================
class PrecisionHandler:
    """交易所精度處理類"""

    # Binance Futures 最小訂單價值為 100 USDT
    FUTURES_MIN_NOTIONAL = 5

    DEFAULT_PRECISIONS = {
        'BTC/USDT': {'amount': 3, 'price': 2, 'min_amount': 0.001, 'min_cost': 5},
        'ETH/USDT': {'amount': 3, 'price': 2, 'min_amount': 0.001, 'min_cost': 5},
        'SOL/USDT': {'amount': 0, 'price': 2, 'min_amount': 1, 'min_cost': 5},  # 🔧 修復：SOL 精度為整數
        'DOGE/USDT': {'amount': 0, 'price': 5, 'min_amount': 1, 'min_cost': 5},
        'ADA/USDT': {'amount': 0, 'price': 4, 'min_amount': 1, 'min_cost': 5},
        'LINK/USDT': {'amount': 2, 'price': 3, 'min_amount': 0.01, 'min_cost': 5},
    }

    def __init__(self, exchange):
        self.exchange = exchange
        self.markets = {}
        self.use_default_precision = False
        self._exchange_info_cache = {}  # Binance exchangeInfo 快取
        self.load_markets()

    def load_markets(self):
        try:
            self.markets = self.exchange.load_markets(reload=True)
            logger.info("✅ 市場精度資訊已載入")
            self.use_default_precision = False
        except Exception as e:
            logger.error(f"載入市場失敗: {e}")
            logger.warning("⚠️ 使用默認精度設置")
            self.use_default_precision = True
            self.markets = {}

    @staticmethod
    def _step_to_decimals(step) -> int:
        """將步長（如 0.001）轉換為小數位數（如 3）"""
        import math
        if step is None or step <= 0:
            return 3
        if step >= 1:
            return 0
        return max(0, int(round(-math.log10(float(step)))))

    def _fetch_binance_precision(self, symbol: str) -> int:
        """從 Binance API 直接查詢精度（適用於 DEFAULT_PRECISIONS 未覆蓋的動態幣對）"""
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]

        try:
            symbol_id = symbol.replace('/', '')
            if Config.SANDBOX_MODE and Config.TRADING_MODE == 'future':
                url = f"https://testnet.binancefuture.com/fapi/v1/exchangeInfo"
            else:
                url = f"https://fapi.binance.com/fapi/v1/exchangeInfo"

            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                for s in data.get('symbols', []):
                    sid = s.get('symbol', '')
                    qty_precision = s.get('quantityPrecision', 3)
                    # 快取所有幣對
                    ccxt_sym = sid[:-4] + '/' + sid[-4:] if sid.endswith('USDT') else sid
                    self._exchange_info_cache[ccxt_sym] = int(qty_precision)

                if symbol in self._exchange_info_cache:
                    p = self._exchange_info_cache[symbol]
                    logger.info(f"📋 {symbol} 從交易所 API 取得精度: {p}")
                    return p
        except Exception as e:
            logger.debug(f"查詢 exchangeInfo 失敗: {e}")

        return 3  # 最終回退

    def get_precision(self, symbol: str) -> int:
        """獲取交易對的數量精度（支援動態幣對）"""
        # 1. 硬編碼表優先
        if symbol in self.DEFAULT_PRECISIONS:
            return self.DEFAULT_PRECISIONS[symbol]['amount']

        # 2. ccxt 市場資料（處理 int 和 float 兩種格式）
        if symbol in self.markets:
            precision = self.markets[symbol]['precision']['amount']
            if isinstance(precision, int):
                return precision
            if isinstance(precision, float) and precision > 0:
                return self._step_to_decimals(precision)

        # 3. 快取
        if symbol in self._exchange_info_cache:
            return self._exchange_info_cache[symbol]

        # 4. 從 Binance API 查詢（動態幣對）
        return self._fetch_binance_precision(symbol)

    def round_amount_up(self, symbol: str, amount: float, price: float) -> float:
        """
        向上取整數量，確保訂單價值滿足最小要求
        用於開倉時計算數量
        """
        import math

        precision = self.get_precision(symbol)
        multiplier = 10 ** precision

        # 向上取整到指定精度
        rounded = math.ceil(amount * multiplier) / multiplier

        # 檢查訂單價值是否滿足最小要求
        order_value = rounded * price
        min_notional = self.FUTURES_MIN_NOTIONAL if Config.TRADING_MODE == 'future' else 10

        if order_value < min_notional:
            # 計算滿足最小訂單價值所需的數量
            min_quantity = min_notional / price
            rounded = math.ceil(min_quantity * multiplier) / multiplier
            logger.info(f"⚠️ 調整數量以滿足最小訂單價值 ${min_notional}")

        return rounded

    def round_amount(self, symbol: str, amount: float) -> float:
        """向下取整數量（用於平倉等操作）"""
        precision = self.get_precision(symbol)
        amount_decimal = Decimal(str(amount))
        multiplier = Decimal(10) ** precision
        rounded = (amount_decimal * multiplier).quantize(Decimal('1'), rounding=ROUND_DOWN) / multiplier
        return float(rounded)

    def get_min_amount(self, symbol: str) -> float:
        """v5.2 新增：獲取交易對的最小交易數量"""
        if symbol in self.DEFAULT_PRECISIONS:
            return self.DEFAULT_PRECISIONS[symbol].get('min_amount', 0.001)
        return 0.001

    def check_limits(self, symbol: str, amount: float, price: float) -> bool:
        """檢查訂單是否滿足限制"""
        min_notional = self.FUTURES_MIN_NOTIONAL if Config.TRADING_MODE == 'future' else 10

        if symbol not in self.markets and self.use_default_precision:
            if symbol in self.DEFAULT_PRECISIONS:
                defaults = self.DEFAULT_PRECISIONS[symbol]
                if amount < defaults['min_amount']:
                    logger.warning(f"{symbol} 數量 {amount} 小於最小值 {defaults['min_amount']}")
                    return False
                cost = amount * price
                if cost < min_notional:
                    logger.warning(f"{symbol} 金額 ${cost:.2f} 小於最小值 ${min_notional}")
                    return False
            return True

        if symbol not in self.markets:
            # 即使沒有市場信息，也要檢查最小訂單價值
            cost = amount * price
            if cost < min_notional:
                logger.warning(f"{symbol} 金額 ${cost:.2f} 小於最小值 ${min_notional}")
                return False
            return True

        market = self.markets[symbol]
        limits = market['limits']

        if limits['amount']['min'] and amount < limits['amount']['min']:
            logger.warning(f"{symbol} 數量小於最小值")
            return False

        cost = amount * price
        # 使用 Futures 的最小訂單價值
        actual_min_cost = max(limits['cost']['min'] or 0, min_notional)
        if cost < actual_min_cost:
            logger.warning(f"{symbol} 金額 ${cost:.2f} 小於最小值 ${actual_min_cost}")
            return False

        return True


# ==================== 風險管理（v5.1 增強版）====================
class RiskManager:
    """風險管理類（v5.1 增強版）"""

    def __init__(self, exchange, precision_handler):
        self.exchange = exchange
        self.precision_handler = precision_handler

        # Binance Futures Testnet API 設定
        self.futures_base_url = "https://testnet.binancefuture.com"

    def _futures_signed_request(self, method: str, endpoint: str, params: dict = None) -> requests.Response:
        """統一 Binance Futures Testnet HMAC 簽名 + 請求"""
        import hmac as hmac_mod
        import hashlib
        from urllib.parse import urlencode

        if params is None:
            params = {}

        params['timestamp'] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac_mod.new(
            Config.API_SECRET.strip().encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature

        headers = {'X-MBX-APIKEY': Config.API_KEY}
        url = f"{self.futures_base_url}{endpoint}"

        if method == 'POST':
            return requests.post(url, data=params, headers=headers, timeout=30)
        elif method == 'DELETE':
            return requests.delete(url, params=params, headers=headers, timeout=30)
        else:
            return requests.get(url, params=params, headers=headers, timeout=30)

    def _get_futures_balance(self) -> float:
        """
        使用 /fapi/v2/balance 端點獲取 Futures 餘額
        解決 Binance Futures Testnet 不支援 sapi 端點的問題
        """
        try:
            response = self._futures_signed_request('GET', '/fapi/v2/balance')

            if response.status_code == 200:
                data = response.json()
                for asset in data:
                    if asset.get('asset') == 'USDT':
                        return float(asset.get('availableBalance', 0))
                return 0
            else:
                logger.error(f"Futures API 錯誤: {response.status_code} - {response.text}")
                return 0

        except Exception as e:
            logger.error(f"獲取 Futures 餘額失敗: {e}")
            return 0

    def get_balance(self) -> float:
        """獲取帳戶餘額"""
        for attempt in range(Config.MAX_RETRY):
            try:
                # 如果是 Binance Futures Testnet，使用專用 API
                if Config.SANDBOX_MODE and Config.TRADING_MODE == 'future' and Config.EXCHANGE == 'binance':
                    balance = self._get_futures_balance()
                    if balance > 0:
                        return balance
                    # 如果失敗，繼續嘗試
                    if attempt < Config.MAX_RETRY - 1:
                        time.sleep(Config.RETRY_DELAY)
                        continue
                    return 0
                else:
                    # 正式網或現貨模式使用原本的方法
                    balance = self.exchange.fetch_balance()
                    return balance['USDT']['free']

            except ccxt.NetworkError as e:
                logger.warning(f"網絡錯誤，重試 {attempt+1}/{Config.MAX_RETRY}")
                time.sleep(Config.RETRY_DELAY)
            except Exception as e:
                logger.error(f"獲取餘額失敗: {e}")
                if attempt < Config.MAX_RETRY - 1:
                    time.sleep(Config.RETRY_DELAY)
                else:
                    return 0
        return 0

    def get_positions(self) -> list:
        """獲取現有持倉"""
        try:
            if Config.SANDBOX_MODE and Config.TRADING_MODE == 'future' and Config.EXCHANGE == 'binance':
                return self._get_futures_positions()
            else:
                positions = self.exchange.fetch_positions()
                return [p for p in positions if float(p.get('contracts', 0)) != 0]
        except Exception as e:
            logger.error(f"獲取持倉失敗: {e}")
            return []

    def _get_futures_positions(self) -> list:
        """使用 Binance Futures API 獲取持倉"""
        try:
            response = self._futures_signed_request('GET', '/fapi/v2/positionRisk')

            if response.status_code == 200:
                data = response.json()
                # 只返回有倉位的
                return [p for p in data if float(p.get('positionAmt', 0)) != 0]
            else:
                logger.error(f"獲取持倉 API 錯誤: {response.status_code} - {response.text}")
                return []
        except Exception as e:
            logger.error(f"獲取 Futures 持倉失敗: {e}")
            return []

    def get_account_info(self) -> dict:
        """獲取完整帳戶資訊（餘額 + 倉位）"""
        return {
            'balance': self.get_balance(),
            'positions': self.get_positions()
        }

    def calculate_position_size(self, symbol: str, balance: float,
                               entry_price: float, stop_loss: float,
                               tier_multiplier: float = 1.0) -> float:
        """計算倉位大小（v5.1: 加入分級乘數，確保滿足最小訂單價值，加入倉位上限保護）"""
        risk_amount = balance * Config.RISK_PER_TRADE
        stop_dist_percent = abs(entry_price - stop_loss) / entry_price

        if stop_dist_percent == 0:
            return 0

        position_value = risk_amount / stop_dist_percent

        # 倉位上限保護：確保倉位價值不超過帳戶餘額的指定比例（考慮槓桿）
        max_position_value = balance * Config.MAX_POSITION_PERCENT * Config.LEVERAGE
        if position_value > max_position_value:
            logger.warning(f"⚠️ {symbol} 倉位超過上限，從 ${position_value:.2f} 調整為 ${max_position_value:.2f}")
            position_value = max_position_value

        raw_position = position_value / entry_price

        # v5.1: 根據信號等級調整倉位
        raw_position *= tier_multiplier

        # 使用向上取整並確保滿足最小訂單價值
        rounded_position = self.precision_handler.round_amount_up(symbol, raw_position, entry_price)

        # 再次檢查限制（雖然 round_amount_up 已經確保了，但作為安全檢查）
        if not self.precision_handler.check_limits(symbol, rounded_position, entry_price):
            return 0

        order_value = rounded_position * entry_price
        logger.info(f"💰 {symbol} 倉位: {rounded_position:.6f} (訂單價值: ${order_value:.2f}, 等級乘數: {tier_multiplier})")
        return rounded_position

    def calculate_stop_loss(self, extreme_point: float, atr: float, side: str, df: pd.DataFrame = None) -> float:
        """計算止損價位（v5.1: 動態 ATR 乘數）"""
        # v5.1: 使用動態 ATR 乘數
        atr_mult = DynamicThresholdManager.get_atr_multiplier(df) if df is not None else Config.ATR_MULTIPLIER
        
        if side == 'LONG':
            return extreme_point - (atr * atr_mult)
        else:
            return extreme_point + (atr * atr_mult)

    def check_total_risk(self, active_positions: List) -> bool:
        """
        計算所有持倉的實際剩餘風險
        - 考慮信號等級乘數（B=0.7, C=0.5）
        - 考慮已減倉的部位（current_size < position_size）
        - 止損已移至獲利區的部位，風險計為 0
        """
        if not active_positions:
            return True

        total_risk = 0.0

        for trade in active_positions:
            # 如果部位已關閉，跳過
            if trade.is_closed:
                continue

            # 計算該部位的實際下行風險
            # 剩餘倉位 × (入場價 - 當前止損) = 實際可能虧損金額
            if trade.side == 'LONG':
                risk_per_unit = trade.entry_price - trade.current_sl
            else:
                risk_per_unit = trade.current_sl - trade.entry_price

            # 如果止損已在獲利區（risk_per_unit < 0），風險為 0
            if risk_per_unit <= 0:
                continue

            # 實際風險金額 = 剩餘數量 × 每單位風險
            actual_risk_amount = trade.current_size * risk_per_unit

            total_risk += actual_risk_amount

        # 需要取得餘額來計算風險百分比
        balance = self.get_balance()
        if balance <= 0:
            return False

        total_risk_pct = total_risk / balance
        return total_risk_pct <= Config.MAX_TOTAL_RISK


# ==================== 交易管理 ====================
class TradeManager:
    """單筆交易管理類（v5.3 統一出場 SOP + 硬止損）"""

    # Binance Futures Testnet API 設定
    FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"

    def __init__(self, symbol: str, side: str, entry_price: float, stop_loss: float,
                 position_size: float, exchange, precision_handler, target_ref: float = None,
                 signal_tier: str = 'B'):
        self.symbol = symbol
        self.side = side
        self.entry_price = entry_price
        self.initial_sl = stop_loss
        self.current_sl = stop_loss
        self.position_size = position_size
        self.current_size = position_size
        self.exchange = exchange
        self.precision_handler = precision_handler
        self.target_ref = target_ref
        self.signal_tier = signal_tier

        self.is_closed = False

        # 硬止損單 ID
        self.stop_loss_order_id = None

        # v5.3 統一出場 SOP 狀態追蹤
        self.is_1r_protected = False     # 是否已執行 1R 移損
        self.is_first_partial = False    # 是否已執行 1.5R 減倉
        self.is_second_partial = False   # 是否已執行 2.5R 減倉
        self.is_trailing_active = False  # 是否已啟動追蹤止損
        self.entry_time = datetime.now(timezone.utc)  # 入場時間（用於時間退出）

        self.highest_price = entry_price  # 追蹤用：歷史最高價 (LONG)
        self.lowest_price = entry_price   # 追蹤用：歷史最低價 (SHORT)
        self.atr = None                   # 儲存 ATR 用於追蹤止損

        risk_dist = abs(entry_price - stop_loss)
        self.risk_dist = risk_dist  # 保存風險距離

        # 計算參考目標價（僅用於日誌顯示）
        if side == 'LONG':
            self.r15_target = entry_price + (risk_dist * 1.5)
            self.r25_target = entry_price + (risk_dist * 2.5)
        else:
            self.r15_target = entry_price - (risk_dist * 1.5)
            self.r25_target = entry_price - (risk_dist * 2.5)

        # v5.3: 統一日誌格式
        logger.info(f"🚀 {symbol} {side} 交易建立 (等級: {signal_tier})")
        logger.info(f"   ├─ 入場: ${entry_price:.2f}")
        logger.info(f"   ├─ 止損: ${stop_loss:.2f}")
        logger.info(f"   ├─ 倉位: {position_size:.6f}")
        logger.info(f"   ├─ 1.0R: 移損至 +0.3R")
        logger.info(f"   ├─ 1.5R: ${self.r15_target:.2f} (減{Config.FIRST_PARTIAL_PCT}%)")
        logger.info(f"   ├─ 2.5R: ${self.r25_target:.2f} (減{Config.SECOND_PARTIAL_PCT}%)")
        logger.info(f"   └─ 尾倉: ATR 追蹤止損")

        # 開倉後立即設置硬止損單
        if Config.USE_HARD_STOP_LOSS:
            self._place_hard_stop_loss()

    def _is_binance_futures_testnet(self) -> bool:
        """檢查是否為 Binance Futures Testnet"""
        return (Config.SANDBOX_MODE and
                Config.TRADING_MODE == 'future' and
                Config.EXCHANGE == 'binance')

    def _futures_signed_request(self, method: str, endpoint: str, params: dict) -> requests.Response:
        """統一 Binance Futures Testnet HMAC 簽名 + 請求"""
        import hmac as hmac_mod
        import hashlib
        from urllib.parse import urlencode

        params['timestamp'] = int(time.time() * 1000)
        query_string = urlencode(params)
        signature = hmac_mod.new(
            Config.API_SECRET.strip().encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature

        headers = {'X-MBX-APIKEY': Config.API_KEY}
        url = f"{self.FUTURES_TESTNET_URL}{endpoint}"

        if method == 'POST':
            return requests.post(url, data=params, headers=headers, timeout=30)
        elif method == 'DELETE':
            return requests.delete(url, params=params, headers=headers, timeout=30)
        else:
            return requests.get(url, params=params, headers=headers, timeout=30)

    def _place_hard_stop_loss(self) -> bool:
        """v5.2: 在交易所端設置硬止損單"""
        try:
            if not self._is_binance_futures_testnet():
                # 非 Binance Futures Testnet 使用 ccxt
                stop_side = 'sell' if self.side == 'LONG' else 'buy'
                order = self.exchange.create_order(
                    symbol=self.symbol,
                    type='STOP_MARKET',
                    side=stop_side,
                    amount=self.current_size,
                    params={
                        'stopPrice': self.current_sl,
                        'reduceOnly': True
                    }
                )
                self.stop_loss_order_id = order.get('id')
            else:
                # Binance Futures Testnet 使用直接 API
                symbol_id = self.symbol.replace('/', '')
                stop_side = 'SELL' if self.side == 'LONG' else 'BUY'

                precision = self.precision_handler.get_precision(self.symbol)
                if precision == 0:
                    formatted_quantity = str(int(self.current_size))
                else:
                    formatted_quantity = f"{self.current_size:.{precision}f}"

                params = {
                    'symbol': symbol_id,
                    'side': stop_side,
                    'type': 'STOP_MARKET',
                    'algoType': 'CONDITIONAL',
                    'quantity': formatted_quantity,
                    'triggerPrice': f"{self.current_sl:.2f}",
                    'reduceOnly': 'true',
                }

                response = self._futures_signed_request('POST', '/fapi/v1/algoOrder', params)

                if response.status_code == 200:
                    result = response.json()
                    self.stop_loss_order_id = result.get('algoId')
                    logger.info(f"🛡 {self.symbol} 硬止損單已設置 @ ${self.current_sl:.2f} (ID: {self.stop_loss_order_id})")
                    return True
                else:
                    logger.error(f"❌ 硬止損單設置失敗: {response.status_code} - {response.text}")
                    return False

            logger.info(f"🛡 {self.symbol} 硬止損單已設置 @ ${self.current_sl:.2f}")
            return True

        except Exception as e:
            logger.error(f"❌ 設置硬止損單失敗: {e}")
            return False

    def _cancel_stop_loss_order(self) -> bool:
        """v5.2: 取消現有的硬止損單"""
        if not self.stop_loss_order_id:
            return True

        try:
            if not self._is_binance_futures_testnet():
                self.exchange.cancel_order(self.stop_loss_order_id, self.symbol)
            else:
                symbol_id = self.symbol.replace('/', '')
                params = {
                    'symbol': symbol_id,
                    'algoId': self.stop_loss_order_id,
                }

                response = self._futures_signed_request('DELETE', '/fapi/v1/algoOrder', params)

                if response.status_code == 200:
                    logger.info(f"✅ {self.symbol} 已取消舊止損單 (ID: {self.stop_loss_order_id})")
                else:
                    # 止損單可能已被觸發或不存在，忽略錯誤
                    logger.debug(f"取消止損單: {response.status_code} - {response.text}")

            self.stop_loss_order_id = None
            return True

        except Exception as e:
            logger.warning(f"⚠️ 取消止損單失敗: {e}")
            self.stop_loss_order_id = None
            return False

    def _update_hard_stop_loss(self, new_stop: float) -> bool:
        """v5.2: 更新硬止損單（取消舊的，設置新的）"""
        if not Config.USE_HARD_STOP_LOSS:
            return True

        old_sl = self.current_sl
        self.current_sl = new_stop

        # 取消舊止損單
        self._cancel_stop_loss_order()

        # 設置新止損單
        success = self._place_hard_stop_loss()

        if success:
            logger.info(f"🔄 {self.symbol} 止損已更新: ${old_sl:.2f} -> ${new_stop:.2f}")
        else:
            logger.warning(f"⚠️ {self.symbol} 止損更新失敗，保持軟止損 @ ${new_stop:.2f}")

        return success

    def _futures_close_position(self, quantity: float) -> dict:
        """直接使用 Binance Futures Testnet API 平倉"""
        symbol_id = self.symbol.replace('/', '')
        close_side = 'SELL' if self.side == 'LONG' else 'BUY'

        precision = self.precision_handler.get_precision(self.symbol)
        if precision == 0:
            formatted_quantity = str(int(quantity))
        else:
            formatted_quantity = f"{quantity:.{precision}f}"

        params = {
            'symbol': symbol_id,
            'side': close_side,
            'type': 'MARKET',
            'quantity': formatted_quantity,
            'reduceOnly': 'true',
        }

        response = self._futures_signed_request('POST', '/fapi/v1/order', params)

        if response.status_code == 200:
            return response.json()
        else:
            raise Exception(f"平倉 API 錯誤: {response.status_code} - {response.text}")

    def monitor(self, current_price: float, df_1h: pd.DataFrame = None) -> str:
        """
        監控盈虧與統一出場 SOP（v5.3 重構版）
        所有信號等級共用同一套出場流程，等級只影響入場倉位大小。
        """
        if self.is_closed:
            return "CLOSED"

        # 更新歷史極值（用於追蹤止損）
        if self.side == 'LONG':
            self.highest_price = max(self.highest_price, current_price)
        else:
            self.lowest_price = min(self.lowest_price, current_price)

        # 更新 ATR（如果有數據）
        if df_1h is not None and 'atr' in df_1h.columns and len(df_1h) > 0:
            self.atr = df_1h['atr'].iloc[-1]

        # ========== 1. 止損檢查 ==========
        if self.side == 'LONG':
            if current_price <= self.current_sl:
                logger.warning(f"🚨 {self.symbol} 觸發止損 @ ${current_price:.2f}")
                self._cancel_stop_loss_order()
                self.close_position(percent=100, reason="止損出場", price=current_price)
                TelegramNotifier.notify_action(self.symbol, "止損出場", current_price)
                return "CLOSED"
        else:
            if current_price >= self.current_sl:
                logger.warning(f"🚨 {self.symbol} 觸發止損 @ ${current_price:.2f}")
                self._cancel_stop_loss_order()
                self.close_position(percent=100, reason="止損出場", price=current_price)
                TelegramNotifier.notify_action(self.symbol, "止損出場", current_price)
                return "CLOSED"

        # ========== 2. 結構破壞檢查 ==========
        if df_1h is not None and Config.ENABLE_STRUCTURE_BREAK_EXIT:
            if TechnicalAnalysis.check_structure_break(df_1h, current_price, self.side):
                logger.warning(f"⚠️ {self.symbol} 結構破壞，全部出場")
                self._cancel_stop_loss_order()
                self.close_position(percent=100, reason="結構破壞", price=current_price)
                TelegramNotifier.notify_action(self.symbol, "結構破壞", current_price)
                return "CLOSED"

        # ========== 3. 計算當前 R 值 ==========
        r_unit = abs(self.entry_price - self.initial_sl)
        if r_unit == 0:
            return "ACTIVE"

        if self.side == 'LONG':
            current_r = (current_price - self.entry_price) / r_unit
        else:
            current_r = (self.entry_price - current_price) / r_unit

        # ========== 4. 時間退出檢查 ==========
        hours_held = (datetime.now(timezone.utc) - self.entry_time).total_seconds() / 3600
        if hours_held >= Config.MAX_HOLD_HOURS and not self.is_first_partial:
            # 未達第一次減倉目標，時間退出
            logger.warning(f"⏰ {self.symbol} 持倉 {hours_held:.1f}H 未達 1.5R，時間退出")
            self._cancel_stop_loss_order()
            self.close_position(percent=100, reason="時間退出", price=current_price)
            TelegramNotifier.notify_action(self.symbol, "時間退出",
                current_price, f"持倉 {hours_held:.1f}H 未達 1.5R")
            return "CLOSED"

        # ========== 5. 統一出場 SOP ==========

        # 5a. 2.5R 第二次減倉（優先檢查，避免跳過）
        if not self.is_second_partial and current_r >= 2.5:
            if not self.is_first_partial:
                # 跳過了 1.5R，合併減倉
                reduce_pct = Config.FIRST_PARTIAL_PCT + Config.SECOND_PARTIAL_PCT
                self.is_first_partial = True
                logger.info(f"⚡ {self.symbol} 跳過1.5R，執行合併減倉{reduce_pct}%")
            else:
                reduce_pct = Config.SECOND_PARTIAL_PCT

            self.close_position(percent=reduce_pct, reason="2.5R減倉", price=current_price)
            self.is_second_partial = True
            self.is_1r_protected = True

            # 移損到 +1.5R
            if self.side == 'LONG':
                new_sl = self.entry_price + (r_unit * 1.5)
            else:
                new_sl = self.entry_price - (r_unit * 1.5)
            self._update_hard_stop_loss(new_sl)
            self.is_trailing_active = True

            logger.info(f"🎯 {self.symbol} 2.5R達成！止損移至 +1.5R (${new_sl:.2f})，啟動追蹤止損")
            TelegramNotifier.notify_action(self.symbol, "2.5R減倉+追蹤", current_price,
                                          f"新止損: ${new_sl:.2f}")

        # 5b. 1.5R 第一次減倉
        elif not self.is_first_partial and current_r >= 1.5:
            self.close_position(percent=Config.FIRST_PARTIAL_PCT, reason="1.5R減倉", price=current_price)
            self.is_first_partial = True
            self.is_1r_protected = True

            # 移損到 +0.5R
            if self.side == 'LONG':
                new_sl = self.entry_price + (r_unit * 0.5)
            else:
                new_sl = self.entry_price - (r_unit * 0.5)
            self._update_hard_stop_loss(new_sl)

            logger.info(f"🎯 {self.symbol} 1.5R達成！減倉{Config.FIRST_PARTIAL_PCT}%，止損移至 +0.5R (${new_sl:.2f})")
            TelegramNotifier.notify_action(self.symbol, "1.5R減倉+移損", current_price,
                                          f"新止損: ${new_sl:.2f}")

        # 5c. 1.0R 移損保護（不減倉）
        elif not self.is_1r_protected and current_r >= 1.0:
            if self.side == 'LONG':
                new_sl = self.entry_price + (r_unit * 0.3)
            else:
                new_sl = self.entry_price - (r_unit * 0.3)
            self._update_hard_stop_loss(new_sl)
            self.is_1r_protected = True

            logger.info(f"🛡 {self.symbol} 1.0R達成，移損至 +0.3R @ ${new_sl:.2f}")
            TelegramNotifier.notify_action(self.symbol, "1R移損保護", current_price,
                                          f"止損: ${new_sl:.2f}")

        # 5d. 追蹤止損（2.5R 後啟動）
        if self.is_trailing_active and self.atr is not None:
            trailing_distance = self.atr * Config.APLUS_TRAILING_ATR_MULT

            if self.side == 'LONG':
                new_trailing_sl = self.highest_price - trailing_distance
                if new_trailing_sl > self.current_sl:
                    old_sl = self.current_sl
                    self._update_hard_stop_loss(new_trailing_sl)
                    logger.info(f"📈 {self.symbol} 追蹤止損: ${old_sl:.2f} → ${new_trailing_sl:.2f}")
            else:
                new_trailing_sl = self.lowest_price + trailing_distance
                if new_trailing_sl < self.current_sl:
                    old_sl = self.current_sl
                    self._update_hard_stop_loss(new_trailing_sl)
                    logger.info(f"📉 {self.symbol} 追蹤止損: ${old_sl:.2f} → ${new_trailing_sl:.2f}")

        return "ACTIVE"

    def close_position(self, percent: int, reason: str, price: float):
        """v5.2 增強版：關閉部分或全部倉位"""
        # v5.2: 小倉位保護
        if self.current_size <= 0:
            logger.warning(f"⚠️ {self.symbol} 倉位已為零，跳過平倉")
            return

        try:
            close_amount = self.current_size * (percent / 100)
            close_amount_rounded = self.precision_handler.round_amount(self.symbol, close_amount)

            if Config.TRADING_MODE == 'spot':
                if self.side == 'LONG':
                    self.exchange.create_market_sell_order(self.symbol, close_amount_rounded)
                else:
                    self.exchange.create_market_buy_order(self.symbol, close_amount_rounded)
            else:
                # 使用直接 API 調用（繞過 ccxt 對 Binance Futures Testnet 的限制）
                if self._is_binance_futures_testnet():
                    self._futures_close_position(close_amount_rounded)
                else:
                    close_side = 'sell' if self.side == 'LONG' else 'buy'
                    self.exchange.create_order(
                        symbol=self.symbol,
                        type='market',
                        side=close_side,
                        amount=close_amount_rounded
                    )

            logger.info(f"✅ {self.symbol} {reason}: 平倉 {percent}% @ ${price:.2f}")

            self.current_size -= close_amount_rounded

            if percent >= 100:
                self.is_closed = True
                # 全部平倉時取消硬止損單
                self._cancel_stop_loss_order()
            elif Config.USE_HARD_STOP_LOSS and self.stop_loss_order_id:
                # 部分平倉時更新硬止損單的數量
                self._cancel_stop_loss_order()
                if self.current_size > 0:
                    self._place_hard_stop_loss()

        except Exception as e:
            logger.error(f"❌ 平倉失敗: {e}")


# ==================== 主交易機器人（v5.3 出場重構版）====================
class TradingBotV53:
    """v5.3 出場機制重構版交易機器人"""

    # Binance Futures Testnet API 設定
    FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"

    def __init__(self):
        self.exchange = self.init_exchange()
        self.precision_handler = PrecisionHandler(self.exchange)
        self.risk_manager = RiskManager(self.exchange, self.precision_handler)
        self.active_trades: Dict[str, TradeManager] = {}

        self.trend_cache = {}
        self.last_trend_check = {}

        logger.info("="*60)
        logger.info("🤖 交易機器人 v5.3 出場重構版已啟動")
        logger.info("="*60)
        logger.info(f"📊 交易模式: {Config.TRADING_MODE} ({Config.TRADING_DIRECTION})")
        logger.info(f"⚡ 槓桿: {Config.LEVERAGE}x")
        logger.info(f"💰 風險配置: {Config.RISK_PER_TRADE*100}% / {Config.MAX_TOTAL_RISK*100}%")
        logger.info("-"*60)
        logger.info("🆕 v5.3 統一出場 SOP:")
        logger.info(f"   ├─ 1.0R 移損保護 → +0.3R")
        logger.info(f"   ├─ 1.5R 第一次減倉 {Config.FIRST_PARTIAL_PCT}% → 移損 +0.5R")
        logger.info(f"   ├─ 2.5R 第二次減倉 {Config.SECOND_PARTIAL_PCT}% → 移損 +1.5R + ATR trailing")
        logger.info(f"   ├─ 最大持倉時間: {Config.MAX_HOLD_HOURS}H")
        logger.info(f"   ├─ 追蹤止損 ATR 乘數: {Config.APLUS_TRAILING_ATR_MULT}")
        logger.info(f"   └─ 結構破壞出場: {'啟用' if Config.ENABLE_STRUCTURE_BREAK_EXIT else '關閉'}")
        logger.info("-"*60)
        logger.info(f"📋 入場功能:")
        logger.info(f"   ├─ 多時間框架確認: {'啟用' if Config.ENABLE_MTF_CONFIRMATION else '關閉'}")
        logger.info(f"   ├─ 動態閾值調整: {'啟用' if Config.ENABLE_DYNAMIC_THRESHOLDS else '關閉'}")
        logger.info(f"   ├─ 分級入場系統: {'啟用' if Config.ENABLE_TIERED_ENTRY else '關閉'}")
        logger.info(f"   └─ 硬止損單: {'啟用' if Config.USE_HARD_STOP_LOSS else '關閉'}")
        logger.info("-"*60)
        logger.info(f"🎯 市場過濾: {'啟用' if Config.ENABLE_MARKET_FILTER else '關閉'} (ADX≥{Config.ADX_THRESHOLD})")
        logger.info(f"🔥 量能分級: {'啟用' if Config.ENABLE_VOLUME_GRADING else '關閉'}")
        logger.info(f"📡 監控交易對: {', '.join(Config.SYMBOLS)}")
        logger.info("="*60)

    def init_exchange(self):
        """初始化交易所"""
        try:
            exchange_class = getattr(ccxt, Config.EXCHANGE)

            exchange_config = {
                'apiKey': Config.API_KEY,
                'secret': Config.API_SECRET,
                'enableRateLimit': True,
                'timeout': 30000,
                'options': {'defaultType': Config.TRADING_MODE}
            }

            exchange = exchange_class(exchange_config)

            if Config.SANDBOX_MODE:
                if Config.TRADING_MODE == 'future':
                    # Binance Futures Testnet 配置
                    base_url = 'https://testnet.binancefuture.com'
                    exchange.set_sandbox_mode(True)

                    # 更新所有 API URLs
                    if 'api' in exchange.urls:
                        for key in exchange.urls['api']:
                            if 'fapi' in str(exchange.urls['api'].get(key, '')).lower():
                                exchange.urls['api'][key] = exchange.urls['api'][key].replace(
                                    'fapi.binance.com', 'testnet.binancefuture.com'
                                )

                    # 設置其他選項
                    exchange.options['sandboxMode'] = True
                    exchange.options['defaultType'] = 'future'

                    logger.info(f"🧪 已連接到 Binance Futures Testnet")
                else:
                    try:
                        exchange.set_sandbox_mode(True)
                        logger.info(f"🧪 已啟用 {Config.EXCHANGE.upper()} 現貨測試網")
                    except Exception as e:
                        logger.warning(f"⚠️ 無法啟用沙盒模式: {e}")

            # 載入市場資訊
            try:
                logger.info("📊 正在載入市場資訊...")
                exchange.load_markets()
                logger.info(f"✅ 已載入 {len(exchange.markets)} 個交易對")
            except Exception as e:
                logger.warning(f"⚠️ 載入市場資訊失敗: {e}")
                logger.info("   將使用默認精度設置")

            if Config.TRADING_MODE == 'future':
                try:
                    for symbol in Config.SYMBOLS:
                        try:
                            exchange.set_leverage(Config.LEVERAGE, symbol)
                            logger.debug(f"✅ {symbol} 槓桿設置為 {Config.LEVERAGE}x")
                        except Exception as e:
                            logger.debug(f"⚠️ {symbol} 設置槓桿失敗: {e}")
                except Exception as e:
                    logger.warning(f"⚠️ 批量設置槓桿失敗: {e}")

            logger.info(f"✅ 已連接到 {Config.EXCHANGE} {'(測試網)' if Config.SANDBOX_MODE else '(正式網)'}")
            return exchange

        except Exception as e:
            logger.error(f"❌ 交易所初始化失敗: {e}")
            import traceback
            logger.error(f"詳細錯誤: {traceback.format_exc()}")
            raise

    def _futures_api_request(self, method: str, endpoint: str, params: dict = None, signed: bool = True) -> dict:
        """
        直接調用 Binance Futures Testnet API
        繞過 ccxt 的限制
        """
        import hmac
        import hashlib
        from urllib.parse import urlencode

        url = f"{self.FUTURES_TESTNET_URL}{endpoint}"

        if params is None:
            params = {}

        headers = {'X-MBX-APIKEY': Config.API_KEY}

        if signed:
            params['timestamp'] = int(time.time() * 1000)
            query_string = urlencode(params)
            signature = hmac.new(
                Config.API_SECRET.strip().encode('utf-8'),
                query_string.encode('utf-8'),
                hashlib.sha256
            ).hexdigest()
            params['signature'] = signature

        try:
            if method.upper() == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=30)
            elif method.upper() == 'POST':
                response = requests.post(url, data=params, headers=headers, timeout=30)
            elif method.upper() == 'DELETE':
                response = requests.delete(url, params=params, headers=headers, timeout=30)
            else:
                raise ValueError(f"不支持的 HTTP 方法: {method}")

            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"API 錯誤: {response.status_code} - {response.text}")
                return {"error": response.text, "code": response.status_code}

        except Exception as e:
            logger.error(f"API 請求失敗: {e}")
            return {"error": str(e)}

    def _futures_set_leverage(self, symbol: str, leverage: int) -> bool:
        """設置槓桿"""
        symbol_id = symbol.replace('/', '')
        result = self._futures_api_request('POST', '/fapi/v1/leverage', {
            'symbol': symbol_id,
            'leverage': leverage
        })
        return 'error' not in result

    def _futures_create_order(self, symbol: str, side: str, quantity: float) -> dict:
        """
        直接使用 Binance Futures Testnet API 下單
        """
        symbol_id = symbol.replace('/', '')

        # 先設置槓桿
        self._futures_set_leverage(symbol, Config.LEVERAGE)

        # 🔧 修復：根據交易對精度格式化數量
        precision = self.precision_handler.get_precision(symbol)
        if precision == 0:
            # 整數精度（如 SOL、DOGE、ADA）
            formatted_quantity = str(int(quantity))
        else:
            # 指定小數位精度
            formatted_quantity = f"{quantity:.{precision}f}"
        
        logger.info(f"📝 {symbol} 下單數量: {quantity} -> {formatted_quantity} (精度: {precision})")

        params = {
            'symbol': symbol_id,
            'side': side.upper(),
            'type': 'MARKET',
            'quantity': formatted_quantity  # 使用格式化後的字符串
        }

        result = self._futures_api_request('POST', '/fapi/v1/order', params)

        if 'error' in result:
            logger.error(f"❌ API 錯誤: {result.get('code', 'N/A')}")
            logger.error(f"   響應: {result['error']}")
            raise Exception(f"下單失敗: {result['error']}")

        return result

    def _futures_close_position(self, symbol: str, side: str, quantity: float) -> dict:
        """
        直接使用 Binance Futures Testnet API 平倉
        """
        symbol_id = symbol.replace('/', '')

        # 平倉方向相反
        close_side = 'SELL' if side == 'LONG' else 'BUY'

        # 🔧 修復：根據交易對精度格式化數量
        precision = self.precision_handler.get_precision(symbol)
        if precision == 0:
            formatted_quantity = str(int(quantity))
        else:
            formatted_quantity = f"{quantity:.{precision}f}"

        params = {
            'symbol': symbol_id,
            'side': close_side,
            'type': 'MARKET',
            'quantity': formatted_quantity,  # 使用格式化後的字符串
            'reduceOnly': 'true'
        }

        result = self._futures_api_request('POST', '/fapi/v1/order', params)

        if 'error' in result:
            raise Exception(f"平倉失敗: {result['error']}")

        return result

    def _is_binance_futures_testnet(self) -> bool:
        """檢查是否為 Binance Futures Testnet"""
        return (Config.SANDBOX_MODE and
                Config.TRADING_MODE == 'future' and
                Config.EXCHANGE == 'binance')

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """獲取 OHLCV 數據"""
        for attempt in range(Config.MAX_RETRY):
            try:
                ohlcv = None

                # 嘗試使用標準 fetch_ohlcv 方法
                try:
                    ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                except Exception as e:
                    logger.debug(f"標準方法失敗: {e}")

                    # 備用方案：直接調用 API（針對 Binance Futures Testnet）
                    if Config.TRADING_MODE == 'future' and Config.SANDBOX_MODE:
                        symbol_id = symbol.replace('/', '')

                        # 嘗試使用 requests 直接調用
                        import requests as req
                        base_url = 'https://testnet.binancefuture.com'
                        endpoint = f'{base_url}/fapi/v1/klines'
                        params = {
                            'symbol': symbol_id,
                            'interval': timeframe,
                            'limit': limit
                        }

                        response = req.get(endpoint, params=params, timeout=30)
                        if response.status_code == 200:
                            data = response.json()
                            ohlcv = []
                            for candle in data:
                                ohlcv.append([
                                    int(candle[0]),
                                    float(candle[1]),
                                    float(candle[2]),
                                    float(candle[3]),
                                    float(candle[4]),
                                    float(candle[5]),
                                ])
                        else:
                            logger.error(f"API 請求失敗: {response.status_code} - {response.text}")
                            raise Exception(f"API 錯誤: {response.status_code}")

                if ohlcv is None or len(ohlcv) == 0:
                    logger.warning(f"⚠️ {symbol} {timeframe} 無數據返回")
                    return pd.DataFrame()

                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')

                logger.debug(f"✅ {symbol} {timeframe} 獲取 {len(df)} 根K線")
                return df

            except ccxt.NetworkError as e:
                if attempt < Config.MAX_RETRY - 1:
                    wait_time = Config.RETRY_DELAY * (attempt + 1)
                    logger.warning(f"🔄 {symbol} 網絡錯誤，等待 {wait_time} 秒後重試...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"❌ {symbol} 網絡錯誤（已重試 {Config.MAX_RETRY} 次）: {e}")
            except ccxt.ExchangeError as e:
                logger.error(f"❌ {symbol} 交易所錯誤: {e}")
                break
            except Exception as e:
                logger.error(f"❌ {symbol} 獲取數據失敗: {e}")
                import traceback
                logger.debug(f"詳細錯誤: {traceback.format_exc()}")
                if attempt < Config.MAX_RETRY - 1:
                    time.sleep(Config.RETRY_DELAY)
                else:
                    break

        return pd.DataFrame()

    def load_scanner_results(self) -> List[str]:
        """
        從 Scanner 結果載入動態標的清單
        如果 Scanner 沒有結果，返回預設標的池
        """
        try:
            scanner_path = Config.SCANNER_JSON_PATH

            if not os.path.exists(scanner_path):
                logger.warning("⚠️ 找不到 Scanner 結果檔案，使用預設標的")
                return Config.SYMBOLS

            with open(scanner_path, 'r') as f:
                data = json.load(f)

            # 檢查掃描時間是否過期
            scan_time_str = data.get('scan_time', '')
            if scan_time_str:
                try:
                    scan_time = datetime.fromisoformat(scan_time_str.replace('Z', '+00:00'))
                    age_minutes = (datetime.now(timezone.utc) - scan_time).total_seconds() / 60

                    if age_minutes > Config.SCANNER_MAX_AGE_MINUTES:
                        logger.warning(f"⚠️ Scanner 結果已過期 ({age_minutes:.0f} 分鐘前)，使用預設標的")
                        return Config.SYMBOLS
                except Exception:
                    pass

            # 提取標的清單
            hot_symbols = data.get('hot_symbols', [])

            if not hot_symbols:
                logger.warning("⚠️ Scanner 未掃出潛力標的，使用預設標的池")
                logger.info(f"   預設標的: {Config.SYMBOLS}")
                return Config.SYMBOLS

            # Scanner 作為選股器：所有掃出的標的都加入交易池
            # Bot 後續仍會執行完整的 2B / EMA 回撤 / 量能突破 信號偵測
            scanner_symbols = [item['symbol'] for item in hot_symbols if item.get('symbol')]

            if not scanner_symbols:
                logger.warning("⚠️ Scanner 結果無有效標的，使用預設標的池")
                return Config.SYMBOLS

            logger.info(f"✅ 從 Scanner 載入 {len(scanner_symbols)} 個動態標的")
            for symbol in scanner_symbols:
                logger.info(f"   📌 {symbol}")

            return scanner_symbols

        except Exception as e:
            logger.error(f"讀取 Scanner 結果失敗: {e}")
            return Config.SYMBOLS

    def scan_for_signals(self):
        """掃描交易信號（v5.2 增強版：多策略掃描 + Scanner 整合）"""
        # 根據配置決定使用哪個標的池
        if Config.USE_SCANNER_SYMBOLS:
            symbols_to_scan = self.load_scanner_results()
        else:
            symbols_to_scan = Config.SYMBOLS

        logger.info(f"🔍 開始掃描交易信號 | {len(symbols_to_scan)} 個標的")

        for symbol in symbols_to_scan:
            try:
                logger.debug(f"📊 正在分析 {symbol}...")
                logger.debug("-" * 60)

                # 檢查是否已有持倉
                if symbol in self.active_trades:
                    active_trade = self.active_trades[symbol]
                    logger.debug(f"⏭ 跳過原因: 已有 {active_trade.side} 持倉")
                    continue

                # 檢查總風險
                active_list = list(self.active_trades.values())
                if not self.risk_manager.check_total_risk(active_list):
                    logger.debug(f"🚫 總風險已達上限 ({len(active_list)} 個持倉)")
                    break

                # 獲取各時間框架數據
                df_trend = self.fetch_ohlcv(symbol, Config.TIMEFRAME_TREND, limit=250)
                df_signal = self.fetch_ohlcv(symbol, Config.TIMEFRAME_SIGNAL, limit=100)
                df_mtf = self.fetch_ohlcv(symbol, Config.TIMEFRAME_MTF, limit=100) if Config.ENABLE_MTF_CONFIRMATION else pd.DataFrame()

                if df_trend.empty or len(df_trend) < 100:
                    logger.warning(f"⚠️ 跳過原因: 趨勢數據不足")
                    continue
                if df_signal.empty or len(df_signal) < 50:
                    logger.warning(f"⚠️ 跳過原因: 信號數據不足")
                    continue

                df_trend = TechnicalAnalysis.calculate_indicators(df_trend)
                df_signal = TechnicalAnalysis.calculate_indicators(df_signal)
                if not df_mtf.empty:
                    df_mtf = TechnicalAnalysis.calculate_indicators(df_mtf)

                # v4 市場過濾（v5.1 增強：返回市場強度）
                market_ok, market_reason, is_strong_market = MarketFilter.check_market_condition(df_trend, symbol)
                if not market_ok:
                    logger.debug(f"🚫 市場過濾未通過: {market_reason}")
                    continue

                logger.debug(f"✅ 市場狀態: {market_reason}")

                # ========== v5.1: 多策略信號掃描 ==========
                signals_found = []

                # 策略 1: 原有的 2B 信號
                has_2b, details_2b = TechnicalAnalysis.detect_2B_signal(df_signal)
                if has_2b:
                    details_2b['signal_type'] = '2B'
                    signals_found.append(('2B 突破', details_2b))

                # 策略 2: EMA 回撤信號
                has_pullback, details_pullback = TechnicalAnalysis.detect_ema_pullback_signal(df_signal)
                if has_pullback:
                    signals_found.append(('EMA 回撤', details_pullback))

                # 策略 3: 量能突破信號
                has_breakout, details_breakout = TechnicalAnalysis.detect_volume_breakout_signal(df_signal)
                if has_breakout:
                    signals_found.append(('量能突破', details_breakout))

                if not signals_found:
                    logger.debug(f"⏭ 未檢測到任何信號")
                    continue

                # 選擇最佳信號（優先級：量能突破 > 2B > EMA 回撤）
                priority_order = {'量能突破': 1, '2B 突破': 2, 'EMA 回撤': 3}
                signals_found.sort(key=lambda x: priority_order.get(x[0], 99))
                
                best_signal_type, signal_details = signals_found[0]
                signal_side = signal_details['side']

                logger.info(f"🎯 發現 {best_signal_type} 信號！")
                logger.info(f"   ├─ 方向: {signal_side}")
                logger.info(f"   ├─ 量能: {signal_details.get('vol_ratio', 0):.2f}x")

                # 交易方向過濾
                trading_dir = Config.TRADING_DIRECTION.lower()
                if trading_dir == 'long' and signal_side != 'LONG':
                    logger.info(f"🚫 跳過: 當前僅做多模式")
                    continue
                if trading_dir == 'short' and signal_side != 'SHORT':
                    logger.info(f"🚫 跳過: 當前僅做空模式")
                    continue

                # 趨勢檢查
                trend_ok, trend_desc = TechnicalAnalysis.check_trend(df_trend, signal_side)
                if not trend_ok:
                    logger.info(f"❌ 趨勢檢查未通過: {trend_desc}")
                    continue

                # v5.1: MTF 確認（不作為硬性條件）
                mtf_aligned = True
                mtf_reason = "MTF 未啟用"
                if Config.ENABLE_MTF_CONFIRMATION and not df_mtf.empty:
                    mtf_aligned, mtf_reason = MTFConfirmation.check_mtf_alignment(df_mtf, signal_side)
                    logger.info(f"📊 MTF 確認: {mtf_reason}")

                # v5.1: 計算信號等級
                signal_tier, tier_multiplier = SignalTierSystem.calculate_signal_tier(
                    signal_details,
                    mtf_aligned,
                    is_strong_market,
                    signal_details.get('signal_strength', 'moderate')
                )
                
                signal_details['signal_tier'] = signal_tier
                logger.info(f"🏆 信號等級: {signal_tier} (倉位乘數: {tier_multiplier})")

                logger.info(f"✅ 趨勢確認: {trend_desc}")
                logger.info(f"🚀 準備執行 {signal_side} 交易...")

                self.execute_trade(symbol, signal_details, market_reason, tier_multiplier, df_signal)

            except Exception as e:
                logger.error(f"❌ {symbol} 掃描失敗: {e}")
                import traceback
                logger.debug(f"錯誤詳情: {traceback.format_exc()}")

        positions_str = ', '.join(f'{s}({t.side})' for s, t in self.active_trades.items()) if self.active_trades else "無"
        logger.info(f"✅ 掃描完成 | 持倉: {positions_str}")

    def execute_trade(self, symbol: str, signal_details: Dict, market_state: str,
                     tier_multiplier: float = 1.0, df_signal: pd.DataFrame = None):
        """v5.2 增強版：執行交易（防重複 + 分級倉位）"""
        try:
            # v5.2: 再次檢查是否已有持倉（防止重複開倉）
            if symbol in self.active_trades:
                logger.warning(f"⚠️ {symbol} 已有持倉，跳過開倉")
                return

            balance = self.risk_manager.get_balance()
            if balance <= 0:
                logger.error("❌ 餘額不足")
                return

            entry_price = signal_details['entry_price']
            side = signal_details['side']

            # 計算止損
            if side == 'LONG':
                extreme_point = signal_details.get('lowest_point', signal_details.get('stop_level'))
            else:
                extreme_point = signal_details.get('highest_point', signal_details.get('stop_level'))

            atr = signal_details['atr']
            stop_loss = self.risk_manager.calculate_stop_loss(extreme_point, atr, side, df_signal)
            target_ref = signal_details.get('target_ref')

            # v5.1: 計算分級倉位
            position_size = self.risk_manager.calculate_position_size(
                symbol, balance, entry_price, stop_loss, tier_multiplier
            )

            if position_size <= 0:
                logger.warning(f"⚠️ {symbol} 倉位計算失敗")
                return

            # 開倉
            if Config.TRADING_MODE == 'spot':
                if side == 'LONG':
                    order = self.exchange.create_market_buy_order(symbol, position_size)
                else:
                    logger.warning(f"⚠️ {symbol} 現貨模式不支持做空")
                    return
            else:
                order_side = 'BUY' if side == 'LONG' else 'SELL'

                # 使用直接 API 調用（繞過 ccxt 對 Binance Futures Testnet 的限制）
                if self._is_binance_futures_testnet():
                    order = self._futures_create_order(symbol, order_side, position_size)
                else:
                    order = self.exchange.create_order(
                        symbol=symbol,
                        type='market',
                        side=order_side.lower(),
                        amount=position_size
                    )

            logger.info(f"✅ {symbol} {side} 開倉成功！")
            logger.info("-" * 60)

            # 計算交易詳情
            risk_dist = abs(entry_price - stop_loss)
            risk_dist_pct = (risk_dist / entry_price) * 100
            position_value = position_size * entry_price
            risk_amount = position_size * risk_dist

            if side == 'LONG':
                r15_target = entry_price + (risk_dist * 1.5)
                r25_target = entry_price + (risk_dist * 2.5)
            else:
                r15_target = entry_price - (risk_dist * 1.5)
                r25_target = entry_price - (risk_dist * 2.5)

            # 顯示詳細開倉信息
            logger.info(f"📊 開倉詳情:")
            logger.info(f"   ├─ 方向: {side}")
            logger.info(f"   ├─ 信號類型: {signal_details.get('signal_type', '2B')}")
            logger.info(f"   ├─ 信號等級: {signal_details.get('signal_tier', 'B')}")
            logger.info(f"   ├─ 入場價: ${entry_price:.2f}")
            logger.info(f"   ├─ 止損價: ${stop_loss:.2f}")
            logger.info(f"   ├─ 止損距離: ${risk_dist:.2f} ({risk_dist_pct:.2f}%)")
            logger.info(f"   ├─ 倉位大小: {position_size:.6f}")
            logger.info(f"   ├─ 倉位價值: ${position_value:.2f}")
            logger.info(f"   └─ 風險金額: ${risk_amount:.2f}")
            logger.info(f"🎯 統一出場 SOP:")
            logger.info(f"   ├─ 1.5R 減倉: ${r15_target:.2f} (減{Config.FIRST_PARTIAL_PCT}%)")
            logger.info(f"   └─ 2.5R 減倉: ${r25_target:.2f} (減{Config.SECOND_PARTIAL_PCT}%+追蹤)")
            logger.info("-" * 60)

            # 創建交易管理器
            trade_manager = TradeManager(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                position_size=position_size,
                exchange=self.exchange,
                precision_handler=self.precision_handler,
                target_ref=target_ref,
                signal_tier=signal_details.get('signal_tier', 'B')
            )

            # v5.2: 傳遞 ATR 給 TradeManager（用於追蹤止損）
            trade_manager.atr = signal_details.get('atr')

            self.active_trades[symbol] = trade_manager

            # 發送 Telegram 通知
            TelegramNotifier.notify_signal(symbol, {
                **signal_details,
                'market_state': market_state,
                'position_size': position_size,
                'stop_loss': stop_loss,
                'r15_target': r15_target
            })

        except Exception as e:
            logger.error(f"❌ {symbol} 開倉失敗: {e}")
            import traceback
            logger.debug(f"錯誤詳情: {traceback.format_exc()}")

    def monitor_positions(self):
        """監控持倉"""
        if not self.active_trades:
            return

        logger.info(f"👁 監控 {len(self.active_trades)} 個持倉...")

        # v5.2: 同步交易所端實際持倉，防止硬止損觸發後 Bot 不知情
        try:
            exchange_positions = self.risk_manager.get_positions()
            exchange_symbols = set()
            for pos in exchange_positions:
                sym = pos.get('symbol', '')
                if not sym:
                    # Binance Futures 原始 API 返回 'BTCUSDT' 格式
                    sym = pos.get('info', {}).get('symbol', '')
                exchange_symbols.add(sym)

            for symbol, trade in list(self.active_trades.items()):
                symbol_id = symbol.replace('/', '')
                if symbol_id not in exchange_symbols and symbol not in exchange_symbols:
                    logger.warning(f"⚠️ {symbol} 交易所端已無持倉（硬止損可能已觸發），本地同步關閉")
                    trade.is_closed = True
        except Exception as e:
            logger.debug(f"持倉同步檢查失敗（非關鍵）: {e}")

        closed_symbols = []

        for symbol, trade in self.active_trades.items():
            try:
                if trade.is_closed:
                    closed_symbols.append(symbol)
                    logger.info(f"🔴 {symbol} 已被交易所端平倉")
                    continue

                logger.debug(f"📊 {symbol} ({trade.side}, 等級:{trade.signal_tier})")
                logger.debug("-" * 60)

                ticker = self.exchange.fetch_ticker(symbol)
                current_price = ticker['last']

                # 計算盈虧
                if trade.side == 'LONG':
                    profit = current_price - trade.entry_price
                    profit_pct = (profit / trade.entry_price) * 100
                    profit_r = profit / abs(trade.entry_price - trade.initial_sl)
                else:
                    profit = trade.entry_price - current_price
                    profit_pct = (profit / trade.entry_price) * 100
                    profit_r = profit / abs(trade.entry_price - trade.initial_sl)

                # 盈虧狀態
                if profit_pct >= 5:
                    status_emoji = "🟢"
                elif profit_pct >= 1:
                    status_emoji = "🟡"
                elif profit_pct >= -1:
                    status_emoji = "⚪"
                elif profit_pct >= -3:
                    status_emoji = "🟠"
                else:
                    status_emoji = "🔴"

                logger.debug(f"💰 當前: ${current_price:.2f} | 入場: ${trade.entry_price:.2f}")
                logger.debug(f"{status_emoji} 盈虧: ${profit:.2f} ({profit_pct:+.2f}%) | {profit_r:+.2f}R")

                # 獲取 1H 數據（追蹤止損需要 ATR，結構破壞需要完整指標）
                df_1h = None
                # v5.3: 統一 SOP 始終需要 ATR 數據
                df_1h = self.fetch_ohlcv(symbol, Config.TIMEFRAME_SIGNAL, limit=50)
                if not df_1h.empty:
                    df_1h = TechnicalAnalysis.calculate_indicators(df_1h)

                status = trade.monitor(current_price, df_1h)

                if status == "CLOSED":
                    closed_symbols.append(symbol)
                    logger.info(f"🔴 持倉已關閉")

            except Exception as e:
                logger.error(f"❌ {symbol} 監控失敗: {e}")

        for symbol in closed_symbols:
            del self.active_trades[symbol]

        logger.info(f"✅ 監控完成 | 剩餘: {len(self.active_trades)}")

    def startup_diagnostics(self):
        """啟動診斷：檢查連線和數據獲取"""
        logger.info("\n" + "="*60)
        logger.info("🔍 執行啟動診斷...")
        logger.info("="*60)

        # 1. 檢查 API 連線
        try:
            balance = self.risk_manager.get_balance()
            logger.info(f"✅ API 連線正常 | 餘額: ${balance:.2f} USDT")
        except Exception as e:
            logger.error(f"❌ API 連線失敗: {e}")
            logger.error("   請檢查 API Key 和 Secret 是否正確")
            return False

        # 2. 檢查數據獲取
        test_symbol = Config.SYMBOLS[0] if Config.SYMBOLS else 'BTC/USDT'
        logger.info(f"\n📊 測試數據獲取: {test_symbol}")

        timeframes = [
            (Config.TIMEFRAME_TREND, '趨勢'),
            (Config.TIMEFRAME_SIGNAL, '信號'),
        ]
        if Config.ENABLE_MTF_CONFIRMATION:
            timeframes.append((Config.TIMEFRAME_MTF, 'MTF'))

        all_ok = True
        for tf, name in timeframes:
            df = self.fetch_ohlcv(test_symbol, tf, limit=50)
            if df.empty:
                logger.error(f"   ❌ {name}數據 ({tf}): 無法獲取")
                all_ok = False
            else:
                latest_time = df['timestamp'].iloc[-1]
                logger.info(f"   ✅ {name}數據 ({tf}): {len(df)} 根K線 | 最新: {latest_time}")

        if not all_ok:
            logger.error("\n⚠️ 數據獲取存在問題，請檢查:")
            logger.error("   1. 交易對格式是否正確 (例: BTC/USDT)")
            logger.error("   2. 網路連線是否正常")
            logger.error("   3. 測試網是否可用")
            return False

        # 3. 顯示當前配置
        logger.info(f"\n📋 當前配置:")
        logger.info(f"   ├─ 交易所: {Config.EXCHANGE}")
        logger.info(f"   ├─ 模式: {Config.TRADING_MODE} ({'測試網' if Config.SANDBOX_MODE else '正式網'})")
        logger.info(f"   ├─ 方向: {Config.TRADING_DIRECTION}")
        logger.info(f"   ├─ 槓桿: {Config.LEVERAGE}x")
        logger.info(f"   └─ 交易對: {', '.join(Config.SYMBOLS)}")

        logger.info("\n" + "="*60)
        logger.info("✅ 啟動診斷完成，系統準備就緒！")
        logger.info("="*60 + "\n")
        return True

    def run(self, info_only: bool = False):
        """主運行循環

        Args:
            info_only: 若為 True，只獲取帳戶資訊後等待，不執行交易
        """
        # 執行啟動診斷
        if not self.startup_diagnostics():
            logger.error("❌ 啟動診斷失敗，機器人停止運行")
            return

        # 如果是 info_only 模式，輸出帳戶資訊後等待
        if info_only:
            logger.info("\n" + "="*60)
            logger.info("📊 帳戶資訊模式 - 等待交易指令")
            logger.info("="*60)

            account_info = self.risk_manager.get_account_info()
            balance = account_info['balance']
            positions = account_info['positions']

            # 輸出餘額
            logger.info(f"\n💰 帳戶餘額: {balance:.2f} USDT")

            # 輸出持倉
            if positions:
                logger.info(f"\n📋 現有持倉 ({len(positions)} 個):")
                for p in positions:
                    symbol = p.get('symbol', 'N/A')
                    amt = float(p.get('positionAmt', 0))
                    entry = float(p.get('entryPrice', 0))
                    pnl = float(p.get('unRealizedProfit', 0))
                    side = 'LONG' if amt > 0 else 'SHORT'
                    logger.info(f"   ├─ {symbol}: {side} {abs(amt):.4f} @ ${entry:.2f} | PnL: ${pnl:.2f}")
            else:
                logger.info("\n📋 目前無持倉")

            # 輸出 JSON 格式供 GUI 解析
            print(f"__ACCOUNT_INFO_JSON__:{json.dumps(account_info)}")

            logger.info("\n⏳ 等待交易指令...")

            # 等待 stdin 指令
            while True:
                try:
                    line = sys.stdin.readline().strip()
                    if line == "__START_TRADING__":
                        logger.info("✅ 收到交易指令，開始交易...")
                        break
                    elif line == "__STOP__":
                        logger.info("⏹ 收到停止指令")
                        return
                    elif line == "__REFRESH__":
                        account_info = self.risk_manager.get_account_info()
                        print(f"__ACCOUNT_INFO_JSON__:{json.dumps(account_info)}")
                    time.sleep(0.1)
                except KeyboardInterrupt:
                    logger.info("\n⏹ 用戶中斷")
                    return

        logger.info("🚀 機器人開始運行...\n")

        cycle_count = 0
        while True:
            try:
                cycle_count += 1
                logger.info(f"[循環 #{cycle_count}] 開始掃描...")

                self.scan_for_signals()
                self.monitor_positions()

                logger.info(f"😴 休息 {Config.CHECK_INTERVAL} 秒...\n")
                time.sleep(Config.CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("\n⏹ 用戶中斷，停止機器人")
                break
            except Exception as e:
                logger.error(f"❌ 運行循環 #{cycle_count} 中發生錯誤: {e}")
                logger.info(f"😴 休息 {Config.CHECK_INTERVAL} 秒後重試...\n")
                time.sleep(Config.CHECK_INTERVAL)


# ==================== 主程序入口 ====================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Trading Bot v5.3')
    parser.add_argument('--info-only', action='store_true',
                        help='只獲取帳戶資訊，不執行交易')
    args = parser.parse_args()

    try:
        # 首先載入配置
        Config.load_from_json("bot_config.json")

        bot = TradingBotV53()
        bot.run(info_only=args.info_only)
    except Exception as e:
        logger.error(f"❌ 機器人啟動失敗: {e}")
        raise
