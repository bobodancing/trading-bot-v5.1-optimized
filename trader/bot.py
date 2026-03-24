"""
V6.0 主引擎 — 終極滾倉版

基於 V5.3 TradingBotV53 重構：
- 2B 信號 → V7 結構加倉（V7StructureStrategy, 三段 swing-based 加倉）
- EMA Pullback / Volume Breakout → V5.3 SOP（PositionManager, is_v6_pyramid=False）
- [DEPRECATED] V6.0 滾倉仍保留供既有持倉平倉
- positions.json 持久化
"""

import sys
import os
import time
import json
import signal
import logging
import logging.handlers
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Optional

# 確保從專案根目錄 import v6 package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ccxt
import pandas as pd

# 基礎設施層
from trader.infrastructure.api_client import BinanceFuturesClient
from trader.infrastructure.notifier import TelegramNotifier
from trader.infrastructure.telegram_handler import TelegramCommandHandler
from trader.infrastructure.data_provider import MarketDataProvider
from trader.infrastructure.performance_db import PerformanceDB
# 技術指標層
from trader.indicators.technical import (
    TechnicalAnalysis,
    DynamicThresholdManager,
    MTFConfirmation,
    MarketFilter,
)
# 風險管理層
from trader.risk.manager import PrecisionHandler, RiskManager, SignalTierSystem
# 訂單執行層
from trader.execution.order_engine import OrderExecutionEngine
from trader.config import ConfigV6 as Config
from trader.positions import PositionManager
from trader.persistence import PositionPersistence
from trader.signals import detect_2b_with_pivots, detect_ema_pullback, detect_volume_breakout
from trader.strategies.base import Action

logger = logging.getLogger(__name__)


def _trade_log(fields: dict):
    """Emit structured [TRADE] log line for log_summarizer.py"""
    parts = ' | '.join(f'{k}={v}' for k, v in fields.items())
    logger.info(f"[TRADE] {parts}")


