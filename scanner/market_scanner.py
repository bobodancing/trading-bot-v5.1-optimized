# -*- coding: utf-8 -*-
"""
Crypto Market Scanner v1.0
==========================
主動掃描市場，找出最符合 2B 策略的 Top 10 標的

設計理念：
- Layer 1: 流動性過濾（排除垃圾幣）
- Layer 2: 動能篩選（找趨勢中的標的）
- Layer 3: 形態匹配（2B 信號 + 預警）
- Layer 4: 相關性過濾（分散風險）

整合建議來源：Claude + Gemini
"""

import ccxt
import pandas as pd
import pandas_ta as ta
import numpy as np
import json
import sqlite3
import logging
from logging.handlers import RotatingFileHandler
import time
import requests
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict
from enum import Enum

# 標記模組可用
SCANNER_AVAILABLE = True

# ==================== 配置 ====================
class ScannerConfig:
    """Scanner 配置"""
    
    # 交易所設置
    EXCHANGE = 'binance'
    MARKET_TYPE = 'future'  # 'future' 或 'spot'
    SANDBOX_MODE = False    # Scanner 永遠使用正式網
    
    # 掃描間隔
    SCAN_INTERVAL_MINUTES = 15
    
    # Layer 1: 流動性過濾
    L1_MIN_VOLUME_USD = 50_000_000  # 24H 最低成交量
    L1_EXCLUDED_SYMBOLS = ['USDC/USDT', 'BUSD/USDT', 'TUSD/USDT', 'DAI/USDT', 'FDUSD/USDT']
    L1_EXCLUDED_PATTERNS = ['UP/USDT', 'DOWN/USDT', 'BEAR/', 'BULL/', '3L/', '3S/']
    
    # Layer 2: 動能篩選
    L2_MIN_ADX = 20
    L2_RSI_RANGE = (40, 70)  # RSI 區間
    L2_MIN_CONDITIONS = 3     # 至少滿足 N 項條件
    L2_MIN_ATR_PERCENT = 1.5  # 最低 ATR% (避免橫盤)
    L2_MAX_ATR_PERCENT = 15   # 最高 ATR% (避免黑天鵝)
    
    # Layer 3: 形態匹配
    L3_SWING_LEFT_BARS = 5
    L3_SWING_RIGHT_BARS = 2
    L3_MIN_VOLUME_RATIO = 1.0
    L3_MAX_PENETRATION_ATR = 2.0
    L3_PRE_2B_THRESHOLD = 0.5  # 距離前高/低 0.5 ATR 內視為「潛在 2B」
    
    # Layer 4: 相關性過濾
    L4_MAX_CORRELATION = 0.7
    L4_MAX_PER_SECTOR = 2
    L4_CORRELATION_PERIOD = 30  # 計算相關性的天數
    
    # 輸出設置
    OUTPUT_TOP_N = 10
    OUTPUT_JSON_PATH = 'hot_symbols.json'
    OUTPUT_DB_PATH = 'scanner_results.db'
    
    # API 優化
    API_BATCH_SIZE = 50
    API_DELAY_BETWEEN_BATCHES = 1.0
    API_MAX_RETRIES = 3
    
    # Telegram 通知（可選）
    TELEGRAM_ENABLED = False
    TELEGRAM_BOT_TOKEN = ''
    TELEGRAM_CHAT_ID = ''
    
    # 時間框架
    TIMEFRAME_SCAN = '1h'      # 掃描用時間框架
    TIMEFRAME_TREND = '4h'     # 趨勢確認時間框架
    TIMEFRAME_DAILY = '1d'     # 日線確認
    
    @classmethod
    def load_from_json(cls, config_file: str = None):
        """從 JSON 載入配置"""
        if config_file is None:
            # 嘗試多個路徑
            possible_paths = [
                'scanner_config.json',
                'scanner/scanner_config.json',
                os.path.join(os.path.dirname(__file__), 'scanner_config.json')
            ]
            for path in possible_paths:
                if os.path.exists(path):
                    config_file = path
                    break
        
        if config_file is None or not os.path.exists(config_file):
            if not getattr(cls, '_config_loaded', False):
                logger.info("⚠️ 未找到 scanner_config.json，使用預設配置（正式網）")
            return

        # 防止重複載入
        if getattr(cls, '_config_loaded', False) and config_file is None:
            return

        try:
            with open(config_file, 'r') as f:
                data = json.load(f)

            for key, value in data.items():
                attr_name = key.upper()
                if hasattr(cls, attr_name):
                    setattr(cls, attr_name, value)

            cls._config_loaded = True

            # 強制提醒 Scanner 使用的網路
            network = "測試網" if cls.SANDBOX_MODE else "正式網"
            logger.info(f"✅ 從 {config_file} 載入配置")
            logger.info(f"📡 Scanner 網路模式: {network}")
            logger.info(f"📊 Layer 1 成交量門檻: ${cls.L1_MIN_VOLUME_USD:,.0f}")
        except Exception as e:
            logger.error(f"載入配置失敗: {e}")


# ==================== 日誌設置 ====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        RotatingFileHandler('scanner.log', maxBytes=5*1024*1024, backupCount=2, encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


