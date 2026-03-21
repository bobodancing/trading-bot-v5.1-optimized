"""
V6.0 Config — 獨立配置類

合併 V5.3 + V6.0 所有參數，不再依賴 trading_bot_main.py。
"""

import os
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class ConfigV6:
    """
    V6.0 配置類（獨立版）

    包含所有 V5.3 基礎參數 + V6.0 滾倉專屬設定。
    """

    # ==================== V5.3 基礎參數 ====================

    # 基本設置
    EXCHANGE = 'binance'
    API_KEY = 'your_api_key_here'
    API_SECRET = 'your_api_secret_here'
    SANDBOX_MODE = True

    # 交易模式
    TRADING_MODE = 'future'
    TRADING_DIRECTION = 'both'
    LEVERAGE = 3
    USE_HARD_STOP_LOSS = False

    # Telegram
    TELEGRAM_ENABLED = True
    TELEGRAM_BOT_TOKEN = '8280737402:AAFRARTvKmLZbIGK0iZ1Cscu-yZR7Mt-PJA'
    TELEGRAM_CHAT_ID = '@trading_bobo_bot'

    # 交易標的
    SYMBOLS = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT']

    # 風險管理
    RISK_PER_TRADE = 0.017
    MAX_TOTAL_RISK = 0.05
    MAX_POSITIONS_PER_GROUP = 6
    MAX_POSITION_PERCENT = 0.146

    # 技術指標
    LOOKBACK_PERIOD = 20
    VOLUME_MA_PERIOD = 20
    ATR_PERIOD = 13
    ATR_MULTIPLIER = 1.5

    # 時間框架
    TIMEFRAME_TREND = '1d'
    TIMEFRAME_SIGNAL = '1h'
    EMA_TREND = 200

    # 多時間框架確認
    ENABLE_MTF_CONFIRMATION = True
    TIMEFRAME_MTF = '4h'
    MTF_EMA_FAST = 20
    MTF_EMA_SLOW = 50

    # 動態閾值系統
    ENABLE_DYNAMIC_THRESHOLDS = True
    ADX_BASE_THRESHOLD = 18
    ADX_STRONG_THRESHOLD = 25
    ATR_QUIET_MULTIPLIER = 1.2
    ATR_NORMAL_MULTIPLIER = 1.5
    ATR_VOLATILE_MULTIPLIER = 2.0

    # 分級入場系統
    ENABLE_TIERED_ENTRY = True
    TIER_A_POSITION_MULT = 1.0
    TIER_B_POSITION_MULT = 0.7
    TIER_C_POSITION_MULT = 0.5

    # EMA 回撤進場信號
    ENABLE_EMA_PULLBACK = True
    EMA_PULLBACK_FAST = 10
    EMA_PULLBACK_SLOW = 20
    EMA_PULLBACK_THRESHOLD = 0.02  # 回撤觸及 EMA 的容差（佔 EMA 價格比例）

    # 量能突破進場信號
    ENABLE_VOLUME_BREAKOUT = True
    VOLUME_BREAKOUT_MULT = 2.0  # 量比門檻（量 ÷ 均量 >= 此值才觸發）

    # 市場過濾器
    ENABLE_MARKET_FILTER = True
    ADX_THRESHOLD = 22
    ATR_SPIKE_MULTIPLIER = 2.0
    EMA_ENTANGLEMENT_THRESHOLD = 0.03

    # 量能分級
    ENABLE_VOLUME_GRADING = True
    VOL_EXPLOSIVE_THRESHOLD = 2.5
    VOL_STRONG_THRESHOLD = 1.5
    VOL_MODERATE_THRESHOLD = 1.0
    VOL_MINIMUM_THRESHOLD = 0.7
    ACCEPT_WEAK_SIGNALS = False

    # V5.3 統一出場 SOP（給非滾倉策略用）
    FIRST_PARTIAL_PCT = 30
    SECOND_PARTIAL_PCT = 30
    APLUS_TRAILING_ATR_MULT = 1.5
    MAX_HOLD_HOURS = 168

    # 其他
    ENABLE_STRUCTURE_BREAK_EXIT = True
    CHECK_INTERVAL = 60
    MAX_RETRY = 3
    RETRY_DELAY = 5
    TREND_CACHE_HOURS = 4

    # ==================== V6.0 滾倉系統 ====================

    PYRAMID_ENABLED = True

    # 資金管理：V6 Equity Cap（三段加滿 = V5.3 的 2x）
    EQUITY_CAP_PERCENT = 0.20

    # V5.3 獨立 equity cap（防緊止損暴倉，與 V6 分開管理）
    V53_EQUITY_CAP_PERCENT = 0.10

    # 三段式分配比例（加總 = 100%）
    # Stage 1: 33% = 初始確認進場（V5.3 max 的 0.66x）
    # Stage 2: 37% = neckline 突破後加碼（最大，方向已確認）
    # Stage 3: 30% = EMA 回撤後收尾加倉
    STAGE1_RATIO = 0.33
    STAGE2_RATIO = 0.37
    STAGE3_RATIO = 0.30

    # Stage 觸發條件
    STAGE2_VOLUME_MULT = 1.2
    STAGE3_EMA_PERIOD = 20
    STAGE3_VOLUME_REDUCED = True

    # ===== 診斷設定 =====
    V6_STAGE2_DEBUG_LOG = True   # Stage 2 trigger 診斷 log（True → INFO level 輸出）

    # ===== 從 hardcode 提取的參數（2026-03-21 cleanup）=====

    # 2B 信號：最大穿透深度（ATR 倍數），超過視為趨勢反轉非 fakeout
    MAX_FAKEOUT_ATR = 3.0

    # 信號止損 ATR buffer（signals.py 用，與 SL_ATR_BUFFER 分開管理）
    SL_ATR_BUFFER_SIGNAL = 0.5

    # EMA 回撤信號：最低量比（vol / vol_ma）
    VOLUME_PULLBACK_MIN_RATIO = 0.6

    # 結構破壞容差（0.5%）：價格必須穿越 swing point 此比例才算 break
    STRUCTURE_BREAK_TOLERANCE = 0.005

    # 結構破壞 lookback（根數）
    STRUCTURE_BREAK_LOOKBACK = 10

    # ATR ratio 閾值：recent_atr / historical_atr
    ATR_QUIET_RATIO = 0.8       # < 此值 → quiet market
    ATR_VOLATILE_RATIO = 1.5    # > 此值 → volatile market

    # Stage 3 EMA 觸碰容差（佔 EMA 價格比例）
    STAGE3_EMA_TOUCH_TOLERANCE = 0.02

    # Swing Point 參數
    SWING_LEFT_BARS = 7
    SWING_RIGHT_BARS = 3

    # 止損設定
    SL_ATR_BUFFER = 0.8

    # 2B 信號最小穿透深度（ATR 倍數）
    # 穿透不足視為噪音，非真正流動性獵殺
    MIN_FAKEOUT_ATR = 0.3

    # Reverse 2B 出場最小穿透深度（ATR 倍數）
    # 出場 reverse 2B 穿透不足視為噪音 wick，不觸發平倉
    # 不設上限（穿越深再收回 = 大型 trap，越危險）
    REVERSE_2B_MIN_FAKEOUT_ATR = 0.3

    # V6.0 出場設定
    V6_STRUCTURE_TRAILING = True
    V6_4H_EMA20_FORCE_EXIT = False
    V6_REVERSE_2B_EXIT = True

    # === 三段式動態防守 (Three-Tier Defense) ===
    # Tier 1: 保本移損（Breakeven Bridge）
    V6_BREAKEVEN_ENABLED = True
    V6_BREAKEVEN_MFE_R = 1.5       # MFE 達 1.5R 觸發保本
    V6_BREAKEVEN_BUFFER_R = 0.1    # SL 移到 entry + 0.1R（覆蓋手續費）

    # Tier 2: 加速結構追蹤（Stage 1 用）
    V6_FAST_TRAIL_RIGHT_BARS = 2   # Stage 1 加速確認（標準是 SWING_RIGHT_BARS=3）
    V6_FAST_TRAIL_REQUIRE_BOS = False  # Stage 1 不要求 BOS，只要 HL/LH

    # === 舊 pullback 參數（V53 fallback 用，V6 已改為三段式防守）===
    PROFIT_PULLBACK_THRESHOLD = 0.55
    MIN_MFE_R_FOR_PULLBACK = 0.3

    # 快速止損（虧損達 0.67R 即平倉）
    EARLY_STOP_R_THRESHOLD = 0.75

    # 時間退出（未升級到下一階段的最大持倉時間）
    STAGE1_MAX_HOURS = 24        # V5.3 路徑
    V6_STAGE1_MAX_HOURS = 36    # V6 滾倉路徑

    # 快速止損/時間退出後的冷卻時間
    EARLY_EXIT_COOLDOWN_HOURS = 10

    # === Risk Guard V1 ===

    # BTC 趨勢過濾：逆 BTC 趨勢時降低倉位乘數（0.0 = 完全禁止，0.5 = 半倉）
    # 判定方式：BTC/USDT 1D EMA20 vs EMA50
    BTC_TREND_FILTER_ENABLED = True
    BTC_COUNTER_TREND_MULT = 0.0  # 0.0 = 禁止逆勢進場

    # SL 距離上限（佔 entry price 的百分比）
    # 超過此距離的交易直接跳過（防小幣結構寬導致單筆巨虧）
    MAX_SL_DISTANCE_PCT = 0.06  # 6%

    # 同幣虧損冷卻（小時）
    # 某 symbol 最近一筆虧損後，需等待此時間才能再進場
    # 持久化（基於 perf_db 查詢，restart 不遺失）
    SYMBOL_LOSS_COOLDOWN_HOURS = 24

    # 策略選擇器
    STRATEGY_USE_V6 = {
        '2B_BREAKOUT': True,
        # EMA_PULLBACK / VOLUME_BREAKOUT 固定走 V5.3 路徑，不在此控制
    }

    # Signal → Strategy 映射（新增策略只需在此加一行 + register class）
    SIGNAL_STRATEGY_MAP: dict = {
        "2B": "v6_pyramid",
        "EMA_PULLBACK": "v53_sop",
        "VOLUME_BREAKOUT": "v53_sop",
    }

    # Debug & 日誌
    V6_DEBUG_MODE = False
    V6_DRY_RUN = False

    # --- Strategy ---
    STRATEGY = "v6_pyramid"

    @classmethod
    def get_strategy(cls) -> 'TradingStrategy':
        from trader.strategies import StrategyFactory
        return StrategyFactory.create_strategy(cls.STRATEGY)

    # ==================== Persistence ====================

    _PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
    _LOG_DIR = str(Path(__file__).resolve().parent.parent / '.log')

    POSITIONS_JSON_PATH = str(Path(__file__).resolve().parent.parent / '.log' / 'positions.json')
    LOG_FILE_PATH = str(Path(__file__).resolve().parent.parent / '.log' / 'v6_bot.log')
    AUTO_BACKUP_ON_STAGE_CHANGE = True
    DB_PATH = "v6_performance.db"

    # ==================== Scanner 整合 ====================

    USE_SCANNER_SYMBOLS = True
    SCANNER_JSON_PATH = 'hot_symbols.json'
    SCANNER_MAX_AGE_MINUTES = 60

    # ==================== Config Validation ====================

    @classmethod
    def validate(cls):
        """驗證 V6.0 config 參數合理性"""
        total_ratio = cls.STAGE1_RATIO + cls.STAGE2_RATIO + cls.STAGE3_RATIO
        if abs(total_ratio - 1.0) > 0.001:
            raise ValueError(
                f"Stage ratios must sum to 1.0, got {total_ratio:.3f} "
                f"(S1={cls.STAGE1_RATIO}, S2={cls.STAGE2_RATIO}, S3={cls.STAGE3_RATIO})"
            )

        if not (0.01 <= cls.EQUITY_CAP_PERCENT <= 0.5):
            raise ValueError(
                f"EQUITY_CAP_PERCENT should be between 1% and 50%, got {cls.EQUITY_CAP_PERCENT*100}%"
            )

        if not (0.01 <= cls.V53_EQUITY_CAP_PERCENT <= cls.EQUITY_CAP_PERCENT):
            raise ValueError(
                f"V53_EQUITY_CAP_PERCENT should be between 1% and EQUITY_CAP_PERCENT "
                f"({cls.EQUITY_CAP_PERCENT*100}%), got {cls.V53_EQUITY_CAP_PERCENT*100}%"
            )

        if cls.SWING_RIGHT_BARS < 2:
            raise ValueError(
                f"SWING_RIGHT_BARS must be at least 2 for proper confirmation, got {cls.SWING_RIGHT_BARS}"
            )

        if cls.STAGE2_VOLUME_MULT < 1.0:
            raise ValueError(
                f"STAGE2_VOLUME_MULT should be >= 1.0 (放量), got {cls.STAGE2_VOLUME_MULT}"
            )

        return True

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
            unknown_keys = []
            for json_key, value in config_data.items():
                attr_name = json_key.upper()
                if hasattr(cls, attr_name):
                    current = getattr(cls, attr_name)
                    # dict 類型用 merge（保留未覆寫的 key）
                    if isinstance(current, dict) and isinstance(value, dict):
                        current.update(value)
                        value = current
                    setattr(cls, attr_name, value)
                    loaded_count += 1
                else:
                    unknown_keys.append(json_key)

            logger.info(f"✅ 已從 {config_file} 加載 {loaded_count} 項配置")
            if unknown_keys:
                logger.debug(f"⚠️ 以下 JSON key 無對應的 Config 屬性（已忽略）: {unknown_keys}")

        except Exception as e:
            logger.error(f"❌ 加載配置文件失敗: {e}")
            logger.info("⚠️ 將使用默認配置")
            return

        # --- 載入 secrets.json ---
        config_dir = os.path.dirname(os.path.abspath(config_file))
        secrets_path = os.path.join(config_dir, "secrets.json")
        if os.path.exists(secrets_path):
            try:
                with open(secrets_path, 'r', encoding='utf-8') as f:
                    secrets_data = json.load(f)
                for key, value in secrets_data.items():
                    attr_name = key.upper()
                    setattr(cls, attr_name, value)
                logger.info(f"✅ 已從 {secrets_path} 加載 {len(secrets_data)} 項 secrets")
            except Exception as e:
                logger.error(f"❌ 加載 secrets 失敗: {e}")
        else:
            logger.warning(f"⚠️ Secrets 文件不存在: {secrets_path}（將使用 class defaults）")

        # 載入後自動驗證
        try:
            cls.validate()
        except ValueError as e:
            logger.error(f"❌ Config validation failed: {e}")
            raise


# Alias for convenience
Config = ConfigV6