class TradingBotV6:
    """V6.0 終極滾倉版交易機器人"""

    def __init__(self):
        self.exchange = self._init_exchange()
        self.data_provider = MarketDataProvider(
            self.exchange,
            max_retry=Config.MAX_RETRY,
            retry_delay=Config.RETRY_DELAY,
            sandbox_mode=Config.SANDBOX_MODE,
            trading_mode=Config.TRADING_MODE,
        )
        self.precision_handler = PrecisionHandler(self.exchange)
        self.futures_client = BinanceFuturesClient(Config.API_KEY, Config.API_SECRET, Config.SANDBOX_MODE)
        self.risk_manager = RiskManager(self.exchange, self.precision_handler)
        # RiskManager 內部用 V5.3 Config 建的 futures_client 拿不到新 key，覆蓋掉
        self.risk_manager.futures_client = self.futures_client
        # 訂單執行引擎（Phase 3: 剝離下單邏輯）
        self.execution_engine = OrderExecutionEngine(
            self.exchange, self.futures_client, self.precision_handler
        )

        # V6.0: PositionManager 取代 TradeManager
        self.active_trades: Dict[str, PositionManager] = {}

        # 冷卻和黑名單
        self.recently_exited: Dict[str, datetime] = {}
        self.order_failed_symbols: Dict[str, datetime] = {}
        self.early_exit_cooldown: Dict[str, datetime] = {}  # 快速止損/超時退出 12h 冷卻

        # 帳戶初始餘額（用於 net_pnl_pct 計算）
        self.initial_balance: float = 0.0

        # V6.0: 持久化層（路徑在 Config，指向專案根目錄）
        pos_path = os.path.expanduser(Config.POSITIONS_JSON_PATH)
        if not os.path.isabs(pos_path):
            pos_path = str(Path(__file__).parent.parent / pos_path)
        Path(pos_path).parent.mkdir(parents=True, exist_ok=True)
        self.persistence = PositionPersistence(pos_path)

        # 啟動時恢復 positions
        self._restore_positions()

        # Phase 0: 績效 DB
        db_path = getattr(Config, 'DB_PATH', 'v6_performance.db')
        self.perf_db = PerformanceDB(db_path=db_path)

        self._log_startup()

        # Telegram 互動指令
        self.telegram_handler = TelegramCommandHandler(self)
        self._start_time = datetime.now(timezone.utc)

    def _init_exchange(self):
        """初始化交易所（沿用 V5.3）"""
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
                    exchange.set_sandbox_mode(True)
                    # ccxt sandbox 會設成 testnet，覆蓋為 Demo Trading 端點
                    if 'api' in exchange.urls:
                        for key in exchange.urls['api']:
                            url_val = str(exchange.urls['api'].get(key, ''))
                            if 'fapi' in url_val.lower() or 'testnet' in url_val.lower():
                                exchange.urls['api'][key] = url_val.replace(
                                    'testnet.binancefuture.com', 'demo-fapi.binance.com'
                                ).replace(
                                    'fapi.binance.com', 'demo-fapi.binance.com'
                                )
                    exchange.options['sandboxMode'] = True
                    exchange.options['defaultType'] = 'future'
                    logger.info("已連接 Binance Demo Trading")
                else:
                    try:
                        exchange.set_sandbox_mode(True)
                    except Exception as e:
                        logger.warning(f"沙盒模式啟用失敗: {e}")

            try:
                exchange.load_markets()
                logger.info(f"已載入 {len(exchange.markets)} 個交易對")
            except Exception as e:
                logger.warning(f"載入市場資訊失敗: {e}")

            if Config.TRADING_MODE == 'future':
                for symbol in Config.SYMBOLS:
                    try:
                        exchange.set_leverage(Config.LEVERAGE, symbol)
                    except Exception:
                        pass

            return exchange

        except Exception as e:
            logger.error(f"交易所初始化失敗: {e}")
            raise

    def _log_startup(self):
        """啟動日誌"""
        logger.info("=" * 60)
        logger.info("交易機器人 V6.0 已啟動")
        logger.info("=" * 60)
        logger.info(f"模式: {Config.TRADING_MODE} ({Config.TRADING_DIRECTION})")
        logger.info(f"槓桿: {Config.LEVERAGE}x")
        logger.info(f"風險: 每筆 {Config.RISK_PER_TRADE*100:.1f}%")
        logger.info(f"滾倉: {'開啟' if Config.PYRAMID_ENABLED else '關閉'}")
        if Config.PYRAMID_ENABLED:
            logger.info(f"  資金上限: {Config.EQUITY_CAP_PERCENT*100:.0f}%")
            logger.info(f"  三段比例: {Config.STAGE1_RATIO}/{Config.STAGE2_RATIO}/{Config.STAGE3_RATIO}")
        logger.info(f"模擬模式: {'開啟' if Config.V6_DRY_RUN else '關閉'}")
        logger.info(f"已恢復持倉: {len(self.active_trades)}")
        logger.info(f"監控標的: {', '.join(Config.SYMBOLS)}")
        logger.info("=" * 60)

    def _restore_positions(self):
        """從 positions.json 恢復 positions"""
        data = self.persistence.load_positions()
        if not data:
            return

        for symbol, pos_data in data.items():
            try:
                pm = PositionManager.from_dict(pos_data)
                self.active_trades[symbol] = pm
                value_usdt = pm.total_size * pm.avg_entry
                logger.info(
                    f"已恢復 {symbol}: {pm.side} 階段{pm.stage} "
                    f"倉位=${value_usdt:.2f} 止損=${pm.current_sl:.2f}"
                )
            except Exception as e:
                logger.error(f"恢復 {symbol} 失敗: {e}")

    def _save_positions(self):
        """儲存所有 positions 到 JSON"""
        data = {}
        for symbol, pm in self.active_trades.items():
            data[symbol] = pm.to_dict()
        self.persistence.save_positions(data)

    # ==================== 數據獲取 ====================

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> pd.DataFrame:
        """獲取 OHLCV 數據（委託 MarketDataProvider 統一處理重試與沙盒 fallback）"""
        return self.data_provider.fetch_ohlcv(symbol, timeframe, limit)

    def fetch_ticker(self, symbol: str) -> dict:
        """獲取 ticker（含 Demo Trading fallback）"""
        try:
            return self.exchange.fetch_ticker(symbol)
        except Exception:
            if Config.TRADING_MODE == 'future' and Config.SANDBOX_MODE:
                import requests as req
                symbol_id = symbol.replace('/', '')
                base_url = 'https://demo-fapi.binance.com'
                resp = req.get(
                    f'{base_url}/fapi/v1/ticker/price',
                    params={'symbol': symbol_id},
                    timeout=30
                )
                if resp.status_code == 200:
                    data = resp.json()
                    price = float(data['price'])
                    return {'symbol': symbol, 'last': price, 'bid': price, 'ask': price}
            raise

    def load_scanner_results(self) -> List[str]:
        """從 Scanner 載入動態標的（沿用 V5.3）"""
        try:
            scanner_path = os.path.expanduser(Config.SCANNER_JSON_PATH)
            # 相對路徑 → 基於專案根目錄
            if not os.path.isabs(scanner_path):
                scanner_path = str(Path(__file__).parent.parent / scanner_path)
            if not os.path.exists(scanner_path):
                logger.warning(f"Scanner JSON 不存在: {scanner_path}，使用預設 symbols")
                return Config.SYMBOLS

            with open(scanner_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            scan_time_str = data.get('scan_time', '')
            if scan_time_str:
                try:
                    scan_time = datetime.fromisoformat(scan_time_str.replace('Z', '+00:00'))
                    age_minutes = (datetime.now(timezone.utc) - scan_time).total_seconds() / 60
                    if age_minutes > Config.SCANNER_MAX_AGE_MINUTES:
                        logger.warning(f"Scanner 資料已過期 ({age_minutes:.0f} 分鐘 > {Config.SCANNER_MAX_AGE_MINUTES} 分鐘上限)，使用預設 symbols")
                        return Config.SYMBOLS
                except Exception:
                    pass

            hot_symbols = data.get('hot_symbols', [])
            if not hot_symbols:
                logger.warning("Scanner JSON 中 hot_symbols 為空，使用預設 symbols")
                return Config.SYMBOLS

            scanner_symbols = [item['symbol'] for item in hot_symbols if item.get('symbol')]
            if scanner_symbols:
                logger.debug(f"Scanner 載入 {len(scanner_symbols)} 個標的: {', '.join(scanner_symbols)}")  # 降噪
                return scanner_symbols
            else:
                logger.warning("Scanner JSON 解析後無有效 symbol，使用預設 symbols")
                return Config.SYMBOLS

        except Exception as e:
            logger.warning(f"Scanner JSON 載入失敗: {e}，使用預設 symbols")
            return Config.SYMBOLS

    # ==================== 訂單執行（委託 OrderExecutionEngine）====================

    def _futures_set_leverage(self, symbol: str) -> bool:
        """設置槓桿"""
        return self.execution_engine.set_leverage(symbol)

    def _futures_create_order(self, symbol: str, side: str, quantity: float) -> dict:
        """下市價單"""
        return self.execution_engine.create_order(symbol, side, quantity)

    @staticmethod
    def _extract_fill_price(order_result: dict, fallback_price: float) -> float:
        """
        從訂單回應取實際成交均價（avgPrice / average）。
        失敗或回傳 0 時使用 fallback（信號價），確保不影響現有邏輯。

        BinanceFuturesClient 路徑：result['avgPrice']（字串）
        CCXT 路徑：result['average']（float）
        """
        try:
            avg = order_result.get('avgPrice') or order_result.get('average')
            if avg:
                price = float(avg)
                if price > 0:
                    return price
        except Exception:
            pass
        return fallback_price

    def _futures_close_position(self, symbol: str, side: str, quantity: float) -> dict:
        """平倉"""
        return self.execution_engine.close_position(symbol, side, quantity)

    def _place_hard_stop_loss(self, symbol: str, side: str, size: float, stop_price: float) -> Optional[str]:
        """設置硬止損單，回傳 order ID"""
        return self.execution_engine.place_hard_stop_loss(symbol, side, size, stop_price)

    def _cancel_stop_loss_order(self, symbol: str, order_id: Optional[str]) -> bool:
        """取消止損單"""
        return self.execution_engine.cancel_stop_loss_order(symbol, order_id)

    def _update_hard_stop_loss(self, pm: PositionManager, new_stop: float):
        """更新硬止損單"""
        self.execution_engine.update_hard_stop_loss(pm, new_stop)

    # ==================== 信號掃描 ====================

    def scan_for_signals(self):
        """掃描交易信號"""
        symbols = self.load_scanner_results() if Config.USE_SCANNER_SYMBOLS else Config.SYMBOLS
        logger.debug(f"開始掃描 {len(symbols)} 個標的...")  # 降噪

        for symbol in symbols:
            try:
                # 跳過已有持倉
                if symbol in self.active_trades:
                    t = self.active_trades[symbol]
                    logger.debug(f"{symbol}: 跳過（已有持倉 {t.side}/階段{t.stage}）")
                    continue

                # 冷卻檢查
                if symbol in self.recently_exited:
                    hours = (datetime.now(timezone.utc) - self.recently_exited[symbol]).total_seconds() / 3600
                    if hours < 2:
                        logger.debug(f"{symbol}: 跳過（冷卻中 {hours:.1f}h）")
                        continue
                    else:
                        del self.recently_exited[symbol]

                # 下單失敗黑名單
                if symbol in self.order_failed_symbols:
                    hours = (datetime.now(timezone.utc) - self.order_failed_symbols[symbol]).total_seconds() / 3600
                    if hours < 1:
                        logger.debug(f"{symbol}: 跳過（下單失敗黑名單）")
                        continue
                    else:
                        del self.order_failed_symbols[symbol]

                # 12h 冷卻（快速止損/超時退出）
                if symbol in self.early_exit_cooldown:
                    hours = (datetime.now(timezone.utc) - self.early_exit_cooldown[symbol]).total_seconds() / 3600
                    if hours < Config.EARLY_EXIT_COOLDOWN_HOURS:
                        logger.debug(f"{symbol}: 跳過（早期退出冷卻中 {hours:.1f}h/{Config.EARLY_EXIT_COOLDOWN_HOURS}h）")
                        continue
                    else:
                        del self.early_exit_cooldown[symbol]

                # === Risk Guard: 同幣虧損冷卻（persistent，基於 perf_db）===
                if Config.SYMBOL_LOSS_COOLDOWN_HOURS > 0:
                    last_loss_exit = self.perf_db.get_last_loss_exit_time(symbol)
                    if last_loss_exit:
                        try:
                            exit_dt = datetime.fromisoformat(last_loss_exit)
                            if exit_dt.tzinfo is None:
                                exit_dt = exit_dt.replace(tzinfo=timezone.utc)
                            hours_since = (datetime.now(timezone.utc) - exit_dt).total_seconds() / 3600
                            if hours_since < Config.SYMBOL_LOSS_COOLDOWN_HOURS:
                                logger.info(
                                    f"{symbol}: 跳過（上次虧損 {hours_since:.1f}h 前，"
                                    f"冷卻 {Config.SYMBOL_LOSS_COOLDOWN_HOURS}h）"
                                )
                                continue
                        except (ValueError, TypeError):
                            pass  # 解析失敗不阻塞

                # 總風險檢查
                active_list = list(self.active_trades.values())
                if not self._check_total_risk(active_list):
                    logger.debug("總風險已達上限，停止掃描")  # 降噪
                    break

                # 獲取數據
                df_trend = self.fetch_ohlcv(symbol, Config.TIMEFRAME_TREND, limit=250)
                df_signal = self.fetch_ohlcv(symbol, Config.TIMEFRAME_SIGNAL, limit=100)
                df_mtf = pd.DataFrame()
                if Config.ENABLE_MTF_CONFIRMATION:
                    df_mtf = self.fetch_ohlcv(symbol, Config.TIMEFRAME_MTF, limit=100)

                if df_trend.empty or len(df_trend) < 100:
                    logger.debug(f"{symbol}: 跳過（趨勢數據不足: {len(df_trend) if not df_trend.empty else 0}根）")
                    continue
                if df_signal.empty or len(df_signal) < 50:
                    logger.debug(f"{symbol}: 跳過（信號數據不足: {len(df_signal) if not df_signal.empty else 0}根）")
                    continue

                df_trend = TechnicalAnalysis.calculate_indicators(df_trend)
                df_signal = TechnicalAnalysis.calculate_indicators(df_signal)
                if not df_mtf.empty:
                    df_mtf = TechnicalAnalysis.calculate_indicators(df_mtf)

                # 市場過濾
                market_ok, market_reason, is_strong_market = MarketFilter.check_market_condition(df_trend, symbol)
                if not market_ok:
                    logger.info(f"{symbol}: 跳過（市場過濾: {market_reason}）")
                    continue

                # === 多策略信號掃描 ===
                signals_found = []

                # V6.0: 升級版 2B（用 swing pivot + neckline）
                has_2b, details_2b = detect_2b_with_pivots(
                    df_signal,
                    left_bars=Config.SWING_LEFT_BARS,
                    right_bars=Config.SWING_RIGHT_BARS,
                    vol_minimum_threshold=Config.VOL_MINIMUM_THRESHOLD,
                    accept_weak_signals=Config.ACCEPT_WEAK_SIGNALS,
                    enable_volume_grading=Config.ENABLE_VOLUME_GRADING,
                    vol_explosive_threshold=Config.VOL_EXPLOSIVE_THRESHOLD,
                    vol_strong_threshold=Config.VOL_STRONG_THRESHOLD,
                    vol_moderate_threshold=Config.VOL_MODERATE_THRESHOLD,
                    min_fakeout_atr=Config.MIN_FAKEOUT_ATR,
                )
                if has_2b and details_2b is not None:
                    details_2b['signal_type'] = '2B'
                    signals_found.append(('2B', details_2b))

                # EMA 回撤信號
                if Config.ENABLE_EMA_PULLBACK:
                    has_pb, details_pb = detect_ema_pullback(
                        df_signal,
                        ema_pullback_threshold=Config.EMA_PULLBACK_THRESHOLD,
                    )
                    if has_pb and details_pb is not None:
                        details_pb['signal_type'] = 'EMA_PULLBACK'
                        signals_found.append(('EMA_PULLBACK', details_pb))

                # 量能突破信號
                if Config.ENABLE_VOLUME_BREAKOUT:
                    has_bo, details_bo = detect_volume_breakout(
                        df_signal,
                        volume_breakout_mult=Config.VOLUME_BREAKOUT_MULT,
                    )
                    if has_bo and details_bo is not None:
                        details_bo['signal_type'] = 'VOLUME_BREAKOUT'
                        signals_found.append(('VOLUME_BREAKOUT', details_bo))

                if not signals_found:
                    logger.debug(f"{symbol}: 無信號（市場OK: {market_reason}）")
                    continue

                # 優先級排序：2B > VOLUME_BREAKOUT > EMA_PULLBACK
                _signal_priority = {'2B': 1, 'VOLUME_BREAKOUT': 2, 'EMA_PULLBACK': 3}
                signals_found.sort(key=lambda x: _signal_priority.get(x[0], 99))

                # 列出所有偵測到的信號
                all_sigs = ', '.join(
                    f"{t} {d['side']} 量能={d.get('vol_ratio',0):.2f}x"
                    for t, d in signals_found
                )
                best_type, signal_details = signals_found[0]
                logger.info(f"{symbol}: 偵測到信號 [{all_sigs}]")
                signal_side = signal_details['side']

                # 交易方向過濾
                trading_dir = Config.TRADING_DIRECTION.lower()
                if trading_dir == 'long' and signal_side != 'LONG':
                    logger.debug(f"{symbol}: 跳過（{best_type} {signal_side} 不符合方向=做多）")
                    continue
                if trading_dir == 'short' and signal_side != 'SHORT':
                    logger.debug(f"{symbol}: 跳過（{best_type} {signal_side} 不符合方向=做空）")
                    continue

                # 趨勢檢查
                trend_ok, trend_desc = TechnicalAnalysis.check_trend(df_trend, signal_side)
                if not trend_ok:
                    logger.info(f"{symbol}: 跳過（趨勢={trend_desc}，信號={signal_side} 方向不符）")
                    continue

                # MTF 確認
                mtf_aligned = True
                mtf_reason = "MTF 未啟用"
                if Config.ENABLE_MTF_CONFIRMATION and not df_mtf.empty:
                    mtf_aligned, mtf_reason = MTFConfirmation.check_mtf_alignment(df_mtf, signal_side)
                    logger.info(f"{symbol}: MTF {mtf_reason}")

                # 信號等級
                signal_tier, tier_multiplier, tier_score = SignalTierSystem.calculate_signal_tier(
                    signal_details, mtf_aligned, is_strong_market,
                    signal_details.get('signal_strength', 'moderate')
                )
                signal_details['signal_tier'] = signal_tier
                signal_details['market_regime'] = 'STRONG' if is_strong_market else 'TRENDING'
                signal_details['entry_adx'] = (
                    round(float(df_signal['adx'].iloc[-1]), 2)
                    if 'adx' in df_signal.columns and not pd.isna(df_signal['adx'].iloc[-1])
                    else None
                )
                signal_details['_market_reason'] = market_reason
                signal_details['_trend_desc'] = trend_desc
                signal_details['_mtf_reason'] = mtf_reason
                # Tier diagnostic fields（數據蒐集，不影響交易邏輯）
                signal_details['tier_score'] = tier_score
                signal_details['mtf_aligned'] = mtf_aligned
                signal_details['volume_grade'] = signal_details.get('signal_strength', 'moderate')
                # trend_adx: 用 df_trend (1D) 的 ADX，而非 df_signal (1H)
                signal_details['trend_adx'] = (
                    round(float(df_trend['adx'].iloc[-1]), 2)
                    if 'adx' in df_trend.columns and len(df_trend) > 0
                    and not pd.isna(df_trend['adx'].iloc[-1])
                    else None
                )

                # === Risk Guard: BTC Trend Filter ===
                if Config.BTC_TREND_FILTER_ENABLED and "BTC" not in symbol:
                    btc_trend = self._check_btc_trend()
                    if btc_trend is not None and signal_side != btc_trend:
                        if Config.BTC_COUNTER_TREND_MULT <= 0:
                            logger.info(
                                f"{symbol}: 跳過（BTC 趨勢={btc_trend}，信號={signal_side} 逆勢，"
                                f"BTC_COUNTER_TREND_MULT=0）"
                            )
                            continue
                        else:
                            # 降倉：乘以逆勢乘數
                            tier_multiplier *= Config.BTC_COUNTER_TREND_MULT
                            logger.info(
                                f"{symbol}: BTC 逆勢（BTC={btc_trend}，信號={signal_side}），"
                                f"倉位乘數 ×{Config.BTC_COUNTER_TREND_MULT}"
                            )

                logger.info(
                    f"準備進場: {symbol} {best_type} {signal_side} | "
                    f"等級={signal_tier} 量能={signal_details.get('vol_ratio', 0):.2f}x | "
                    f"市場={market_reason} 趨勢={trend_desc} MTF={'通過' if mtf_aligned else '未通過'}"
                )

                # 執行開倉
                self._execute_trade(symbol, signal_details, best_type, tier_multiplier, df_signal)

            except Exception as e:
                logger.error(f"{symbol} 掃描錯誤: {e}")

        active_str = ', '.join(
            f'{s}({t.side}/階段{t.stage}/${t.total_size * t.avg_entry:.0f})'
            for s, t in self.active_trades.items()
        ) or "無"
        logger.debug(f"掃描完成 | 活躍持倉: {active_str}")  # 降噪

        # Structured scan summary (will be supplemented by monitor CYCLE_SUMMARY)
        _trade_log({
            'event': 'CYCLE_SUMMARY',
            'ts': datetime.now(timezone.utc).isoformat(),
            'bot': 'v6.0',
            'cycle': getattr(self, 'cycle_count', 0),
            'active': len(self.active_trades),
            'closed': 0,
            'symbols': active_str.replace(' ', ''),
        })

    # ==================== Private Helpers ====================

    def _check_btc_trend(self) -> Optional[str]:
        """Fetch BTC 1D EMA20/50 trend. Returns 'LONG', 'SHORT', or None on failure."""
        try:
            btc_df = self.data_provider.fetch_ohlcv("BTC/USDT", "1d", limit=60)
            if btc_df is not None and len(btc_df) >= 50:
                btc_ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                btc_ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                return "LONG" if btc_ema20 > btc_ema50 else "SHORT"
        except Exception as e:
            logger.warning(f"BTC trend check failed: {e}")
        return None

    def _refresh_stop_loss(self, pm: PositionManager, new_sl: float):
        """Cancel existing SL order, place new one, update pm.stop_order_id."""
        self._cancel_stop_loss_order(pm.symbol, pm.stop_order_id)
        pm.stop_order_id = self._place_hard_stop_loss(
            pm.symbol, pm.side, pm.total_size, new_sl
        )

    def _calc_total_risk_pct(self, balance: float) -> float:
        """計算所有活躍持倉的總風險佔比"""
        if balance <= 0:
            return 0.0
        total_risk = 0.0
        for p in self.active_trades.values():
            if p.is_closed:
                continue
            sl_dist_pct = abs(p.avg_entry - p.current_sl) / p.avg_entry if p.avg_entry > 0 else 0
            position_risk = sl_dist_pct * p.total_size * p.avg_entry
            total_risk += position_risk
        return total_risk / balance

    @staticmethod
    def _get_close_side(side: str) -> str:
        """Return exchange order side for closing a position."""
        return 'BUY' if side == 'LONG' else 'SELL'

    def _validate_position_size(self, symbol: str, raw_size: float, entry_price: float,
                                 label: str = "") -> Optional[float]:
        """Round amount and check limits. Returns size or None if below minimum."""
        size = self.precision_handler.round_amount_up(symbol, raw_size, entry_price)
        if not self.precision_handler.check_limits(symbol, size, entry_price):
            logger.warning(f"{symbol}{(' ' + label) if label else ''} 低於最小值")
            return None
        return size

    @staticmethod
    def _calculate_pnl(side: str, size: float, price: float, avg_entry: float) -> float:
        """Calculate unrealised/realised PnL for a position."""
        if side == 'LONG':
            return size * (price - avg_entry)
        return size * (avg_entry - price)

    @staticmethod
    def _build_log_base(event: str, trade_id: str, symbol: str, side: str) -> dict:
        """Build common fields for _trade_log calls."""
        return {
            'event': event,
            'trade_id': trade_id,
            'ts': datetime.now(timezone.utc).isoformat(),
            'bot': 'v6.0',
            'symbol': symbol,
            'side': side,
        }

    def _check_total_risk(self, active_positions: List[PositionManager]) -> bool:
        """總風險檢查（改用 PositionManager）"""
        if not active_positions:
            return True

        total_risk = 0.0
        for pm in active_positions:
            if pm.is_closed:
                continue
            if pm.side == 'LONG':
                risk_per_unit = pm.avg_entry - pm.current_sl
            else:
                risk_per_unit = pm.current_sl - pm.avg_entry
            if risk_per_unit <= 0:
                continue
            total_risk += pm.total_size * risk_per_unit

        if Config.V6_DRY_RUN:
            balance = 10000.0
        else:
            balance = self.risk_manager.get_balance()
        if balance <= 0:
            return False
        return (total_risk / balance) <= Config.MAX_TOTAL_RISK

    # ==================== 開倉執行 ====================

    def _execute_trade(self, symbol: str, signal_details: Dict, signal_type: str,
                       tier_multiplier: float, df_signal: pd.DataFrame):
        """執行開倉"""
        try:
            if symbol in self.active_trades:
                return

            if Config.V6_DRY_RUN:
                balance = 10000.0  # Dry run: mock balance
            else:
                balance = self.risk_manager.get_balance()
                if balance <= 0:
                    logger.error(f"{symbol}: 餘額不足")
                    return

            side = signal_details['side']
            entry_price = signal_details['entry_price']
            atr = signal_details.get('atr', 0)

            # 判斷是否用 V6.0 滾倉
            use_v6 = (
                Config.PYRAMID_ENABLED
                and signal_type == '2B'
                and Config.STRATEGY_USE_V6.get('2B_BREAKOUT', True)
            )

            if use_v6:
                # V6.0: 止損用 signal 的 stop_loss（swing point + buffer）
                stop_loss = signal_details.get('stop_loss', signal_details.get('stop_level', entry_price))
                neckline = signal_details.get('neckline')

                # V6.0: Stage 1 size = equity_cap * stage1_ratio
                stage1_value = balance * Config.EQUITY_CAP_PERCENT * Config.STAGE1_RATIO
                raw_size = stage1_value / entry_price
                raw_size *= tier_multiplier

                position_size = self._validate_position_size(symbol, raw_size, entry_price, "V6")
                if position_size is None:
                    return

                initial_r = position_size * abs(entry_price - stop_loss)

            else:
                # V5.3: 原有 risk-based sizing
                if side == 'LONG':
                    extreme = signal_details.get('lowest_point', signal_details.get('stop_level'))
                else:
                    extreme = signal_details.get('highest_point', signal_details.get('stop_level'))

                stop_loss = self.risk_manager.calculate_stop_loss(extreme, atr, side, df_signal)
                neckline = None

                position_size = self.risk_manager.calculate_position_size(
                    symbol, balance, entry_price, stop_loss, tier_multiplier
                )
                if position_size <= 0:
                    return

                # V5.3 equity cap：獨立上限，避免緊止損暴倉
                v53_notional = position_size * entry_price
                v53_cap_notional = balance * Config.V53_EQUITY_CAP_PERCENT
                if v53_notional > v53_cap_notional:
                    capped_size = self.precision_handler.round_amount(symbol, v53_cap_notional / entry_price)
                    logger.info(
                        f"{symbol}: V5.3 notional 截頂 "
                        f"${v53_notional:.2f} -> ${v53_cap_notional:.2f} "
                        f"(v53_equity_cap={Config.V53_EQUITY_CAP_PERCENT*100:.0f}%)"
                    )
                    position_size = capped_size

                initial_r = balance * Config.RISK_PER_TRADE

            # === Risk Guard: SL Distance Cap ===
            sl_distance_pct = abs(entry_price - stop_loss) / entry_price
            if sl_distance_pct > Config.MAX_SL_DISTANCE_PCT:
                logger.info(
                    f"{symbol}: 跳過（SL 距離 {sl_distance_pct:.1%} > 上限 "
                    f"{Config.MAX_SL_DISTANCE_PCT:.0%}，entry={entry_price} sl={stop_loss}）"
                )
                return

            # === Dry run 模式 ===
            if Config.V6_DRY_RUN:
                logger.info(
                    f"[模擬] {symbol} {side} | 策略={signal_type} | "
                    f"倉位={position_size:.6f} @ ${entry_price:.2f} | "
                    f"止損=${stop_loss:.2f} | neckline={'$' + f'{neckline:.2f}' if neckline else '無'} | "
                    f"滾倉={use_v6}"
                )
                # Dry run 也生成 trade_id 用於測試
                dry_trade_id = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + symbol.replace('/', '')
                _trade_log({
                    **self._build_log_base('TRADE_OPEN', dry_trade_id, symbol, side),
                    'strategy': signal_type,
                    'tier': signal_details.get('signal_tier', '?'),
                    'size': f'{position_size:.6f}',
                    'entry': f'{entry_price:.2f}',
                    'sl': f'{stop_loss:.2f}',
                    'value': f'{position_size * entry_price:.2f}',
                    'risk': f'{initial_r:.2f}',
                    'v6': use_v6,
                    'vol_ratio': f'{signal_details.get("vol_ratio", 0):.2f}',
                    'regime': signal_details.get('market_regime', 'UNKNOWN'),
                    'btc_trend': signal_details.get('btc_trend', 'UNKNOWN'),
                    'initial_r': f'{initial_r:.2f}',
                })
                return

            # === 實際下單 ===
            order_side = self._get_close_side(side)
            if BinanceFuturesClient.is_enabled():
                order_result = self._futures_create_order(symbol, order_side, position_size)
            else:
                order_result = self.exchange.create_order(
                    symbol=symbol, type='market', side=order_side.lower(), amount=position_size
                )

            # 捕捉實際成交均價（market order 可能有 slippage）
            fill_price = self._extract_fill_price(order_result, entry_price)
            if fill_price != entry_price:
                logger.info(
                    f"{symbol} 成交均價修正: 信號${entry_price:.4f} → 實際${fill_price:.4f}"
                )
            entry_price = fill_price

            logger.info(
                f"{symbol} {side} 開倉成功: {position_size:.6f} @ ${entry_price:.2f} | "
                f"止損=${stop_loss:.2f} 策略={signal_type} 等級={signal_details.get('signal_tier','?')} "
                f"量能={signal_details.get('vol_ratio',0):.2f}x 滾倉={use_v6} | "
                f"市場={signal_details.get('_market_reason','')} 趨勢={signal_details.get('_trend_desc','')} "
                f"MTF={signal_details.get('_mtf_reason','')}"
            )

            # 建立 PositionManager（strategy_name 由 SIGNAL_STRATEGY_MAP 決定）
            strategy_name = Config.SIGNAL_STRATEGY_MAP.get(signal_type, "v6_pyramid")
            pm = PositionManager(
                symbol=symbol,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                position_size=position_size,
                strategy_name=strategy_name,
                neckline=neckline,
                equity_base=balance,
                initial_r=initial_r,
                signal_tier=signal_details.get('signal_tier', 'B'),
                market_regime=signal_details.get('market_regime', 'UNKNOWN'),
            )
            pm.atr = atr
            pm.entry_adx = signal_details.get('entry_adx')
            pm.fakeout_depth_atr = signal_details.get('fakeout_depth_atr')
            pm.trend_adx = signal_details.get('trend_adx')
            pm.mtf_aligned = signal_details.get('mtf_aligned')
            pm.volume_grade = signal_details.get('volume_grade')
            pm.tier_score = signal_details.get('tier_score')

            # --- BTC Trend Alignment (data collection only) ---
            if "BTC" not in symbol:  # BTC/USDT 自身不適用
                btc_trend = self._check_btc_trend()
                pm.btc_trend_aligned = (side == btc_trend) if btc_trend is not None else None
            else:
                pm.btc_trend_aligned = None

            # Structured trade log
            _trade_log({
                **self._build_log_base('TRADE_OPEN', pm.trade_id, symbol, side),
                'strategy': signal_type,
                'tier': signal_details.get('signal_tier', '?'),
                'size': f'{position_size:.6f}',
                'entry': f'{entry_price:.2f}',
                'sl': f'{stop_loss:.2f}',
                'value': f'{position_size * entry_price:.2f}',
                'risk': f'{initial_r:.2f}',
                'v6': use_v6,
                'vol_ratio': f'{signal_details.get("vol_ratio", 0):.2f}',
                'regime': signal_details.get('market_regime', 'UNKNOWN'),
                'btc_trend': signal_details.get('btc_trend', 'UNKNOWN'),
                'initial_r': f'{initial_r:.2f}',
            })

            # 設置硬止損
            pm.stop_order_id = self._place_hard_stop_loss(symbol, side, position_size, stop_loss)

            self.active_trades[symbol] = pm

            # 持久化
            self._save_positions()

            # Telegram 通知
            TelegramNotifier.notify_signal(symbol, {
                **signal_details,
                'position_size': position_size,
                'stop_loss': stop_loss,
                'is_v6': use_v6,
                'neckline': neckline,
            })

        except Exception as e:
            logger.error(f"{symbol} 開倉失敗: {e}")
            self.order_failed_symbols[symbol] = datetime.now(timezone.utc)

    # ==================== 持倉監控 ====================

    def monitor_positions(self):
        """監控持倉"""
        if not self.active_trades:
            return

        logger.debug(f"監控 {len(self.active_trades)} 個持倉中...")

        closed_symbols = []
        state_changed = False

        for symbol, pm in self.active_trades.items():
            try:
                if pm.is_closed:
                    closed_symbols.append(symbol)
                    continue

                # 取得 ticker
                ticker = self.fetch_ticker(symbol)
                current_price = ticker['last']

                # 取得 1H 數據
                df_1h = self.fetch_ohlcv(symbol, Config.TIMEFRAME_SIGNAL, limit=50)
                if not df_1h.empty:
                    df_1h = TechnicalAnalysis.calculate_indicators(df_1h)

                # V6 / V7: 額外取得 4H 數據
                df_4h = None
                if pm.strategy_name in ("v6_pyramid", "v7_structure"):
                    df_4h = self.fetch_ohlcv(symbol, '4h', limit=50)
                    if df_4h is not None and not df_4h.empty:
                        df_4h = TechnicalAnalysis.calculate_indicators(df_4h)

                # Monitor（V7 P2 起回傳 Dict）
                decision = pm.monitor(current_price, df_1h, df_4h)
                action = decision.get('action', Action.HOLD)
                new_sl = decision.get('new_sl')

                # SL 變化 → 更新硬止損
                if new_sl is not None:
                    old_sl = pm.current_sl
                    self._update_hard_stop_loss(pm, new_sl)
                    state_changed = True
                    # 只通知顯著移損（變化 > 1%），避免 trailing 微調洗版
                    if old_sl > 0 and abs(new_sl - old_sl) / old_sl > 0.01:
                        TelegramNotifier.notify_action(
                            symbol, '1.5R移損',
                            current_price,
                            f"SL ${old_sl:.2f} → ${new_sl:.2f}"
                        )

                # 通用 action dispatch
                if action == Action.CLOSE:
                    if self._handle_close(pm, current_price):
                        closed_symbols.append(symbol)
                        state_changed = True
                    # 失敗時不加入 closed_symbols，保留持倉待下一週期重試

                elif action == Action.ADD:
                    stage = decision.get('add_stage', 2)
                    if stage == 2:
                        self._handle_stage2(pm, current_price, df_1h, decision=decision)
                    else:
                        self._handle_stage3(pm, current_price, df_1h, decision=decision)
                    state_changed = True

                elif action == Action.PARTIAL_CLOSE:
                    close_pct = decision.get('close_pct', 0.3)
                    pct_int = round(close_pct * 100)
                    reason = decision.get('reason', 'PARTIAL_CLOSE')
                    label = "2.5R" if "25R" in reason else "1.5R"
                    self._handle_v53_reduce(pm, pct_int, label, current_price)
                    state_changed = True

                # 記錄狀態
                if pm.side == 'LONG':
                    profit_pct = (current_price - pm.avg_entry) / pm.avg_entry * 100
                else:
                    profit_pct = (pm.avg_entry - current_price) / pm.avg_entry * 100

                if pm.strategy_name == "v7_structure":
                    mode = f"V7/S{pm.stage}"
                elif pm.strategy_name == "v6_pyramid":
                    mode = f"V6/S{pm.stage}"
                else:
                    mode = "V53"
                logger.debug(
                    f"{symbol} [{mode}]: ${current_price:.2f} | "
                    f"PnL={profit_pct:+.2f}% | SL=${pm.current_sl:.2f}"
                )

                # Structured position update
                _trade_log({
                    **self._build_log_base('POSITION_UPDATE', pm.trade_id, symbol, pm.side),
                    'price': f'{current_price:.2f}',
                    'pnl_pct': f'{profit_pct:+.2f}',
                    'sl': f'{pm.current_sl:.2f}',
                    'stage': pm.stage,
                    'mode': mode,
                })

            except Exception as e:
                logger.error(f"{symbol} 監控錯誤: {e}")

            # 背景清理待取消止損單
            if pm.pending_stop_cancels:
                order_id = pm.pending_stop_cancels[0]
                try:
                    success = self.execution_engine.cancel_stop_loss_order(pm.symbol, order_id)
                    if success:
                        pm.pending_stop_cancels.pop(0)
                        logger.info(f"[{pm.symbol}] pending stop cancel cleared: {order_id}")
                except Exception as e:
                    logger.warning(f"[{pm.symbol}] pending stop cancel retry failed: {e}")
                    # 保留在清單，下次迴圈繼續重試

        # 清理已關閉的
        for symbol in closed_symbols:
            pm = self.active_trades.get(symbol)
            if pm:
                # 在刪除前清理殘留止損單（防止舊 algo order 影響未來倉位）
                for order_id in pm.pending_stop_cancels:
                    try:
                        self.execution_engine.cancel_stop_loss_order(pm.symbol, order_id)
                        logger.info(f"[{pm.symbol}] 平倉清理殘留止損: {order_id}")
                    except Exception as e:
                        logger.warning(f"[{pm.symbol}] 清理殘留止損失敗（可能已觸發）: {order_id} — {e}")

                if pm.exit_reason in ('early_stop_r', 'stage1_timeout'):
                    self.early_exit_cooldown[symbol] = datetime.now(timezone.utc)

            if symbol in self.active_trades:
                del self.active_trades[symbol]
                self.recently_exited[symbol] = datetime.now(timezone.utc)

        # 狀態有變化就儲存
        if state_changed or closed_symbols:
            self._save_positions()

        logger.debug(f"監控完成 | 剩餘持倉: {len(self.active_trades)}")  # 降噪

        # Structured cycle summary
        active_summary = ','.join(
            f'{s}({t.side}/S{t.stage}/${t.total_size * t.avg_entry:.0f})'
            for s, t in self.active_trades.items()
        ) or "none"

        # === [新增] 帳戶餘額與未實現 PnL ===
        cycle_balance = self.risk_manager.get_balance() if not Config.V6_DRY_RUN else 10000.0
        cycle_unrealized_pnl = 0.0
        for pos in self.active_trades.values():
            try:
                current_price = self.fetch_ticker(pos.symbol)['last']
                if current_price and pos.avg_entry and pos.total_size:
                    if pos.side == 'LONG':
                        pnl = (current_price - pos.avg_entry) * pos.total_size
                    else:  # SHORT
                        pnl = (pos.avg_entry - current_price) * pos.total_size
                    cycle_unrealized_pnl += pnl
            except Exception:
                pass  # 避免單一持倉計算失敗影響整體
        # === [新增結束] ===

        net_pnl_pct = round(
            (cycle_balance - self.initial_balance) / self.initial_balance * 100, 2
        ) if self.initial_balance else 0.0
        _trade_log({
            'event': 'CYCLE_SUMMARY',
            'ts': datetime.now(timezone.utc).isoformat(),
            'bot': 'v6.0',
            'cycle': getattr(self, 'cycle_count', 0),
            'active': len(self.active_trades),
            'active_trades_count': len(self.active_trades),
            'closed': len(closed_symbols),
            'symbols': active_summary,
            'balance': f'{cycle_balance:.2f}',
            'unrealized_pnl': f'{cycle_unrealized_pnl:.2f}',
            'net_pnl_pct': f'{net_pnl_pct:+.2f}',
        })

    def _fetch_exchange_stop_map(self) -> Dict[str, float]:
        """
        從交易所取得開放中的止損單。

        先嘗試 algo orders（正式網），若不支援（Demo Trading 回 404）
        再 fallback 到普通 openOrders 中的 STOP_MARKET 訂單。

        Returns:
            {symbol_id: trigger_price}，例如 {'BTCUSDT': 87500.0}
            查不到或 API 失敗回傳 {}
        """
        if not BinanceFuturesClient.is_enabled():
            return {}
        stop_map: Dict[str, float] = {}
        try:
            # 嘗試 algo orders（正式網支援）
            response = self.risk_manager.futures_client.signed_request(
                'GET', '/fapi/v1/algoOrder/openOrders'
            )
            if response.status_code == 200:
                for o in response.json().get('orders', []):
                    sym = o.get('symbol', '')
                    trigger = o.get('triggerPrice') or o.get('stopPrice')
                    if sym and trigger:
                        stop_map[sym] = float(trigger)
                return stop_map
            # algo endpoint 不支援（Demo Trading 回 404）→ fallback 到普通訂單
            logger.debug(f"[ADOPT] algo openOrders 不支援({response.status_code})，改查普通止損單")
        except Exception as e:
            logger.warning(f"[ADOPT] 查 algo 止損單異常: {e}")

        try:
            # Fallback：普通 openOrders 裡的 STOP_MARKET
            response = self.risk_manager.futures_client.signed_request(
                'GET', '/fapi/v1/openOrders'
            )
            if response.status_code == 200:
                for o in response.json():
                    if o.get('type') in ('STOP_MARKET', 'STOP'):
                        sym = o.get('symbol', '')
                        trigger = o.get('stopPrice') or o.get('triggerPrice')
                        if sym and trigger:
                            stop_map[sym] = float(trigger)
        except Exception as e:
            logger.warning(f"[ADOPT] 查普通止損單異常: {e}")

        return stop_map

    def _adopt_ghost_positions(self):
        """
        啟動後一次性接管幽靈倉位（exchange 有、positions.json 未記錄）。

        接管邏輯：
        1. 從交易所取 positions
        2. 跳過已追蹤的 symbol
        3. 查 algo 止損單 → 取得 stop_loss；若無，用入場價 × 2% 保守預設
        4. 建 PositionManager（V5.3 保守模式，is_v6_pyramid=False）
        5. 若無止損 → 補設硬止損
        6. 存入 active_trades + _save_positions()
        """
        if Config.V6_DRY_RUN:
            return

        exchange_positions = self.risk_manager.get_positions()
        if not exchange_positions:  # None 或 []
            return

        stop_map = self._fetch_exchange_stop_map()
        adopted = 0

        for pos in exchange_positions:
            sym_id = pos.get('symbol', '') or pos.get('info', {}).get('symbol', '')
            if not sym_id:
                continue

            # 轉 ccxt 格式：BTCUSDT → BTC/USDT
            ccxt_sym = sym_id[:-4] + '/' + sym_id[-4:] if sym_id.endswith('USDT') else sym_id

            # 已追蹤 → 跳過
            if ccxt_sym in self.active_trades:
                continue

            # 解析 side / size / entry
            raw_amt = float(pos.get('positionAmt', 0))
            if raw_amt == 0:
                continue
            side = 'LONG' if raw_amt > 0 else 'SHORT'
            position_size = abs(raw_amt)
            entry_price = float(
                pos.get('entryPrice', 0) or pos.get('info', {}).get('entryPrice', 0)
            )
            if entry_price <= 0:
                logger.warning(f"[ADOPT] {ccxt_sym} entryPrice 無效，跳過")
                continue

            # 取得或預算止損
            stop_loss = stop_map.get(sym_id)
            stop_source = 'exchange'
            if stop_loss is None:
                fallback_pct = getattr(Config, 'GHOST_ADOPT_SL_PCT', 0.02)
                stop_loss = (
                    entry_price * (1 - fallback_pct) if side == 'LONG'
                    else entry_price * (1 + fallback_pct)
                )
                stop_source = f'fallback({fallback_pct * 100:.0f}%)'

            # 建 PositionManager（V5.3 保守模式，不做 pyramid 加倉）
            pm = PositionManager(
                symbol=ccxt_sym,
                side=side,
                entry_price=entry_price,
                stop_loss=stop_loss,
                position_size=position_size,
                strategy_name="v53_sop",
                initial_r=position_size * abs(entry_price - stop_loss),
            )
            pm.entry_time = datetime.now(timezone.utc)
            pm.highest_price = entry_price
            pm.lowest_price = entry_price

            # 若無止損單 → 補設
            if stop_map.get(sym_id) is None:
                try:
                    order_id = self.execution_engine.place_hard_stop_loss(
                        ccxt_sym, side, position_size, stop_loss
                    )
                    pm.stop_order_id = order_id
                    logger.info(f"[ADOPT] {ccxt_sym} 補設硬止損 @ ${stop_loss:.4f}")
                except Exception as e:
                    logger.warning(f"[ADOPT] {ccxt_sym} 補設止損失敗: {e}")

            self.active_trades[ccxt_sym] = pm
            adopted += 1
            logger.warning(
                f"[GHOST_ADOPTED] {ccxt_sym}: {side} size={position_size} "
                f"entry=${entry_price:.4f} sl=${stop_loss:.4f} "
                f"(stop_source={stop_source})"
            )

        if adopted > 0:
            self._save_positions()
            logger.warning(f"[ADOPT] 共接管 {adopted} 個幽靈倉位，已存入 positions.json")

    def _sync_exchange_positions(self):
        """
        交易所倉位 reconciliation（每次 monitor_positions 都執行）。

        四重防護：
        1. API 錯誤防護：get_positions 回 None 時跳過（不誤殺）
        2. 正向檢查：bot 有 / exchange 無 → hard_stop_hit
        3. Size 校驗：兩邊都有但數量不一致 → 告警
        4. 反向檢查：exchange 有 / bot 無 → 幽靈倉位告警
        """
        if Config.V6_DRY_RUN:
            return
        try:
            exchange_positions = self.risk_manager.get_positions()

            # === 防護 1：API 錯誤 → 跳過同步 ===
            if exchange_positions is None:
                logger.warning(
                    "[SYNC] 交易所持倉查詢失敗，跳過本次同步（防止誤判 hard_stop_hit）"
                )
                return

            # 建立 exchange position map: {symbol_id: amount}
            exchange_map: Dict[str, float] = {}
            for pos in exchange_positions:
                sym = pos.get('symbol', '') or pos.get('info', {}).get('symbol', '')
                if sym:
                    amt = abs(float(pos.get('positionAmt', 0) or pos.get('contracts', 0)))
                    exchange_map[sym] = amt

            hard_stop_detected = False

            # === 防護 2：正向檢查 — bot 有、exchange 無 → hard_stop_hit ===
            for symbol, pm in list(self.active_trades.items()):
                symbol_id = symbol.replace('/', '')
                ex_amt = exchange_map.get(symbol_id, exchange_map.get(symbol))

                if ex_amt is None or ex_amt == 0:
                    logger.warning(
                        f"[SYNC] {symbol} 交易所已無此持倉，推測硬止損已觸發（HARD_STOP_HIT）"
                    )
                    pm.exit_reason = 'hard_stop_hit'
                    pm.is_closed = True
                    hard_stop_detected = True
                    TelegramNotifier.notify_action(
                        symbol, '硬止損觸發',
                        pm.current_sl,
                        f"交易所已無持倉，推測硬止損已觸發"
                    )
                else:
                    # === 防護 3：Size 校驗 — 數量不一致 → 告警 ===
                    bot_amt = pm.total_size
                    if bot_amt > 0 and abs(ex_amt - bot_amt) / bot_amt > 0.05:
                        logger.warning(
                            f"[SIZE_MISMATCH] {symbol}: "
                            f"bot={bot_amt:.6f} vs exchange={ex_amt:.6f} "
                            f"(差異 {abs(ex_amt - bot_amt):.6f})"
                        )

            # === 防護 4：反向檢查 — exchange 有、bot 沒有 → 幽靈倉位 ===
            bot_symbol_ids = {s.replace('/', '') for s in self.active_trades}
            for sym, ex_amt in exchange_map.items():
                if sym not in bot_symbol_ids and ex_amt > 0:
                    ccxt_sym = sym[:-4] + '/' + sym[-4:] if sym.endswith('USDT') else sym
                    logger.warning(
                        f"[GHOST_POSITION] {ccxt_sym}: "
                        f"交易所有倉位 {ex_amt:.6f}，但 bot 未追蹤！請手動檢查。"
                    )

            if hard_stop_detected:
                self._save_positions()

        except Exception as e:
            logger.warning(f"[SYNC] 交易所同步異常，跳過: {e}")

    def _handle_close(self, pm: PositionManager, current_price: float = 0.0) -> bool:
        """
        處理平倉。

        Returns:
            True  — 平倉成功，呼叫方應移除持倉
            False — 平倉失敗，pm.is_closed 維持 False，待下一週期重試
        """
        try:
            # 如果沒有傳入 current_price（exchange_sync），嘗試取得
            if current_price <= 0:
                try:
                    ticker = self.fetch_ticker(pm.symbol)
                    current_price = ticker['last']
                except Exception:
                    current_price = pm.avg_entry  # fallback

            # 計算 PnL（合計 partial closes + final close）
            final_pnl = self._calculate_pnl(pm.side, pm.total_size, current_price, pm.avg_entry)

            pnl_usdt = final_pnl + pm.realized_partial_pnl
            original_notional = pm.original_size * pm.avg_entry
            pnl_pct = (pnl_usdt / original_notional * 100) if original_notional > 0 else 0

            duration_h = (datetime.now(timezone.utc) - pm.entry_time).total_seconds() / 3600
            exit_reason = getattr(pm, 'exit_reason', None) or 'unknown'

            # Safety fallback：確保平倉當下的價格納入 highest/lowest 追蹤
            # 防止快速出場（< 1 monitor cycle）或 exchange_sync 路徑導致 MFE/MAE = 0
            pm.highest_price = max(pm.highest_price, current_price)
            pm.lowest_price = min(pm.lowest_price, current_price)

            # 計算 MFE / MAE / Realized R / Capture Ratio
            avg_entry = pm.avg_entry
            if avg_entry and avg_entry > 0:
                if pm.side == 'LONG':
                    mfe_pct = round((pm.highest_price - avg_entry) / avg_entry * 100, 4)
                    mae_pct = round((pm.lowest_price - avg_entry) / avg_entry * 100, 4)
                else:
                    mfe_pct = round((avg_entry - pm.lowest_price) / avg_entry * 100, 4)
                    mae_pct = round((avg_entry - pm.highest_price) / avg_entry * 100, 4)
            else:
                mfe_pct = 0.0
                mae_pct = 0.0
            realized_r = round(pnl_usdt / pm.initial_r, 2) if pm.initial_r else 0.0
            capture_ratio = round(pnl_pct / mfe_pct, 2) if mfe_pct > 0.0001 else None
            holding_time_min = round(duration_h * 60, 1)

            if Config.V6_DRY_RUN:
                logger.info(f"[模擬] 平倉 {pm.symbol} {pm.side} 倉位={pm.total_size:.6f}")
                _trade_log({
                    **self._build_log_base('TRADE_CLOSE', pm.trade_id, pm.symbol, pm.side),
                    'exit_price': f'{current_price:.2f}',
                    'entry': f'{pm.avg_entry:.2f}',
                    'size': f'{pm.total_size:.6f}',
                    'pnl_pct': f'{pnl_pct:+.2f}',
                    'pnl_usdt': f'{pnl_usdt:+.2f}',
                    'exit_reason': exit_reason,
                    'duration_h': f'{duration_h:.1f}',
                    'holding_time_min': f'{holding_time_min}',
                    'stage': pm.stage,
                    'realized_r': f'{realized_r:.2f}',
                    'mfe_pct': f'{mfe_pct:.4f}',
                    'mae_pct': f'{mae_pct:.4f}',
                    'capture_ratio': f'{capture_ratio or 0:.2f}',
                })
                return True

            # --- 止損單 → 放入 pending_stop_cancels（非阻塞），平倉優先 ---
            if pm.stop_order_id:
                pm.pending_stop_cancels.append(pm.stop_order_id)
                pm.stop_order_id = None

            # --- 平倉下單（失敗則 rollback：保留持倉狀態，寫入 positions.json 待下週期重試）---
            try:
                self._futures_close_position(pm.symbol, pm.side, pm.total_size)
            except Exception as close_err:
                logger.error(
                    f"{pm.symbol} 平倉下單失敗（持倉狀態保留，待下一週期重試）: {close_err}"
                )
                # rollback：is_closed 維持 False，寫入 positions.json 確保重啟後能復原
                self._save_positions()
                return False

            logger.info(f"{pm.symbol} 已平倉: {pm.side} 倉位={pm.total_size:.6f}")

            # Structured trade log
            _trade_log({
                **self._build_log_base('TRADE_CLOSE', pm.trade_id, pm.symbol, pm.side),
                'exit_price': f'{current_price:.2f}',
                'entry': f'{pm.avg_entry:.2f}',
                'size': f'{pm.total_size:.6f}',
                'pnl_pct': f'{pnl_pct:+.2f}',
                'pnl_usdt': f'{pnl_usdt:+.2f}',
                'exit_reason': exit_reason,
                'duration_h': f'{duration_h:.1f}',
                'holding_time_min': f'{holding_time_min}',
                'stage': pm.stage,
                'realized_r': f'{realized_r:.2f}',
                'mfe_pct': f'{mfe_pct:.4f}',
                'mae_pct': f'{mae_pct:.4f}',
                'capture_ratio': f'{capture_ratio or 0:.2f}',
            })

            # === Phase 0: 寫入績效 DB ===
            # capture_ratio 僅在 mfe_pct > 0 時有意義
            safe_capture = round(pnl_pct / mfe_pct, 4) if mfe_pct > 0.0001 else None

            self.perf_db.record_trade({
                "trade_id":      pm.trade_id,
                "symbol":        pm.symbol,
                "side":          pm.side,
                "is_v6_pyramid": int(pm.is_v6_pyramid),
                "signal_tier":   pm.signal_tier,
                "entry_price":   pm.avg_entry,
                "exit_price":    current_price,
                "total_size":    pm.total_size,
                "initial_r":     pm.initial_r,
                "entry_time":    pm.entry_time.isoformat() if hasattr(pm.entry_time, 'isoformat') else str(pm.entry_time),
                "exit_time":     datetime.now(timezone.utc).isoformat(),
                "holding_hours": duration_h,
                "pnl_usdt":      pnl_usdt,
                "pnl_pct":       pnl_pct,
                "realized_r":    realized_r,
                "mfe_pct":       mfe_pct,
                "mae_pct":       mae_pct,
                "capture_ratio": safe_capture,
                "stage_reached":   pm.stage,
                "exit_reason":     exit_reason,
                "market_regime":   pm.market_regime,
                "entry_adx":          getattr(pm, 'entry_adx', None),
                "fakeout_depth_atr":  getattr(pm, 'fakeout_depth_atr', None),
                "reverse_2b_depth_atr": getattr(pm, 'reverse_2b_depth_atr', None),
                "original_size":       pm.original_size,
                "partial_pnl_usdt":    pm.realized_partial_pnl,
                "btc_trend_aligned":   getattr(pm, 'btc_trend_aligned', None),
                "trend_adx":       getattr(pm, 'trend_adx', None),
                "mtf_aligned":     int(pm.mtf_aligned) if getattr(pm, 'mtf_aligned', None) is not None else None,
                "volume_grade":    getattr(pm, 'volume_grade', None),
                "tier_score":      getattr(pm, 'tier_score', None),
            })

            # Telegram
            TelegramNotifier.notify_exit(pm.symbol, {
                'side': pm.side,
                'entry_price': pm.avg_entry,
                'exit_reason': exit_reason,
                'position_size': pm.total_size,
                'pnl_pct': pnl_pct,
            })

            return True

        except Exception as e:
            logger.error(f"{pm.symbol} _handle_close 發生意外錯誤: {e}")
            return False

    def _handle_stage2(self, pm: PositionManager, current_price: float, df_1h, decision: dict = None):
        """處理 Stage 2 加倉"""
        try:
            entry_price = current_price

            # V7: 用策略的 calculate_add_size + decision 中的 new_sl
            if pm.strategy_name == 'v7_structure':
                from trader.strategies.v7_structure import V7StructureStrategy
                new_sl = decision.get('new_sl') if decision else None
                if new_sl is None:
                    logger.error(f"{pm.symbol} V7 Stage 2: decision 缺少 new_sl")
                    return

                if Config.V6_DRY_RUN:
                    balance = 10000.0
                else:
                    balance = self.risk_manager.get_balance()

                total_risk_pct = self._calc_total_risk_pct(balance)

                add_size = V7StructureStrategy.calculate_add_size(
                    balance=balance,
                    risk_per_trade=Config.RISK_PER_TRADE,
                    entry_price=entry_price,
                    new_sl=new_sl,
                    max_position_percent=Config.MAX_POSITION_PERCENT,
                    max_total_risk=Config.MAX_TOTAL_RISK,
                    current_total_risk_pct=total_risk_pct,
                )
            else:
                add_size = pm.calculate_stage2_size(entry_price)

            if add_size <= 0:
                logger.warning(f"{pm.symbol} 階段2 倉位=0，跳過")
                return

            # Precision
            add_size = self._validate_position_size(pm.symbol, add_size, entry_price, "階段2")
            if add_size is None:
                return

            if Config.V6_DRY_RUN:
                logger.info(
                    f"[模擬] {pm.symbol} 階段2 加倉: +{add_size:.6f} @ ${entry_price:.2f}"
                )
                pm.add_stage2(entry_price, add_size)
                # V7: override SL to structural swing point
                if pm.strategy_name == 'v7_structure' and decision and decision.get('new_sl'):
                    pm.current_sl = decision['new_sl']
                return

            # 下單
            order_side = self._get_close_side(pm.side)
            order_result = self._futures_create_order(pm.symbol, order_side, add_size)

            # 捕捉實際成交均價
            fill_price = self._extract_fill_price(order_result, entry_price)
            if fill_price != entry_price:
                logger.info(
                    f"{pm.symbol} Stage2 成交均價修正: 信號${entry_price:.4f} → 實際${fill_price:.4f}"
                )

            # 更新 PM
            pm.add_stage2(fill_price, add_size)
            # V7: override SL to structural swing point
            if pm.strategy_name == 'v7_structure' and decision and decision.get('new_sl'):
                pm.current_sl = decision['new_sl']

            # 更新硬止損（Stage 2 移損至保本）
            self._refresh_stop_loss(pm, pm.current_sl)

            # 備份
            if Config.AUTO_BACKUP_ON_STAGE_CHANGE:
                self.persistence.backup_positions()

            logger.info(
                f"{pm.symbol} 階段2 加倉完成: +{add_size:.6f} @ ${fill_price:.2f} | "
                f"總倉位={pm.total_size:.6f} | 止損=${pm.current_sl:.2f}（保本）"
            )
            TelegramNotifier.notify_action(
                pm.symbol,
                'V7加倉' if pm.strategy_name == 'v7_structure' else '1.5R移損',
                fill_price,
                f"Stage2 加倉 +{add_size:.6f} 總={pm.total_size:.6f} SL=${pm.current_sl:.2f}"
            )

        except Exception as e:
            logger.error(f"{pm.symbol} 階段2 加倉失敗: {e}")

    def _handle_stage3(self, pm: PositionManager, current_price: float, df_1h, decision: dict = None):
        """處理 Stage 3 加倉"""
        try:
            entry_price = current_price

            # V7: 用策略的 calculate_add_size + decision 中的 new_sl
            if pm.strategy_name == 'v7_structure':
                from trader.strategies.v7_structure import V7StructureStrategy
                new_sl = decision.get('new_sl') if decision else None
                if new_sl is None:
                    logger.error(f"{pm.symbol} V7 Stage 3: decision 缺少 new_sl")
                    return

                if Config.V6_DRY_RUN:
                    balance = 10000.0
                else:
                    balance = self.risk_manager.get_balance()

                total_risk_pct = self._calc_total_risk_pct(balance)

                add_size = V7StructureStrategy.calculate_add_size(
                    balance=balance,
                    risk_per_trade=Config.RISK_PER_TRADE,
                    entry_price=entry_price,
                    new_sl=new_sl,
                    max_position_percent=Config.MAX_POSITION_PERCENT,
                    max_total_risk=Config.MAX_TOTAL_RISK,
                    current_total_risk_pct=total_risk_pct,
                )
                swing_stop = new_sl  # V7: new_sl 就是 swing-based SL
            else:
                from trader.structure import StructureAnalysis

                if df_1h is not None and not df_1h.empty:
                    if pm.side == 'LONG':
                        swing_price = StructureAnalysis.find_latest_confirmed_swing(
                            df_1h, 'low', Config.SWING_LEFT_BARS, Config.SWING_RIGHT_BARS
                        )
                    else:
                        swing_price = StructureAnalysis.find_latest_confirmed_swing(
                            df_1h, 'high', Config.SWING_LEFT_BARS, Config.SWING_RIGHT_BARS
                        )
                else:
                    swing_price = None

                if swing_price is None:
                    logger.warning(f"{pm.symbol} 階段3: 找不到 swing point 作止損，跳過")
                    return

                atr_buffer = pm.atr * Config.SL_ATR_BUFFER if pm.atr else 0
                if pm.side == 'LONG':
                    swing_stop = swing_price - atr_buffer
                else:
                    swing_stop = swing_price + atr_buffer

                add_size = pm.calculate_stage3_size(entry_price, swing_stop)
            if add_size <= 0:
                logger.warning(f"{pm.symbol} 階段3 倉位=0，跳過")
                return

            add_size = self._validate_position_size(pm.symbol, add_size, entry_price, "階段3")
            if add_size is None:
                return

            if Config.V6_DRY_RUN:
                logger.info(
                    f"[模擬] {pm.symbol} 階段3 加倉: +{add_size:.6f} @ ${entry_price:.2f} "
                    f"| swing 止損=${swing_stop:.2f}"
                )
                pm.add_stage3(entry_price, add_size, swing_stop)
                return

            # 下單
            order_side = self._get_close_side(pm.side)
            order_result = self._futures_create_order(pm.symbol, order_side, add_size)

            # 捕捉實際成交均價
            fill_price = self._extract_fill_price(order_result, entry_price)
            if fill_price != entry_price:
                logger.info(
                    f"{pm.symbol} Stage3 成交均價修正: 信號${entry_price:.4f} → 實際${fill_price:.4f}"
                )

            # 更新 PM
            pm.add_stage3(fill_price, add_size, swing_stop)

            # 更新硬止損
            self._refresh_stop_loss(pm, pm.current_sl)

            if Config.AUTO_BACKUP_ON_STAGE_CHANGE:
                self.persistence.backup_positions()

            logger.info(
                f"{pm.symbol} 階段3 加倉完成: +{add_size:.6f} @ ${fill_price:.2f} | "
                f"總倉位={pm.total_size:.6f} | 止損=${pm.current_sl:.2f}（swing 結構）"
            )

        except Exception as e:
            logger.error(f"{pm.symbol} 階段3 加倉失敗: {e}")

    def _handle_v53_reduce(self, pm: PositionManager, pct: int, label: str, current_price: float):
        """處理 V5.3 減倉"""
        try:
            reduce_size = pm.total_size * (pct / 100.0)
            if reduce_size <= 0:
                return

            reduce_size = float(self.precision_handler.format_quantity(pm.symbol, reduce_size))

            if Config.V6_DRY_RUN:
                partial_pnl = self._calculate_pnl(pm.side, reduce_size, current_price, pm.avg_entry)
                pm.realized_partial_pnl += partial_pnl
                logger.info(
                    f"[模擬] {pm.symbol} {label} 減倉: -{reduce_size:.6f} "
                    f"@ ${current_price:.2f} PnL=${partial_pnl:+.2f}"
                )
                TelegramNotifier.notify_action(
                    pm.symbol, '目標減倉',
                    current_price,
                    f"{label} -{reduce_size:.6f} PnL=${partial_pnl:+.2f}"
                )
                pm.total_size -= reduce_size
                _trade_log({
                    **self._build_log_base('PARTIAL_CLOSE', pm.trade_id, pm.symbol, pm.side),
                    'label': label,
                    'reduce_size': f'{reduce_size:.6f}',
                    'reduce_price': f'{current_price:.2f}',
                    'partial_pnl': f'{partial_pnl:+.2f}',
                    'cumulative_partial_pnl': f'{pm.realized_partial_pnl:+.2f}',
                    'remaining_size': f'{pm.total_size:.6f}',
                })
                return

            order_result = self._futures_close_position(pm.symbol, pm.side, reduce_size)

            # 捕捉實際成交均價
            fill_price = self._extract_fill_price(order_result, current_price)
            if fill_price != current_price:
                logger.info(
                    f"{pm.symbol} {label} 減倉成交均價修正: "
                    f"ticker${current_price:.4f} → 實際${fill_price:.4f}"
                )

            # 計算並累積減倉 PnL
            partial_pnl = self._calculate_pnl(pm.side, reduce_size, fill_price, pm.avg_entry)
            pm.realized_partial_pnl += partial_pnl

            pm.total_size -= reduce_size

            # 更新硬止損（倉位變小了）
            self._refresh_stop_loss(pm, pm.current_sl)

            logger.info(
                f"{pm.symbol} {label} 減倉: -{reduce_size:.6f} @ ${fill_price:.2f} | "
                f"PnL=${partial_pnl:+.2f} 累積=${pm.realized_partial_pnl:+.2f} | "
                f"剩餘={pm.total_size:.6f} | 止損=${pm.current_sl:.2f}"
            )
            TelegramNotifier.notify_action(
                pm.symbol, '目標減倉',
                fill_price,
                f"{label} -{reduce_size:.6f} PnL=${partial_pnl:+.2f} 剩餘={pm.total_size:.6f}"
            )

            _trade_log({
                **self._build_log_base('PARTIAL_CLOSE', pm.trade_id, pm.symbol, pm.side),
                'label': label,
                'reduce_size': f'{reduce_size:.6f}',
                'reduce_price': f'{fill_price:.2f}',
                'partial_pnl': f'{partial_pnl:+.2f}',
                'cumulative_partial_pnl': f'{pm.realized_partial_pnl:+.2f}',
                'remaining_size': f'{pm.total_size:.6f}',
            })

        except Exception as e:
            logger.error(f"{pm.symbol} 減倉失敗: {e}")

    # ==================== 啟動診斷 ====================

    def startup_diagnostics(self) -> bool:
        """啟動診斷"""
        logger.info("執行啟動診斷...")

        try:
            if Config.V6_DRY_RUN:
                balance = 10000.0
                logger.info(f"[模擬] 餘額: ${balance:.2f} USDT")
            else:
                balance = self.risk_manager.get_balance()
                logger.info(f"API 正常 | 餘額: ${balance:.2f} USDT")
            self.initial_balance = balance
        except Exception as e:
            logger.error(f"API 連線失敗: {e}")
            return False

        test_symbol = Config.SYMBOLS[0] if Config.SYMBOLS else 'BTC/USDT'
        df = self.fetch_ohlcv(test_symbol, Config.TIMEFRAME_SIGNAL, limit=50)
        if df.empty:
            logger.error(f"數據獲取失敗: {test_symbol}")
            return False
        logger.info(f"數據正常 | {test_symbol}: {len(df)} 根K線")

        # V6.0: 4H 數據測試
        df_4h = self.fetch_ohlcv(test_symbol, '4h', limit=20)
        if df_4h.empty:
            logger.warning("4H 數據獲取失敗（非關鍵）")
        else:
            logger.info(f"4H 數據正常 | {len(df_4h)} 根K線")

        # V6.0: Config 驗證
        try:
            Config.validate()
            logger.info("Config 驗證通過")
        except ValueError as e:
            logger.error(f"Config 驗證失敗: {e}")
            return False

        logger.info("啟動診斷通過")
        return True

    # ==================== 主循環 ====================

    def run(self):
        """主運行循環"""
        if not self.startup_diagnostics():
            logger.error("啟動診斷失敗，停止運行")
            return

        logger.info("機器人開始運行...\n")

        # 接管交易所有但 positions.json 未記錄的倉位（幽靈倉位恢復）
        self._adopt_ghost_positions()

        cycle = 0
        while True:
            try:
                cycle += 1
                logger.debug(f"[循環 #{cycle}]")

                self.scan_for_signals()
                self._sync_exchange_positions()  # 每 cycle 都執行，active_trades 為空時也偵測幽靈倉位
                self.monitor_positions()
                self.telegram_handler.poll()

                logger.debug(f"休息 {Config.CHECK_INTERVAL} 秒...\n")
                time.sleep(Config.CHECK_INTERVAL)

            except KeyboardInterrupt:
                logger.info("使用者中斷，停止運行")
                self._save_positions()
                break
            except Exception as e:
                logger.error(f"循環 #{cycle} 錯誤: {e}")
                time.sleep(Config.CHECK_INTERVAL)


# ==================== 入口 ====================
if __name__ == "__main__":
    import argparse

    # SIGTERM → KeyboardInterrupt（systemd stop 時 graceful flush positions）
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    parser = argparse.ArgumentParser(description='Trading Bot V6.0')
    parser.add_argument('--dry-run', action='store_true', help='Dry run mode')
    parser.add_argument('--debug', action='store_true', help='Debug mode')
    args = parser.parse_args()

    # Runtime 目錄（.log/ 子目錄）
    project_root = Path(__file__).resolve().parent.parent
    log_dir = project_root / '.log'
    log_dir.mkdir(exist_ok=True)

    # 設定 logging
    log_file = str(log_dir / 'v6_bot.log')
    log_level = logging.DEBUG if args.debug else logging.INFO

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s [%(levelname)s] %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.handlers.RotatingFileHandler(
                log_file, maxBytes=5*1024*1024, backupCount=3, encoding='utf-8'
            ),
        ],
    )

    # [TRADE] 日誌分流到 .log/v6_trades.log
    class _TradeFilter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return isinstance(msg, str) and '[TRADE]' in msg

    _trade_handler = logging.handlers.RotatingFileHandler(
        str(log_dir / 'v6_trades.log'), maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
    )
    _trade_handler.setFormatter(logging.Formatter('%(message)s'))
    _trade_handler.addFilter(_TradeFilter())
    _trade_handler.setLevel(logging.INFO)
    logging.getLogger().addHandler(_trade_handler)

    # WARNING/ERROR 轉發到 Telegram（節流：同訊息 5 分鐘內不重複發送）
    class _TelegramLogHandler(logging.Handler):
        # 不轉發到 Telegram 的訊息（含以下字串即略過）
        _IGNORE_PATTERNS = [
            "Scanner JSON 中 hot_symbols 為空",
        ]

        def __init__(self):
            super().__init__(level=logging.WARNING)
            self._last_sent = {}  # message_key -> timestamp

        def emit(self, record):
            try:
                msg = self.format(record)
                if any(p in msg for p in self._IGNORE_PATTERNS):
                    return
                # 節流：取前 80 字元作 key，5 分鐘內同 key 不重複
                key = msg[:80]
                now = time.time()
                if now - self._last_sent.get(key, 0) < 300:
                    return
                self._last_sent[key] = now
                # 清理過期 key（避免記憶體洩漏）
                if len(self._last_sent) > 100:
                    cutoff = now - 300
                    self._last_sent = {k: v for k, v in self._last_sent.items() if v > cutoff}
                TelegramNotifier.notify_warning(msg)
            except Exception:
                pass  # 通知失敗不影響主程式

    _tg_handler = _TelegramLogHandler()
    _tg_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
    logging.getLogger().addHandler(_tg_handler)

    try:
        # bot_config.json 相對於專案根目錄（bot.py 的上層），不依賴 CWD
        config_path = str(Path(__file__).parent.parent / "bot_config.json")
        Config.load_from_json(config_path)
        if args.dry_run:
            Config.V6_DRY_RUN = True  # type: ignore[assignment]

        bot = TradingBotV6()
        bot.run()
    except Exception as e:
        logger.error(f"機器人啟動失敗: {e}")
        raise