# ==================== 數據結構 ====================
class SignalSide(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    NEUTRAL = "NEUTRAL"


class SignalType(Enum):
    CONFIRMED_2B = "CONFIRMED_2B"  # 已確認的 2B
    PRE_2B = "PRE_2B"              # 潛在 2B（預警）
    NONE = "NONE"


class StructureQuality(Enum):
    SWING = "SWING"
    KEY_LEVEL = "KEY_LEVEL"
    SIMPLE = "SIMPLE"


class VolumeGrade(Enum):
    EXPLOSIVE = "explosive"
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


@dataclass
class ScanResult:
    """單個標的的掃描結果"""
    symbol: str
    rank: int = 0
    score: float = 0.0
    signal_side: str = "NEUTRAL"
    signal_type: str = "NONE"
    structure_quality: str = "SIMPLE"
    volume_grade: str = "weak"
    volume_ratio: float = 0.0
    adx: float = 0.0
    rsi: float = 0.0
    atr_percent: float = 0.0
    entry_price: float = 0.0
    stop_loss: float = 0.0
    target: float = 0.0
    risk_reward: float = 0.0
    sector: str = "Other"
    correlation_group: str = ""
    volume_24h: float = 0.0
    relative_strength: float = 0.0  # 相對 BTC 強度
    mtf_aligned: bool = False
    notes: str = ""
    is_pre_signal: bool = False  # 是否為預警信號


@dataclass
class MarketSummary:
    """市場概況"""
    scan_time: str
    total_scanned: int
    passed_layer1: int
    passed_layer2: int
    passed_layer3: int
    final_count: int
    bullish_count: int
    bearish_count: int
    pre_signal_count: int
    avg_adx: float
    dominant_trend: str
    market_regime: str  # TRENDING / RANGING / VOLATILE
    btc_trend: str      # BTC 的趨勢方向


# ==================== 板塊分類 ====================
SECTOR_MAPPING = {
    # Layer 1
    'BTC/USDT': 'Layer1', 'ETH/USDT': 'Layer1',
    # Layer 2
    'SOL/USDT': 'Layer2', 'AVAX/USDT': 'Layer2', 'MATIC/USDT': 'Layer2',
    'DOT/USDT': 'Layer2', 'ATOM/USDT': 'Layer2', 'NEAR/USDT': 'Layer2',
    'APT/USDT': 'Layer2', 'SUI/USDT': 'Layer2', 'SEI/USDT': 'Layer2',
    # DeFi
    'UNI/USDT': 'DeFi', 'AAVE/USDT': 'DeFi', 'LINK/USDT': 'DeFi',
    'MKR/USDT': 'DeFi', 'SNX/USDT': 'DeFi', 'CRV/USDT': 'DeFi',
    'COMP/USDT': 'DeFi', 'SUSHI/USDT': 'DeFi', 'YFI/USDT': 'DeFi',
    # Meme
    'DOGE/USDT': 'Meme', 'SHIB/USDT': 'Meme', 'PEPE/USDT': 'Meme',
    'FLOKI/USDT': 'Meme', 'BONK/USDT': 'Meme', 'WIF/USDT': 'Meme',
    # AI
    'FET/USDT': 'AI', 'AGIX/USDT': 'AI', 'RNDR/USDT': 'AI',
    'OCEAN/USDT': 'AI', 'TAO/USDT': 'AI',
    # Gaming
    'AXS/USDT': 'Gaming', 'SAND/USDT': 'Gaming', 'MANA/USDT': 'Gaming',
    'GALA/USDT': 'Gaming', 'IMX/USDT': 'Gaming', 'ENJ/USDT': 'Gaming',
    # Exchange Tokens
    'BNB/USDT': 'Exchange', 'OKB/USDT': 'Exchange', 'CRO/USDT': 'Exchange',
}


def get_sector(symbol: str) -> str:
    """獲取標的所屬板塊"""
    return SECTOR_MAPPING.get(symbol, 'Other')


# ==================== 結構分析 ====================
class StructureAnalysis:
    """結構分析工具"""
    
    @staticmethod
    def find_swing_points(df: pd.DataFrame, left_bars: int = 5, right_bars: int = 2) -> Dict:
        """找出 Swing High/Low"""
        if len(df) < left_bars + right_bars + 1:
            return {'swing_lows': [], 'swing_highs': [], 
                    'last_swing_low': None, 'last_swing_high': None}
        
        swing_lows = []
        swing_highs = []
        
        for i in range(left_bars, len(df) - right_bars):
            current_low = df['low'].iloc[i]
            current_high = df['high'].iloc[i]
            
            # Swing Low
            is_swing_low = True
            for j in range(1, left_bars + 1):
                if df['low'].iloc[i - j] <= current_low:
                    is_swing_low = False
                    break
            if is_swing_low:
                for j in range(1, right_bars + 1):
                    if df['low'].iloc[i + j] <= current_low:
                        is_swing_low = False
                        break
            if is_swing_low:
                swing_lows.append((i, current_low))
            
            # Swing High
            is_swing_high = True
            for j in range(1, left_bars + 1):
                if df['high'].iloc[i - j] >= current_high:
                    is_swing_high = False
                    break
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


# ==================== 核心掃描器 ====================
class MarketScanner:
    """市場掃描器主類"""
    
    def __init__(self):
        # 確保配置已載入（防止 GUI 直接建構時未調用 load_from_json）
        ScannerConfig.load_from_json()
        self.exchange = self._init_exchange()
        self.results: List[ScanResult] = []
        self.excluded: List[Dict] = []
        self.btc_data: pd.DataFrame = None
        self.market_summary: MarketSummary = None

    @staticmethod
    def _normalize_symbol(symbol: str) -> str:
        """正規化符號：BTC/USDT:USDT → BTC/USDT"""
        return symbol.split(':')[0] if ':' in symbol else symbol

    def _init_exchange(self):
        """初始化交易所（Scanner 永遠使用正式網）"""
        try:
            exchange_class = getattr(ccxt, ScannerConfig.EXCHANGE)

            # Scanner 配置：永遠使用正式網數據
            exchange_config = {
                'enableRateLimit': True,
                'options': {
                    'defaultType': ScannerConfig.MARKET_TYPE,
                    'sandboxMode': ScannerConfig.SANDBOX_MODE
                }
            }

            # 如果是 Binance Futures 且非 Sandbox，確保使用正式網
            if ScannerConfig.EXCHANGE == 'binance' and not ScannerConfig.SANDBOX_MODE:
                exchange_config['options']['defaultType'] = 'future'
                logger.info("📡 Scanner 使用 Binance Futures 正式網")

            exchange = exchange_class(exchange_config)
            exchange.load_markets()

            market_count = len([s for s in exchange.markets if '/USDT' in s])
            futures_count = len([s for s in exchange.markets
                                if '/USDT' in s and exchange.markets[s].get('linear')])
            logger.info(f"✅ 連接到 {ScannerConfig.EXCHANGE} {'測試網' if ScannerConfig.SANDBOX_MODE else '正式網'}")
            logger.info(f"   載入 {market_count} 個 USDT 交易對，其中 USDT Futures: {futures_count} 個")

            if ScannerConfig.MARKET_TYPE == 'future' and futures_count == 0:
                logger.warning("⚠️ 未偵測到 Futures 交易對！請確認 MARKET_TYPE 設定")

            return exchange
        except Exception as e:
            logger.error(f"❌ 交易所初始化失敗: {e}")
            raise
    
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """獲取 K 線數據"""
        for attempt in range(ScannerConfig.API_MAX_RETRIES):
            try:
                ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                return df
            except Exception as e:
                if attempt < ScannerConfig.API_MAX_RETRIES - 1:
                    time.sleep(ScannerConfig.API_DELAY_BETWEEN_BATCHES)
                else:
                    logger.debug(f"獲取 {symbol} 數據失敗: {e}")
                    return pd.DataFrame()
        return pd.DataFrame()
    
    def calculate_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """計算技術指標"""
        if df.empty or len(df) < 50:
            return df
        
        # 基礎指標
        df['ema_20'] = ta.ema(df['close'], length=20)
        df['ema_50'] = ta.ema(df['close'], length=50)
        df['ema_200'] = ta.ema(df['close'], length=200)
        df['rsi'] = ta.rsi(df['close'], length=14)
        df['atr'] = ta.atr(df['high'], df['low'], df['close'], length=14)
        df['vol_ma'] = ta.sma(df['volume'], length=20)
        
        # ADX
        adx_data = ta.adx(df['high'], df['low'], df['close'], length=14)
        if adx_data is not None and not adx_data.empty:
            if isinstance(adx_data, pd.DataFrame):
                adx_cols = [col for col in adx_data.columns if col.startswith('ADX')]
                if adx_cols:
                    df['adx'] = adx_data[adx_cols[0]]
        
        # ATR 百分比
        df['atr_percent'] = (df['atr'] / df['close']) * 100
        
        return df
    
    # ==================== Layer 1: 流動性過濾 ====================
    def layer1_liquidity_filter(self) -> List[str]:
        """Layer 1: 流動性過濾"""
        logger.info("\n" + "="*60)
        logger.info("📊 Layer 1: 流動性過濾")
        logger.info("="*60)
        
        try:
            tickers = self.exchange.fetch_tickers()

            # Debug: 打印 BTC/USDT ticker 結構，確認欄位名稱
            # 合約格式為 BTC/USDT:USDT，現貨格式為 BTC/USDT
            btc_ticker = tickers.get('BTC/USDT:USDT') or tickers.get('BTC/USDT')
            if btc_ticker:
                logger.debug(f"🔍 BTC/USDT ticker 欄位: quoteVolume={btc_ticker.get('quoteVolume')}, "
                            f"baseVolume={btc_ticker.get('baseVolume')}, "
                            f"info.quoteVolume={btc_ticker.get('info', {}).get('quoteVolume')}")

            passed = []
            usdt_count = 0
            for symbol, ticker in tickers.items():
                if '/USDT' not in symbol:
                    continue
                usdt_count += 1

                # 正規化：BTC/USDT:USDT → BTC/USDT
                base_symbol = self._normalize_symbol(symbol)

                if base_symbol in ScannerConfig.L1_EXCLUDED_SYMBOLS:
                    continue

                skip = False
                for pattern in ScannerConfig.L1_EXCLUDED_PATTERNS:
                    if pattern in base_symbol:
                        skip = True
                        break
                if skip:
                    continue

                # 優先使用 ccxt 標準化欄位，備用 Binance 原始 API 欄位
                quote_volume = ticker.get('quoteVolume', 0) or 0
                if quote_volume == 0:
                    info = ticker.get('info', {})
                    quote_volume = float(info.get('quoteVolume', 0) or 0)

                if quote_volume < ScannerConfig.L1_MIN_VOLUME_USD:
                    continue

                passed.append(base_symbol)

            logger.info(f"✅ Layer 1 通過: {len(passed)} / {usdt_count} 個 USDT 標的 (總 tickers: {len(tickers)})")
            if passed:
                logger.info(f"   前5: {passed[:5]}")
            return passed
            
        except Exception as e:
            logger.error(f"❌ Layer 1 失敗: {e}")
            return []
    
    # ==================== Layer 2: 動能篩選 ====================
    def layer2_momentum_filter(self, symbols: List[str]) -> List[Tuple[str, Dict]]:
        """Layer 2: 動能篩選"""
        logger.info("\n" + "="*60)
        logger.info("📈 Layer 2: 動能篩選")
        logger.info("="*60)
        
        # 先獲取 BTC 數據作為基準
        self.btc_data = self.fetch_ohlcv('BTC/USDT', ScannerConfig.TIMEFRAME_SCAN, limit=100)
        if not self.btc_data.empty:
            self.btc_data = self.calculate_indicators(self.btc_data)
        
        passed = []
        total = len(symbols)
        
        for i in range(0, total, ScannerConfig.API_BATCH_SIZE):
            batch = symbols[i:i + ScannerConfig.API_BATCH_SIZE]
            logger.info(f"   處理批次 {i//ScannerConfig.API_BATCH_SIZE + 1}/{(total-1)//ScannerConfig.API_BATCH_SIZE + 1}")
            
            for symbol in batch:
                try:
                    df = self.fetch_ohlcv(symbol, ScannerConfig.TIMEFRAME_SCAN, limit=100)
                    if df.empty or len(df) < 50:
                        continue
                    
                    df = self.calculate_indicators(df)
                    latest = df.iloc[-1]
                    
                    conditions_met = 0
                    indicators = {}
                    
                    # ADX
                    adx = latest.get('adx', 0)
                    if pd.notna(adx) and adx > ScannerConfig.L2_MIN_ADX:
                        conditions_met += 1
                    indicators['adx'] = adx if pd.notna(adx) else 0
                    
                    # RSI
                    rsi = latest.get('rsi', 50)
                    if pd.notna(rsi) and ScannerConfig.L2_RSI_RANGE[0] <= rsi <= ScannerConfig.L2_RSI_RANGE[1]:
                        conditions_met += 1
                    indicators['rsi'] = rsi if pd.notna(rsi) else 50
                    
                    # 成交量 > MA
                    if latest['volume'] > latest['vol_ma']:
                        conditions_met += 1
                    
                    # ATR%
                    atr_pct = latest.get('atr_percent', 0)
                    if pd.notna(atr_pct) and ScannerConfig.L2_MIN_ATR_PERCENT <= atr_pct <= ScannerConfig.L2_MAX_ATR_PERCENT:
                        conditions_met += 1
                    indicators['atr_percent'] = atr_pct if pd.notna(atr_pct) else 0
                    
                    # EMA 趨勢（價格需明確偏離 EMA 才算有效趨勢）
                    ema_50 = latest.get('ema_50', 0)
                    if pd.notna(ema_50) and ema_50 > 0:
                        ema_gap_pct = abs(latest['close'] - ema_50) / ema_50
                        if ema_gap_pct > 0.01:  # 價格偏離 EMA 至少 1%
                            conditions_met += 1
                        indicators['trend'] = 'BULLISH' if latest['close'] > ema_50 else 'BEARISH'
                    else:
                        indicators['trend'] = 'NEUTRAL'
                    
                    # 相對強度
                    relative_strength = self._calculate_relative_strength(df)
                    indicators['relative_strength'] = relative_strength
                    
                    if conditions_met >= ScannerConfig.L2_MIN_CONDITIONS:
                        indicators['conditions_met'] = conditions_met
                        indicators['df'] = df
                        passed.append((symbol, indicators))
                    
                except Exception as e:
                    logger.debug(f"處理 {symbol} 時出錯: {e}")
                    continue
            
            if i + ScannerConfig.API_BATCH_SIZE < total:
                time.sleep(ScannerConfig.API_DELAY_BETWEEN_BATCHES)
        
        logger.info(f"✅ Layer 2 通過: {len(passed)} / {total} 個標的")
        return passed
    
    def _calculate_relative_strength(self, df: pd.DataFrame) -> float:
        """計算相對 BTC 的強度"""
        if self.btc_data is None or self.btc_data.empty or df.empty:
            return 0.0
        
        try:
            symbol_return = (df['close'].iloc[-1] - df['close'].iloc[-6]) / df['close'].iloc[-6] * 100
            btc_return = (self.btc_data['close'].iloc[-1] - self.btc_data['close'].iloc[-6]) / self.btc_data['close'].iloc[-6] * 100
            return symbol_return - btc_return
        except Exception:
            return 0.0
    
    # ==================== Layer 3: 形態匹配 ====================
    def layer3_pattern_matching(self, candidates: List[Tuple[str, Dict]]) -> List[ScanResult]:
        """Layer 3: 形態匹配"""
        logger.info("\n" + "="*60)
        logger.info("🎯 Layer 3: 形態匹配")
        logger.info("="*60)
        
        results = []
        
        for symbol, indicators in candidates:
            try:
                df = indicators['df']
                signal_result = self._detect_2b_signal(df, symbol, indicators)
                
                if signal_result:
                    results.append(signal_result)
                    
            except Exception as e:
                logger.debug(f"處理 {symbol} 形態時出錯: {e}")
                continue
        
        results.sort(key=lambda x: x.score, reverse=True)
        
        confirmed = [r for r in results if r.signal_type == SignalType.CONFIRMED_2B.value]
        pre_signals = [r for r in results if r.signal_type == SignalType.PRE_2B.value]
        
        logger.info(f"✅ Layer 3 通過: {len(results)} 個標的")
        logger.info(f"   確認信號: {len(confirmed)} 個")
        logger.info(f"   預警信號: {len(pre_signals)} 個")
        
        return results
    
    def _detect_2b_signal(self, df: pd.DataFrame, symbol: str, indicators: Dict) -> Optional[ScanResult]:
        """檢測 2B 信號"""
        if len(df) < 30:
            return None
        
        structure = StructureAnalysis.find_swing_points(
            df,
            left_bars=ScannerConfig.L3_SWING_LEFT_BARS,
            right_bars=ScannerConfig.L3_SWING_RIGHT_BARS
        )
        
        current = df.iloc[-1]
        atr = current.get('atr', 0)
        if not atr or atr == 0:
            return None
        
        swing_low = structure['last_swing_low']
        swing_high = structure['last_swing_high']
        
        if swing_low is None:
            swing_low = df['low'].iloc[-21:-1].min()
        if swing_high is None:
            swing_high = df['high'].iloc[-21:-1].max()
        
        signal_type = SignalType.NONE
        signal_side = SignalSide.NEUTRAL
        structure_quality = StructureQuality.SIMPLE
        is_pre_signal = False
        entry_price = current['close']
        stop_loss = 0
        target = 0
        notes = ""
        
        if structure['last_swing_low'] is not None or structure['last_swing_high'] is not None:
            structure_quality = StructureQuality.SWING
        
        # Bullish 2B
        broke_low = current['low'] < swing_low
        closed_above_low = current['close'] > swing_low
        penetration = swing_low - current['low']
        reasonable_depth = 0 < penetration < (atr * ScannerConfig.L3_MAX_PENETRATION_ATR)
        
        if broke_low and closed_above_low and reasonable_depth:
            signal_type = SignalType.CONFIRMED_2B
            signal_side = SignalSide.LONG
            stop_loss = swing_low - (atr * 0.5)
            target = swing_high
            notes = f"跌破前低 ${swing_low:.2f} 後收回，深度 {penetration/atr:.1f} ATR"
        
        # Bearish 2B
        broke_high = current['high'] > swing_high
        closed_below_high = current['close'] < swing_high
        penetration_up = current['high'] - swing_high
        reasonable_height = 0 < penetration_up < (atr * ScannerConfig.L3_MAX_PENETRATION_ATR)
        
        if broke_high and closed_below_high and reasonable_height:
            signal_type = SignalType.CONFIRMED_2B
            signal_side = SignalSide.SHORT
            stop_loss = swing_high + (atr * 0.5)
            target = swing_low
            notes = f"突破前高 ${swing_high:.2f} 後收回，深度 {penetration_up/atr:.1f} ATR"
        
        # Pre-2B
        if signal_type == SignalType.NONE:
            distance_to_low = current['close'] - swing_low
            if 0 < distance_to_low < (atr * ScannerConfig.L3_PRE_2B_THRESHOLD):
                signal_type = SignalType.PRE_2B
                signal_side = SignalSide.LONG
                is_pre_signal = True
                stop_loss = swing_low - (atr * 0.5)
                target = swing_high
                notes = f"距離前低 ${swing_low:.2f} 僅 {distance_to_low/atr:.1f} ATR"
            
            distance_to_high = swing_high - current['close']
            if 0 < distance_to_high < (atr * ScannerConfig.L3_PRE_2B_THRESHOLD):
                signal_type = SignalType.PRE_2B
                signal_side = SignalSide.SHORT
                is_pre_signal = True
                stop_loss = swing_high + (atr * 0.5)
                target = swing_low
                notes = f"距離前高 ${swing_high:.2f} 僅 {distance_to_high/atr:.1f} ATR"
        
        if signal_type == SignalType.NONE:
            return None
        
        # 量能分級
        vol_ratio = current['volume'] / current['vol_ma'] if current['vol_ma'] > 0 else 0
        
        if vol_ratio >= 2.5:
            volume_grade = VolumeGrade.EXPLOSIVE
        elif vol_ratio >= 1.5:
            volume_grade = VolumeGrade.STRONG
        elif vol_ratio >= 1.0:
            volume_grade = VolumeGrade.MODERATE
        else:
            volume_grade = VolumeGrade.WEAK
        
        if signal_type == SignalType.CONFIRMED_2B and vol_ratio < ScannerConfig.L3_MIN_VOLUME_RATIO:
            return None
        
        # MTF 確認
        mtf_aligned = self._check_mtf_alignment(symbol, signal_side)
        
        # 評分
        score = self._calculate_score(
            structure_quality=structure_quality,
            volume_grade=volume_grade,
            adx=indicators.get('adx', 0),
            atr_percent=indicators.get('atr_percent', 0),
            vol_ratio=vol_ratio,
            mtf_aligned=mtf_aligned,
            is_pre_signal=is_pre_signal,
            relative_strength=indicators.get('relative_strength', 0),
            signal_side=signal_side
        )
        
        # R/R
        if stop_loss and target and entry_price:
            risk = abs(entry_price - stop_loss)
            reward = abs(target - entry_price)
            risk_reward = reward / risk if risk > 0 else 0
        else:
            risk_reward = 0
        
        return ScanResult(
            symbol=symbol,
            score=score,
            signal_side=signal_side.value,
            signal_type=signal_type.value,
            structure_quality=structure_quality.value,
            volume_grade=volume_grade.value,
            volume_ratio=vol_ratio,
            adx=indicators.get('adx', 0),
            rsi=indicators.get('rsi', 50),
            atr_percent=indicators.get('atr_percent', 0),
            entry_price=entry_price,
            stop_loss=stop_loss,
            target=target,
            risk_reward=risk_reward,
            sector=get_sector(symbol),
            relative_strength=indicators.get('relative_strength', 0),
            mtf_aligned=mtf_aligned,
            notes=notes,
            is_pre_signal=is_pre_signal
        )
    
    def _check_mtf_alignment(self, symbol: str, signal_side: SignalSide) -> bool:
        """檢查多時間框架對齊"""
        try:
            df_4h = self.fetch_ohlcv(symbol, ScannerConfig.TIMEFRAME_TREND, limit=50)
            if df_4h.empty or len(df_4h) < 30:
                return False
            
            df_4h = self.calculate_indicators(df_4h)
            latest = df_4h.iloc[-1]
            
            ema_20 = latest.get('ema_20', 0)
            ema_50 = latest.get('ema_50', 0)
            price = latest['close']
            
            if signal_side == SignalSide.LONG:
                return price > ema_20 and ema_20 > ema_50
            elif signal_side == SignalSide.SHORT:
                return price < ema_20 and ema_20 < ema_50
            
            return False
        except Exception:
            return False

    def _calculate_score(self, **kwargs) -> float:
        """計算綜合評分"""
        score = 0
        
        # 結構品質 (25%)
        structure_scores = {
            StructureQuality.SWING: 100,
            StructureQuality.KEY_LEVEL: 70,
            StructureQuality.SIMPLE: 40
        }
        sq = kwargs.get('structure_quality', StructureQuality.SIMPLE)
        score += structure_scores.get(sq, 40) * 0.25
        
        # 量能 (25%)
        volume_scores = {
            VolumeGrade.EXPLOSIVE: 100,
            VolumeGrade.STRONG: 80,
            VolumeGrade.MODERATE: 60,
            VolumeGrade.WEAK: 30
        }
        vg = kwargs.get('volume_grade', VolumeGrade.WEAK)
        score += volume_scores.get(vg, 30) * 0.25
        
        # ADX (20%)
        adx = kwargs.get('adx', 0)
        if adx >= 30:
            score += 100 * 0.20
        elif adx >= 25:
            score += 80 * 0.20
        elif adx >= 20:
            score += 60 * 0.20
        else:
            score += 30 * 0.20
        
        # ATR% (15%)
        atr_pct = kwargs.get('atr_percent', 0)
        if atr_pct >= 5:
            score += 100 * 0.15
        elif atr_pct >= 3:
            score += 80 * 0.15
        elif atr_pct >= 2:
            score += 60 * 0.15
        else:
            score += 40 * 0.15
        
        # MTF (10%)
        if kwargs.get('mtf_aligned', False):
            score += 100 * 0.10
        
        # 相對強度 (5%)
        rs = kwargs.get('relative_strength', 0)
        signal_side = kwargs.get('signal_side', SignalSide.NEUTRAL)
        if signal_side == SignalSide.LONG and rs > 0:
            score += min(rs * 10, 100) * 0.05
        elif signal_side == SignalSide.SHORT and rs < 0:
            score += min(abs(rs) * 10, 100) * 0.05
        
        # 預警扣分
        if kwargs.get('is_pre_signal', False):
            score *= 0.8
        
        return round(score, 1)
    
    # ==================== Layer 4: 相關性過濾 ====================
    def layer4_correlation_filter(self, results: List[ScanResult]) -> List[ScanResult]:
        """Layer 4: 相關性過濾"""
        logger.info("\n" + "="*60)
        logger.info("🔗 Layer 4: 相關性過濾")
        logger.info("="*60)
        
        if len(results) <= ScannerConfig.OUTPUT_TOP_N:
            return results
        
        sector_count: Dict[str, int] = {}
        filtered_results: List[ScanResult] = []
        
        for result in results:
            sector = result.sector
            
            if sector_count.get(sector, 0) >= ScannerConfig.L4_MAX_PER_SECTOR:
                self.excluded.append({
                    'symbol': result.symbol,
                    'reason': f'板塊集中度過濾：{sector} 已有 {ScannerConfig.L4_MAX_PER_SECTOR} 個',
                    'score': result.score
                })
                continue
            
            sector_count[sector] = sector_count.get(sector, 0) + 1
            filtered_results.append(result)
            
            if len(filtered_results) >= ScannerConfig.OUTPUT_TOP_N:
                break
        
        for i, result in enumerate(filtered_results):
            result.rank = i + 1
            result.correlation_group = chr(65 + i % 5)
        
        logger.info(f"✅ Layer 4 通過: {len(filtered_results)} 個標的")
        return filtered_results
    
    # ==================== 主掃描流程 ====================
    def scan(self) -> Tuple[List[ScanResult], MarketSummary]:
        """執行完整掃描"""
        scan_start = datetime.now(timezone.utc)
        logger.info("\n" + "="*70)
        logger.info("🔍 開始市場掃描")
        logger.info(f"   時間: {scan_start.isoformat()}")
        logger.info("="*70)
        
        self.results = []
        self.excluded = []
        
        l1_symbols = self.layer1_liquidity_filter()
        l2_candidates = self.layer2_momentum_filter(l1_symbols)
        l3_results = self.layer3_pattern_matching(l2_candidates)
        final_results = self.layer4_correlation_filter(l3_results)
        
        self.results = final_results
        
        self.market_summary = self._generate_market_summary(
            scan_time=scan_start,
            total_scanned=len(l1_symbols) if l1_symbols else 0,
            passed_layer1=len(l1_symbols),
            passed_layer2=len(l2_candidates),
            passed_layer3=len(l3_results),
            final_count=len(final_results),
            results=final_results
        )
        
        self._output_results()
        
        scan_duration = (datetime.now(timezone.utc) - scan_start).total_seconds()
        logger.info(f"\n✅ 掃描完成，耗時 {scan_duration:.1f} 秒")
        
        return self.results, self.market_summary
    
    def _generate_market_summary(self, **kwargs) -> MarketSummary:
        """生成市場摘要"""
        results = kwargs.get('results', [])
        
        bullish = len([r for r in results if r.signal_side == 'LONG'])
        bearish = len([r for r in results if r.signal_side == 'SHORT'])
        pre_signals = len([r for r in results if r.is_pre_signal])
        
        avg_adx = np.mean([r.adx for r in results]) if results else 0
        
        if bullish > bearish * 2:
            dominant = 'BULLISH'
        elif bearish > bullish * 2:
            dominant = 'BEARISH'
        else:
            dominant = 'MIXED'
        
        if avg_adx > 25:
            regime = 'TRENDING'
        elif avg_adx < 15:
            regime = 'RANGING'
        else:
            regime = 'TRANSITIONING'
        
        btc_trend = 'UNKNOWN'
        if self.btc_data is not None and not self.btc_data.empty:
            latest = self.btc_data.iloc[-1]
            ema_50 = latest.get('ema_50', 0)
            if pd.notna(ema_50) and ema_50 > 0:
                btc_trend = 'BULLISH' if latest['close'] > ema_50 else 'BEARISH'
        
        return MarketSummary(
            scan_time=kwargs.get('scan_time', datetime.now(timezone.utc)).isoformat(),
            total_scanned=kwargs.get('total_scanned', 0),
            passed_layer1=kwargs.get('passed_layer1', 0),
            passed_layer2=kwargs.get('passed_layer2', 0),
            passed_layer3=kwargs.get('passed_layer3', 0),
            final_count=kwargs.get('final_count', 0),
            bullish_count=bullish,
            bearish_count=bearish,
            pre_signal_count=pre_signals,
            avg_adx=round(avg_adx, 1),
            dominant_trend=dominant,
            market_regime=regime,
            btc_trend=btc_trend
        )
    
    # ==================== 輸出 ====================
    def _output_results(self):
        """輸出掃描結果"""
        self._output_json()
        self._output_sqlite()
        self._print_summary()
        
        if ScannerConfig.TELEGRAM_ENABLED:
            self._send_telegram()
    
    def _output_json(self):
        """輸出 JSON"""
        output = {
            'scan_time': self.market_summary.scan_time,
            'market_regime': self.market_summary.market_regime,
            'total_scanned': self.market_summary.total_scanned,
            'passed_layer1': self.market_summary.passed_layer1,
            'passed_layer2': self.market_summary.passed_layer2,
            'passed_layer3': self.market_summary.passed_layer3,
            'final_count': self.market_summary.final_count,
            'hot_symbols': [asdict(r) for r in self.results],
            'excluded': self.excluded,
            'market_summary': asdict(self.market_summary)
        }
        
        def _json_default(obj):
            """處理 numpy bool / int / float 等非標準 JSON 類型"""
            if hasattr(obj, 'item'):
                return obj.item()
            raise TypeError(f'Object of type {type(obj).__name__} is not JSON serializable')

        with open(ScannerConfig.OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=_json_default)
        
        logger.info(f"📄 已輸出: {ScannerConfig.OUTPUT_JSON_PATH}")
    
    def _output_sqlite(self):
        """輸出 SQLite"""
        conn = sqlite3.connect(ScannerConfig.OUTPUT_DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS scan_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_time TEXT,
                total_scanned INTEGER,
                final_count INTEGER,
                market_regime TEXT,
                btc_trend TEXT
            )
        ''')
        
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER,
                symbol TEXT,
                rank INTEGER,
                score REAL,
                signal_side TEXT,
                signal_type TEXT,
                entry_price REAL,
                stop_loss REAL,
                target REAL,
                risk_reward REAL,
                sector TEXT,
                is_pre_signal INTEGER,
                status TEXT DEFAULT 'PENDING',
                FOREIGN KEY (scan_id) REFERENCES scan_history(id)
            )
        ''')
        
        cursor.execute('''
            INSERT INTO scan_history (scan_time, total_scanned, final_count, market_regime, btc_trend)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            self.market_summary.scan_time,
            self.market_summary.total_scanned,
            self.market_summary.final_count,
            self.market_summary.market_regime,
            self.market_summary.btc_trend
        ))
        
        scan_id = cursor.lastrowid
        
        for result in self.results:
            cursor.execute('''
                INSERT INTO signals (scan_id, symbol, rank, score, signal_side, signal_type,
                                    entry_price, stop_loss, target, risk_reward, sector, is_pre_signal)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                scan_id, result.symbol, result.rank, result.score, result.signal_side,
                result.signal_type, result.entry_price, result.stop_loss, result.target,
                result.risk_reward, result.sector, 1 if result.is_pre_signal else 0
            ))
        
        conn.commit()
        conn.close()
        
        logger.info(f"💾 已輸出: {ScannerConfig.OUTPUT_DB_PATH}")
    
    def _print_summary(self):
        """終端輸出摘要"""
        print("\n" + "="*70)
        print("📊 市場掃描結果")
        print("="*70)
        print(f"掃描時間: {self.market_summary.scan_time}")
        print(f"市場狀態: {self.market_summary.market_regime}")
        print(f"BTC 趨勢: {self.market_summary.btc_trend}")
        print(f"多空比例: {self.market_summary.bullish_count} 多 / {self.market_summary.bearish_count} 空")
        print(f"平均 ADX: {self.market_summary.avg_adx}")
        print("-"*70)
        
        confirmed = [r for r in self.results if r.signal_type == SignalType.CONFIRMED_2B.value]
        pre_signals = [r for r in self.results if r.signal_type == SignalType.PRE_2B.value]
        
        if confirmed:
            print(f"\n🎯 確認信號 ({len(confirmed)} 個):")
            for r in confirmed[:5]:
                emoji = "🟢" if r.signal_side == "LONG" else "🔴"
                print(f"  {r.rank}. {emoji} {r.symbol} ({r.signal_side}) ⭐{r.score}分")
                print(f"     入場: ${r.entry_price:.4f} | 止損: ${r.stop_loss:.4f} | 目標: ${r.target:.4f}")
                print(f"     R/R: {r.risk_reward:.1f} | ADX: {r.adx:.1f} | 量能: {r.volume_grade}")
                print(f"     {r.notes}")
        
        if pre_signals:
            print(f"\n⏳ 預警信號 ({len(pre_signals)} 個):")
            for r in pre_signals[:3]:
                emoji = "🟡" if r.signal_side == "LONG" else "🟠"
                print(f"  {r.rank}. {emoji} {r.symbol} ({r.signal_side}) ⭐{r.score}分 [預警]")
                print(f"     {r.notes}")
        
        print("\n" + "="*70)
    
    def _send_telegram(self):
        """發送 Telegram 通知"""
        if not ScannerConfig.TELEGRAM_ENABLED:
            return
        
        try:
            confirmed = [r for r in self.results if r.signal_type == SignalType.CONFIRMED_2B.value]
            
            msg = f"""🔍 <b>Market Scanner 掃描完成</b>

📊 <b>市場概況</b>
├─ 掃描標的: {self.market_summary.total_scanned} 個
├─ 通過篩選: {self.market_summary.final_count} 個
├─ 市場狀態: {self.market_summary.market_regime}
├─ BTC 趨勢: {self.market_summary.btc_trend}
└─ 多空比例: {self.market_summary.bullish_count}多/{self.market_summary.bearish_count}空

"""
            if confirmed:
                msg += "🎯 <b>Top 5 機會</b>\n\n"
                for r in confirmed[:5]:
                    emoji = "🟢" if r.signal_side == "LONG" else "🔴"
                    msg += f"{r.rank}️⃣ {emoji} <b>{r.symbol}</b> ({r.signal_side}) ⭐{r.score}分\n"
                    msg += f"   入場: ${r.entry_price:.2f} | 止損: ${r.stop_loss:.2f}\n"
                    msg += f"   R/R: {r.risk_reward:.1f} | {r.volume_grade}\n\n"
            
            msg += f"⏰ 下次掃描: {ScannerConfig.SCAN_INTERVAL_MINUTES} 分鐘後"
            
            url = f"https://api.telegram.org/bot{ScannerConfig.TELEGRAM_BOT_TOKEN}/sendMessage"
            requests.post(url, data={
                'chat_id': ScannerConfig.TELEGRAM_CHAT_ID,
                'text': msg,
                'parse_mode': 'HTML'
            }, timeout=10)
            
        except Exception as e:
            logger.error(f"Telegram 發送失敗: {e}")


# ==================== 主程序 ====================
def main():
    """主程序入口"""
    import argparse
    
    parser = argparse.ArgumentParser(description='Crypto Market Scanner v1.0')
    parser.add_argument('--once', action='store_true', help='只執行一次掃描')
    parser.add_argument('--config', type=str, help='配置文件路徑')
    args = parser.parse_args()
    
    ScannerConfig.load_from_json(args.config)
    
    scanner = MarketScanner()
    
    if args.once:
        scanner.scan()
    else:
        logger.info(f"🚀 Scanner 啟動，掃描間隔: {ScannerConfig.SCAN_INTERVAL_MINUTES} 分鐘")
        
        while True:
            try:
                scanner.scan()
                
                logger.info(f"😴 等待 {ScannerConfig.SCAN_INTERVAL_MINUTES} 分鐘...")
                time.sleep(ScannerConfig.SCAN_INTERVAL_MINUTES * 60)
                
            except KeyboardInterrupt:
                logger.info("\n⏹ 用戶中斷，停止掃描")
                break
            except Exception as e:
                logger.error(f"❌ 掃描錯誤: {e}")
                logger.info("等待 60 秒後重試...")
                time.sleep(60)


if __name__ == "__main__":
    main()
