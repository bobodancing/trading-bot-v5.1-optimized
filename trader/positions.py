"""
V6.0 PositionManager — 取代 V5.3 TradeManager

管理單一 symbol 的完整生命週期：
- V6.0 滾倉路徑：Stage 1 → Stage 2 → Stage 3 → 結構追蹤出場
- V5.3 相容路徑：1.0R → 1.5R → 2.5R → ATR trailing（for EMA Pullback / Volume Breakout）
"""

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from trader.strategies import TradingStrategy


@dataclass
class EntryRecord:
    """單次入場紀錄"""
    price: float
    size: float
    stage: int  # 1, 2, 3
    time: str   # ISO8601


class PositionManager:
    """
    單一 symbol 的倉位管理器

    兩種模式：
    1. V6.0 滾倉 (is_v6_pyramid=True): 三段式金字塔加倉 + 結構追蹤出場
    2. V5.3 相容 (is_v6_pyramid=False): 原有 1.0R/1.5R/2.5R 分批減倉
    """

    def __init__(
        self,
        symbol: str,
        side: str,
        entry_price: float,
        stop_loss: float,
        position_size: float,
        strategy_name: str = None,
        is_v6_pyramid: bool = None,  # legacy — resolved to strategy_name
        neckline: Optional[float] = None,
        equity_base: float = 0.0,
        initial_r: float = 0.0,
        signal_tier: str = 'B',
        trade_id: Optional[str] = None,
        market_regime: str = 'UNKNOWN',
        strategy: Optional['TradingStrategy'] = None,
    ):
        # === 核心身份 ===
        self.symbol = symbol
        self.side = side  # 'LONG' or 'SHORT'
        self.trade_id = trade_id or datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + symbol.replace('/', '')

        # 解析 strategy_name（支援舊 is_v6_pyramid 參數）
        if strategy_name is not None:
            self.strategy_name = strategy_name
        elif is_v6_pyramid is not None:
            self.strategy_name = "v6_pyramid" if is_v6_pyramid else "v53_sop"
        else:
            self.strategy_name = "v6_pyramid"

        # === Stage 管理（V6.0）===
        self.stage = 1
        self.entries: List[EntryRecord] = [
            EntryRecord(
                price=entry_price,
                size=position_size,
                stage=1,
                time=datetime.now(timezone.utc).isoformat()
            )
        ]

        # === 倉位數據 ===
        self.total_size = position_size
        self.avg_entry = entry_price
        self.current_sl = stop_loss
        self.initial_sl = stop_loss
        self.initial_r = initial_r  # 金額：balance * risk_per_trade

        # === V6.0 滾倉數據 ===
        self.neckline = neckline
        self.equity_base = equity_base  # Stage 1 時的 equity snapshot

        # === 交易所狀態 ===
        self.stop_order_id: Optional[str] = None
        self.is_closed = False

        # === 時間追蹤 ===
        self.entry_time = datetime.now(timezone.utc)
        self.monitor_count = 0

        # === 價格追蹤（用於結構追蹤止損）===
        self.highest_price = entry_price
        self.lowest_price = entry_price
        self.atr: Optional[float] = None

        self.signal_tier = signal_tier
        self.market_regime = market_regime

        # === Phase 1 分析欄位（需持久化）===
        self.entry_adx: Optional[float] = None
        self.fakeout_depth_atr: Optional[float] = None
        self.btc_trend_aligned: Optional[bool] = None
        self.reverse_2b_depth_atr: Optional[float] = None
        self.trend_adx: Optional[float] = None
        self.mtf_aligned: Optional[bool] = None
        self.volume_grade: Optional[str] = None
        self.tier_score: Optional[int] = None

        # === 退出原因標記（runtime 暫態，不持久化）===
        self.exit_reason: Optional[str] = None  # Public for Strategy

        # === 待取消止損單（背景重試佇列）===
        self.pending_stop_cancels: List[str] = []

        # === V5.3 減倉 PnL 累積（partial close tracking）===
        self.original_size = position_size       # 入場時的完整倉位
        self.realized_partial_pnl = 0.0          # 累積已實現的減倉 PnL（USDT）

        # === 計算初始 risk distance ===
        self.risk_dist = abs(entry_price - stop_loss)

        # === 策略（Strategy Pattern V7 P2）===
        from trader.strategies import StrategyFactory
        self.strategy = strategy or StrategyFactory.create(self.strategy_name)

    # ==================== 屬性（Properties）====================

    @property
    def is_v6_pyramid(self) -> bool:
        """是否為 V6 金字塔策略（向下相容）"""
        return self.strategy_name == "v6_pyramid"

    @is_v6_pyramid.setter
    def is_v6_pyramid(self, value: bool):
        """設定策略（向下相容 legacy setter）"""
        self.strategy_name = "v6_pyramid" if value else "v53_sop"

    # --- V53 state proxy properties（委派至 strategy，向下相容）---

    @property
    def is_1r_protected(self) -> bool:
        return getattr(self.strategy, 'is_1r_protected', False)

    @is_1r_protected.setter
    def is_1r_protected(self, value: bool):
        if hasattr(self.strategy, 'is_1r_protected'):
            self.strategy.is_1r_protected = value

    @property
    def is_first_partial(self) -> bool:
        return getattr(self.strategy, 'is_first_partial', False)

    @is_first_partial.setter
    def is_first_partial(self, value: bool):
        if hasattr(self.strategy, 'is_first_partial'):
            self.strategy.is_first_partial = value

    @property
    def is_second_partial(self) -> bool:
        return getattr(self.strategy, 'is_second_partial', False)

    @is_second_partial.setter
    def is_second_partial(self, value: bool):
        if hasattr(self.strategy, 'is_second_partial'):
            self.strategy.is_second_partial = value

    @property
    def is_trailing_active(self) -> bool:
        return getattr(self.strategy, 'is_trailing_active', False)

    @is_trailing_active.setter
    def is_trailing_active(self, value: bool):
        if hasattr(self.strategy, 'is_trailing_active'):
            self.strategy.is_trailing_active = value

    # ==================== V6.0 滾倉方法 ====================

    def add_stage2(self, price: float, size: float) -> bool:
        """
        Stage 2 加倉：neckline 突破確認

        - 加倉 + 移損至 Stage 1 入場價（保本位）
        - 更新 avg_entry 和 total_size

        Args:
            price: Stage 2 入場價（neckline 突破價附近）
            size: 加倉數量（已通過 risk 驗證）

        Returns:
            bool: 成功
        """
        if self.stage != 1:
            logger.warning(f"{self.symbol} Stage 2 skip: current stage is {self.stage}")
            return False

        # 記錄 entry
        self.entries.append(EntryRecord(
            price=price, size=size, stage=2,
            time=datetime.now(timezone.utc).isoformat()
        ))

        # 更新倉位
        old_total = self.total_size
        self.total_size += size
        self.avg_entry = (old_total * self.avg_entry + size * price) / self.total_size

        # 移損至 Stage 1 入場價（保本位）
        stage1_entry = self.entries[0].price
        if self.side == 'LONG':
            new_sl = stage1_entry  # 保本
        else:
            new_sl = stage1_entry  # 保本

        self.current_sl = new_sl
        self.stage = 2

        logger.info(
            f"[V6] {self.symbol} Stage 2 added: "
            f"+{size:.6f} @ ${price:.2f} | "
            f"Total: {self.total_size:.6f} | Avg: ${self.avg_entry:.2f} | "
            f"SL -> ${new_sl:.2f} (breakeven)"
        )
        return True

    def add_stage3(self, price: float, size: float, swing_stop: float) -> bool:
        """
        Stage 3 加倉：EMA pullback + 縮量 + 反轉 K 確認

        - 加倉 + 移損至最近 confirmed swing point +/- 0.5 ATR
        - 更新 avg_entry 和 total_size

        Args:
            price: Stage 3 入場價
            size: 加倉數量（已通過 risk 驗證）
            swing_stop: 最近 confirmed swing point +/- 0.5 ATR 的止損價

        Returns:
            bool: 成功
        """
        if self.stage != 2:
            logger.warning(f"{self.symbol} Stage 3 skip: current stage is {self.stage}")
            return False

        # 記錄 entry
        self.entries.append(EntryRecord(
            price=price, size=size, stage=3,
            time=datetime.now(timezone.utc).isoformat()
        ))

        # 更新倉位
        old_total = self.total_size
        self.total_size += size
        self.avg_entry = (old_total * self.avg_entry + size * price) / self.total_size

        # 移損至 swing point stop
        self.current_sl = swing_stop
        self.stage = 3

        logger.info(
            f"[V6] {self.symbol} Stage 3 added: "
            f"+{size:.6f} @ ${price:.2f} | "
            f"Total: {self.total_size:.6f} | Avg: ${self.avg_entry:.2f} | "
            f"SL -> ${swing_stop:.2f} (swing structure)"
        )
        return True

    # ==================== Stage Trigger 檢查 ====================

    def check_stage2_trigger(self, df_1h) -> bool:
        """
        檢查 Stage 2 觸發條件：neckline 突破 + 1.2x 放量
        每個失敗條件都 log，供診斷用。

        條件：
        1. 目前在 Stage 1
        2. 倉位盈利中（current_price vs entry_price）
        3. 最新 K 線收盤突破 neckline
        4. 最新 K 線量 >= vol_ma * STAGE2_VOLUME_MULT

        Args:
            df_1h: 1H OHLCV DataFrame（含 indicators）

        Returns:
            bool: 是否觸發
        """
        from trader.config import ConfigV6 as Cfg

        prefix = f"[V6] {self.symbol} Stage2Check"
        log_fn = logger.info if Cfg.V6_STAGE2_DEBUG_LOG else logger.debug

        if self.stage != 1 or self.neckline is None:
            log_fn(
                f"{prefix}: SKIP stage={self.stage} "
                f"neckline={'None' if self.neckline is None else f'${self.neckline:.2f}'}"
            )
            return False
        if df_1h is None or df_1h.empty:
            log_fn(f"{prefix}: SKIP df_1h empty/None")
            return False

        current = df_1h.iloc[-1]
        close = current['close']
        volume = current.get('volume', 0)
        vol_ma = current.get('vol_ma', 0)
        vol_ratio = volume / vol_ma if vol_ma > 0 else 0

        # 條件 1: 倉位盈利
        if self.side == 'LONG' and close <= self.entries[0].price:
            log_fn(
                f"{prefix}: FAIL profit check "
                f"close=${close:.2f} <= entry=${self.entries[0].price:.2f}"
            )
            return False
        if self.side == 'SHORT' and close >= self.entries[0].price:
            log_fn(
                f"{prefix}: FAIL profit check "
                f"close=${close:.2f} >= entry=${self.entries[0].price:.2f}"
            )
            return False

        # 條件 2: 收盤突破 neckline
        if self.side == 'LONG' and close <= self.neckline:
            log_fn(
                f"{prefix}: FAIL neckline "
                f"close=${close:.2f} <= neckline=${self.neckline:.2f} "
                f"(gap=${self.neckline - close:.2f})"
            )
            return False
        if self.side == 'SHORT' and close >= self.neckline:
            log_fn(
                f"{prefix}: FAIL neckline "
                f"close=${close:.2f} >= neckline=${self.neckline:.2f} "
                f"(gap=${close - self.neckline:.2f})"
            )
            return False

        # 條件 3: 放量
        if vol_ma <= 0:
            log_fn(f"{prefix}: FAIL vol_ma=0")
            return False
        if vol_ratio < Cfg.STAGE2_VOLUME_MULT:
            log_fn(
                f"{prefix}: FAIL volume "
                f"vol_ratio={vol_ratio:.2f}x < {Cfg.STAGE2_VOLUME_MULT}x "
                f"(need +{(Cfg.STAGE2_VOLUME_MULT - vol_ratio) * 100:.0f}% more volume)"
            )
            return False

        logger.info(
            f"[V6] {self.symbol} Stage 2 TRIGGERED: "
            f"close=${close:.2f} broke neckline=${self.neckline:.2f} | "
            f"vol={vol_ratio:.2f}x"
        )
        return True

    def check_stage3_trigger(self, df_1h) -> bool:
        """
        檢查 Stage 3 觸發條件：EMA20 回測 + 縮量 + 反轉 K 線

        條件：
        1. 目前在 Stage 2
        2. 前一根 K 線 low/high 觸碰 EMA20 附近
        3. 前一根 K 線縮量（volume < vol_ma）
        4. 當前 K 線是反轉 K 線（收盤超越前根高/低點）

        Args:
            df_1h: 1H OHLCV DataFrame（含 indicators）

        Returns:
            bool: 是否觸發
        """
        if self.stage != 2:
            return False
        if df_1h is None or len(df_1h) < 3:
            return False

        from trader.config import ConfigV6 as Cfg

        current = df_1h.iloc[-1]
        prev = df_1h.iloc[-2]

        # 計算 EMA 20
        ema_col = f'ema_{Cfg.STAGE3_EMA_PERIOD}'
        if ema_col not in df_1h.columns:
            # 如果 DataFrame 沒有這個 EMA，嘗試 ema_slow
            if 'ema_slow' in df_1h.columns:
                ema_val = prev['ema_slow']
            else:
                return False
        else:
            ema_val = prev[ema_col]

        vol_ma = prev.get('vol_ma', 0)

        # 條件 1: 前一根 K 線觸碰 EMA20
        if self.side == 'LONG':
            # 做多：前一根 low 接近 EMA20（在 EMA20 上下 2% 以內）
            touch_threshold = ema_val * Cfg.STAGE3_EMA_TOUCH_TOLERANCE
            touched_ema = prev['low'] <= ema_val + touch_threshold
        else:
            # 做空：前一根 high 接近 EMA20
            touch_threshold = ema_val * Cfg.STAGE3_EMA_TOUCH_TOLERANCE
            touched_ema = prev['high'] >= ema_val - touch_threshold

        if not touched_ema:
            return False

        # 條件 2: 前一根縮量
        if vol_ma > 0 and prev['volume'] >= vol_ma:
            return False

        # 條件 3: 當前 K 線反轉（收盤超越前根極值）
        if self.side == 'LONG':
            reversal = current['close'] > prev['high']
        else:
            reversal = current['close'] < prev['low']

        if not reversal:
            return False

        logger.info(
            f"[V6] {self.symbol} Stage 3 TRIGGERED: "
            f"EMA pullback + reduced volume + reversal candle"
        )
        return True

    # ==================== Risk Progression 計算 ====================

    def calculate_stage2_size(self, entry_price: float) -> float:
        """
        計算 Stage 2 加倉數量（含 risk 驗證）

        公式：
        1. max_size = equity_base * EQUITY_CAP * STAGE2_RATIO / entry_price
        2. 驗證：加倉後 total_risk <= initial_R
        3. 超標則縮減

        Args:
            entry_price: Stage 2 預期入場價

        Returns:
            float: 加倉數量（0 = 不加倉）
        """
        from trader.config import ConfigV6 as Cfg

        if self.initial_r <= 0:
            logger.warning(f"[{self.symbol}] initial_r={self.initial_r}, skipping stage2 sizing")
            return 0.0

        # Stage 2 的止損會移到 Stage 1 entry（保本位）
        new_sl = self.entries[0].price

        # 計算 max_size by equity cap（tier_mult 貫穿三段）
        _tier_map = {
            'A': Cfg.TIER_A_POSITION_MULT,
            'B': Cfg.TIER_B_POSITION_MULT,
            'C': Cfg.TIER_C_POSITION_MULT,
        }
        tier_mult = _tier_map.get(self.signal_tier, Cfg.TIER_B_POSITION_MULT)
        max_value = self.equity_base * Cfg.EQUITY_CAP_PERCENT * Cfg.STAGE2_RATIO * tier_mult
        max_size = max_value / entry_price if entry_price > 0 else 0

        # 計算加倉後的 total risk
        new_total_size = self.total_size + max_size
        new_avg_entry = (self.total_size * self.avg_entry + max_size * entry_price) / new_total_size
        new_total_risk = abs(new_avg_entry - new_sl) * new_total_size

        # Risk check: total_risk <= initial_R
        if new_total_risk > self.initial_r and self.initial_r > 0:
            # 反推最大 size
            # total_risk = |avg_entry_new - new_sl| * total_size_new <= initial_R
            # 需要 iterative solve 或近似
            # 簡化：直接按比例縮減
            ratio = self.initial_r / new_total_risk if new_total_risk > 0 else 0
            adjusted_size = max_size * ratio
            logger.info(
                f"[V6] {self.symbol} Stage 2 risk cap: "
                f"max_size={max_size:.6f} -> adjusted={adjusted_size:.6f} "
                f"(risk ratio={ratio:.2f})"
            )
            return adjusted_size

        return max_size

    def calculate_stage3_size(self, entry_price: float, swing_stop: float) -> float:
        """
        計算 Stage 3 加倉數量（含 risk 驗證）

        目標：加倉後 total_risk <= 0（止損在 avg_entry 以上/以下）

        Args:
            entry_price: Stage 3 預期入場價
            swing_stop: 止損價（swing point +/- 0.5 ATR）

        Returns:
            float: 加倉數量（0 = 不加倉）
        """
        from trader.config import ConfigV6 as Cfg

        if self.initial_r <= 0:
            logger.warning(f"[{self.symbol}] initial_r={self.initial_r}, skipping stage3 sizing")
            return 0.0

        # 計算 max_size by equity cap（tier_mult 貫穿三段）
        _tier_map = {
            'A': Cfg.TIER_A_POSITION_MULT,
            'B': Cfg.TIER_B_POSITION_MULT,
            'C': Cfg.TIER_C_POSITION_MULT,
        }
        tier_mult = _tier_map.get(self.signal_tier, Cfg.TIER_B_POSITION_MULT)
        max_value = self.equity_base * Cfg.EQUITY_CAP_PERCENT * Cfg.STAGE3_RATIO * tier_mult
        max_size = max_value / entry_price if entry_price > 0 else 0

        # 計算加倉後的 total risk
        new_total_size = self.total_size + max_size
        new_avg_entry = (self.total_size * self.avg_entry + max_size * entry_price) / new_total_size
        new_total_risk = abs(new_avg_entry - swing_stop) * new_total_size

        # 檢查止損方向
        if self.side == 'LONG':
            risk_in_profit = swing_stop >= new_avg_entry  # 止損在保本以上
        else:
            risk_in_profit = swing_stop <= new_avg_entry

        if not risk_in_profit and new_total_risk > 0:
            # 仍有風險，按比例縮減 size（與 Stage 2 同邏輯）
            # 舊公式 1.0-(risk/initial_r) 在 risk>initial_r 時歸零，改為 proportional
            ratio = min(1.0, (self.initial_r + 0.001) / (new_total_risk + 0.001))
            adjusted_size = max_size * ratio
            logger.info(
                f"[V6] {self.symbol} Stage 3 risk cap: "
                f"max_size={max_size:.6f} -> adjusted={adjusted_size:.6f}"
            )
            return adjusted_size

        return max_size

    # ==================== Monitor（Strategy Pattern V7 P2）====================

    def monitor(self, current_price: float, df_1h=None, df_4h=None) -> Dict[str, Any]:
        """
        統一監控入口（V7 P2 起回傳 Dict）。

        委託 self.strategy.get_decision() 計算出場/加倉決策。

        Returns:
            dict: {
                "action"   : str,            # "ACTIVE"|"CLOSE"|"STAGE2_TRIGGER"|
                                             #   "STAGE3_TRIGGER"|"V53_REDUCE_15R"|"V53_REDUCE_25R"
                "reason"   : str,
                "new_sl"   : Optional[float],
                "close_pct": Optional[float],
            }
        """
        if self.is_closed:
            return {"action": "ACTIVE", "reason": "ALREADY_CLOSED", "new_sl": None, "close_pct": None}
        return self.strategy.get_decision(self, current_price, df_1h, df_4h)

    # ==================== 序列化（for positions.json）====================

    def to_dict(self) -> Dict[str, Any]:
        """序列化為 dict（for positions.json）"""
        return {
            'symbol': self.symbol,
            'side': self.side,
            'stage': self.stage,
            'entries': [asdict(e) for e in self.entries],
            'total_size': self.total_size,
            'avg_entry': self.avg_entry,
            'current_sl': self.current_sl,
            'initial_sl': self.initial_sl,
            'initial_r': self.initial_r,
            'neckline': self.neckline,
            'equity_base': self.equity_base,
            'stop_order_id': self.stop_order_id,
            'entry_time': self.entry_time.isoformat(),
            'highest_price': self.highest_price,
            'lowest_price': self.lowest_price,
            'is_v6_pyramid': self.is_v6_pyramid,   # backward compat
            'strategy_name': self.strategy_name,
            'strategy_type': 'v6' if self.is_v6_pyramid else 'v53',  # backward compat
            'strategy_state': self.strategy.get_state(),
            'signal_tier': self.signal_tier,
            'trade_id': self.trade_id,
            'market_regime': self.market_regime,
            'entry_adx':           getattr(self, 'entry_adx', None),
            'fakeout_depth_atr':   getattr(self, 'fakeout_depth_atr', None),
            'btc_trend_aligned':   getattr(self, 'btc_trend_aligned', None),
            'reverse_2b_depth_atr': getattr(self, 'reverse_2b_depth_atr', None),
            'trend_adx':       getattr(self, 'trend_adx', None),
            'mtf_aligned':     getattr(self, 'mtf_aligned', None),
            'volume_grade':    getattr(self, 'volume_grade', None),
            'tier_score':      getattr(self, 'tier_score', None),
            'pending_stop_cancels': self.pending_stop_cancels,
            'original_size': self.original_size,
            'realized_partial_pnl': self.realized_partial_pnl,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PositionManager':
        """從 dict 反序列化（from positions.json）"""
        # 向下相容：先讀 strategy_name，沒有則從 is_v6_pyramid/strategy_type 推導
        _sname = data.get('strategy_name')
        if _sname is None:
            _legacy_map = {'v6': 'v6_pyramid', 'v53': 'v53_sop'}
            _stype = data.get('strategy_type', 'v6' if data.get('is_v6_pyramid', True) else 'v53')
            _sname = _legacy_map.get(_stype, 'v6_pyramid')

        pm = cls(
            symbol=data['symbol'],
            side=data['side'],
            entry_price=data['avg_entry'],
            stop_loss=data['current_sl'],
            position_size=data['total_size'],
            strategy_name=_sname,
            neckline=data.get('neckline'),
            equity_base=data.get('equity_base', 0),
            initial_r=data.get('initial_r', 0),
            signal_tier=data.get('signal_tier', 'B'),
            market_regime=data.get('market_regime', 'UNKNOWN'),
        )

        # 恢復狀態
        pm.stage = data.get('stage', 1)
        pm.entries = [
            EntryRecord(**e) for e in data.get('entries', [])
        ]
        pm.total_size = data['total_size']
        pm.avg_entry = data['avg_entry']
        pm.current_sl = data['current_sl']
        pm.initial_sl = data.get('initial_sl', data['current_sl'])
        pm.initial_r = data.get('initial_r', 0)
        pm.stop_order_id = data.get('stop_order_id')
        pm.highest_price = data.get('highest_price', pm.avg_entry)
        pm.lowest_price = data.get('lowest_price', pm.avg_entry)
        pm.risk_dist = abs(pm.avg_entry - pm.initial_sl) if pm.initial_sl else 0

        # 恢復入場時間
        entry_time_str = data.get('entry_time')
        if entry_time_str:
            try:
                parsed = datetime.fromisoformat(entry_time_str)
                # 防禦性時區補丁：確保解析出的 datetime 永遠是 aware (UTC)
                pm.entry_time = parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pm.entry_time = datetime.now(timezone.utc)

        # 恢復策略內部 state（strategy_state 優先，向下相容舊 v53_state key）
        state = data.get('strategy_state') or data.get('v53_state') or {}
        pm.strategy.load_state(state)

        # 恢復 trade_id
        pm.trade_id = data.get('trade_id', datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S') + '_' + data['symbol'].replace('/', ''))

        # 恢復待取消止損單佇列（backward compat default = []）
        pm.pending_stop_cancels = data.get('pending_stop_cancels', [])

        # 恢復 V5.3 減倉 PnL 累積
        pm.original_size = data.get(
            'original_size',
            pm.entries[0].size if pm.entries else pm.total_size  # backward compat
        )
        pm.realized_partial_pnl = data.get('realized_partial_pnl', 0.0)

        # 恢復 Phase 1 分析欄位
        pm.entry_adx = data.get('entry_adx')
        pm.fakeout_depth_atr = data.get('fakeout_depth_atr')
        pm.btc_trend_aligned = data.get('btc_trend_aligned')
        pm.reverse_2b_depth_atr = data.get('reverse_2b_depth_atr')
        pm.trend_adx = data.get('trend_adx')
        pm.mtf_aligned = data.get('mtf_aligned')
        pm.volume_grade = data.get('volume_grade')
        pm.tier_score = data.get('tier_score')

        return pm
