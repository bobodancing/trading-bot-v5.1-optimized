# trader/strategies/v8_grid/grid.py
"""
V8 ATR 自適應網格引擎
虛擬網格策略：SMA ± k*ATR 定義區間，金字塔權重，固定網格線 + 偏移重置。
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from trader.config import Config
from trader.persistence import save_grid_state, load_grid_state

logger = logging.getLogger(__name__)


@dataclass
class GridAction:
    type: str       # 'OPEN' | 'CLOSE'
    side: str       # 'LONG' | 'SHORT'
    level: int      # grid level (positive = above center, negative = below)
    size: float     # BTC quantity
    price: float = 0.0  # execution price (filled by bot)


@dataclass
class GridState:
    center: float = 0.0
    upper: float = 0.0
    lower: float = 0.0
    grid_levels: int = 5
    grid_spacing: float = 0.0
    grid_balance: float = 0.0
    active_positions: list = field(default_factory=list)
    level_weights: dict = field(default_factory=dict)
    converging: bool = False
    converge_start_time: float = 0.0
    converge_market_ts: object = None  # pd.Timestamp for backtest timeout
    activated_at: float = 0.0
    last_cooldown_time: float = 0.0

    def to_dict(self) -> dict:
        return {
            'center': self.center, 'upper': self.upper, 'lower': self.lower,
            'grid_levels': self.grid_levels, 'grid_spacing': self.grid_spacing,
            'grid_balance': self.grid_balance,
            'active_positions': self.active_positions,
            'level_weights': {str(k): v for k, v in self.level_weights.items()},
            'converging': self.converging,
            'converge_start_time': self.converge_start_time,
            'activated_at': self.activated_at,
            'last_cooldown_time': self.last_cooldown_time,
        }

    @classmethod
    def from_dict(cls, d: dict) -> 'GridState':
        state = cls()
        for k, v in d.items():
            if k == 'level_weights':
                state.level_weights = {int(kk): vv for kk, vv in v.items()}
            elif hasattr(state, k):
                setattr(state, k, v)
        return state


class V8AtrGrid:
    """ATR 自適應虛擬網格引擎"""

    def __init__(self, api_client=None, notifier=None):
        self.state: Optional[GridState] = None
        self.api_client = api_client
        self.notifier = notifier

    def activate(self, df_4h: pd.DataFrame, grid_balance: float):
        """建構網格 — Regime 進入 RANGING 時呼叫"""
        close = df_4h['close']
        sma_period = getattr(Config, 'GRID_SMA_PERIOD', 20)
        atr_period = getattr(Config, 'GRID_ATR_PERIOD', 14)
        k = getattr(Config, 'GRID_ATR_MULTIPLIER', 2.5)
        levels = getattr(Config, 'GRID_LEVELS', 5)

        center = close.rolling(sma_period).mean().iloc[-1]
        # ATR calculation
        high, low, prev_close = df_4h['high'], df_4h['low'], close.shift(1)
        tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
        atr = tr.rolling(atr_period).mean().iloc[-1]

        upper = center + k * atr
        lower = center - k * atr
        spacing = (upper - lower) / (levels * 2)

        # Build pyramid weights
        w_center = getattr(Config, 'GRID_WEIGHT_CENTER', 0.5)
        w_edge = getattr(Config, 'GRID_WEIGHT_EDGE', 1.5)
        weights = {}
        for i in range(1, levels + 1):
            t = (i - 1) / max(1, levels - 1)  # 0 → 1
            weights[i] = w_center + t * (w_edge - w_center)

        self.state = GridState(
            center=center, upper=upper, lower=lower,
            grid_levels=levels, grid_spacing=spacing,
            grid_balance=grid_balance,
            level_weights=weights,
            activated_at=time.time(),
        )
        logger.info(
            f"Grid activated: center={center:.0f}, "
            f"range={lower:.0f}-{upper:.0f}, spacing={spacing:.0f}, "
            f"{levels*2} levels"
        )

    def tick(self, current_price: float, df_1h: pd.DataFrame, market_ts=None) -> List[GridAction]:
        """每 scan cycle 呼叫，回傳本 cycle 的動作列表。market_ts: 回測用時間戳。"""
        if self.state is None:
            return []

        actions = []

        # Check SMA drift → reset (only when price is near center, i.e. not at an active edge level)
        sma_period = getattr(Config, 'GRID_SMA_PERIOD', 20)
        current_sma = df_1h['close'].rolling(sma_period).mean().iloc[-1]
        drift = abs(current_sma - self.state.center)
        drift_ratio = getattr(Config, 'GRID_RESET_DRIFT_RATIO', 0.5)
        near_center = self.state.grid_spacing > 0 and abs(current_price - self.state.center) < self.state.grid_spacing * 1.5
        if not pd.isna(current_sma) and near_center and drift > drift_ratio * self.state.grid_spacing and self.state.grid_spacing > 0:
            logger.info(f"Grid reset: SMA drifted {drift:.0f} > {drift_ratio}*spacing({self.state.grid_spacing:.0f})")
            close_actions = self.force_close_all("sma_drift_reset")
            actions.extend(close_actions)
            # Rebuild using 1H df as proxy (bot will provide 4H df in production)
            self.activate(df_1h, self.state.grid_balance)
            return actions

        # Check drawdown
        max_dd = getattr(Config, 'GRID_MAX_DRAWDOWN', 0.05)
        unrealized = self._calc_unrealized_pnl(current_price)
        if self.state.grid_balance > 0 and unrealized / self.state.grid_balance < -max_dd:
            logger.warning(f"Grid drawdown limit: {unrealized/self.state.grid_balance:.1%}")
            actions.extend(self.force_close_all("drawdown_limit"))
            self.state.last_cooldown_time = time.time()
            return actions

        # Check converge timeout (wall clock for production, market_ts for backtest)
        if self.state.converging:
            timeout_h = getattr(Config, 'GRID_CONVERGE_TIMEOUT_HOURS', 72)
            timed_out = False
            if market_ts is not None and self.state.converge_market_ts is not None:
                elapsed = (market_ts - self.state.converge_market_ts).total_seconds()
                timed_out = elapsed > timeout_h * 3600
            elif self.state.converge_start_time > 0:
                timed_out = time.time() - self.state.converge_start_time > timeout_h * 3600
            if timed_out:
                logger.info(f"Grid converge timeout ({timeout_h}h) — force closing")
                actions.extend(self.force_close_all("converge_timeout"))
                return actions

        # Determine current level
        if self.state.grid_spacing <= 0:
            return actions
        level = round((current_price - self.state.center) / self.state.grid_spacing)

        # Check for positions to close (returned to center zone)
        for pos in list(self.state.active_positions):
            should_close = False
            if pos['side'] == 'SHORT' and current_price <= self.state.center + 0.5 * self.state.grid_spacing:
                should_close = True
            elif pos['side'] == 'LONG' and current_price >= self.state.center - 0.5 * self.state.grid_spacing:
                should_close = True
            if should_close:
                actions.append(GridAction(
                    type='CLOSE', side=pos['side'],
                    level=pos['level'], size=pos['size'],
                    price=current_price,
                ))
                self.state.active_positions.remove(pos)

        # Check for new positions to open (if not converging)
        if not self.state.converging and abs(level) >= 1:
            occupied_levels = {p['level'] for p in self.state.active_positions}
            if level > 0:  # Above center → SHORT
                for lv in range(1, min(level + 1, self.state.grid_levels + 1)):
                    if lv not in occupied_levels:
                        weight = self.state.level_weights.get(lv, 1.0)
                        size = self._calc_level_size(weight, current_price)
                        if self._check_notional_limit(size, current_price):
                            actions.append(GridAction(
                                type='OPEN', side='SHORT',
                                level=lv, size=size,
                                price=current_price,
                            ))
                            self.state.active_positions.append({
                                'level': lv, 'side': 'SHORT',
                                'entry': current_price, 'size': size,
                            })
            else:  # Below center → LONG
                for lv in range(-1, max(level - 1, -(self.state.grid_levels + 1)), -1):
                    abs_lv = abs(lv)
                    if lv not in occupied_levels:
                        weight = self.state.level_weights.get(abs_lv, 1.0)
                        size = self._calc_level_size(weight, current_price)
                        if self._check_notional_limit(size, current_price):
                            actions.append(GridAction(
                                type='OPEN', side='LONG',
                                level=lv, size=size,
                                price=current_price,
                            ))
                            self.state.active_positions.append({
                                'level': lv, 'side': 'LONG',
                                'entry': current_price, 'size': size,
                            })

        return actions

    def converge(self, market_ts=None):
        """進入收斂模式 — 不開新格，等現有格位平倉"""
        if self.state and not self.state.converging:
            self.state.converging = True
            self.state.converge_start_time = time.time()
            self.state.converge_market_ts = market_ts  # 回測用 market timestamp
            logger.info("Grid entering converge mode")

    def force_close_all(self, reason: str) -> List[GridAction]:
        """強制平倉所有格位"""
        if not self.state:
            return []
        actions = []
        for pos in self.state.active_positions:
            actions.append(GridAction(
                type='CLOSE', side=pos['side'],
                level=pos['level'], size=pos['size'],
            ))
        self.state.active_positions.clear()
        logger.info(f"Grid force close all: {reason} ({len(actions)} positions)")
        return actions

    def deactivate(self):
        """完全停用網格"""
        self.state = None

    def save_state(self):
        if self.state:
            save_grid_state(self.state.to_dict())

    def load_state(self):
        data = load_grid_state()
        if data:
            self.state = GridState.from_dict(data)

    def _calc_level_size(self, weight: float, price: float = 0.0) -> float:
        """計算單格倉位大小 (BTC quantity)"""
        if not self.state or self.state.grid_balance <= 0:
            return 0.0
        risk_per_trade = getattr(Config, 'GRID_RISK_PER_TRADE', 0.01)
        levels = self.state.grid_levels
        # base is USD allocation per level; divide by price to get BTC qty
        base_usd = self.state.grid_balance * risk_per_trade / max(1, levels)
        if price > 0:
            return base_usd * weight / price
        return base_usd * weight

    def _calc_unrealized_pnl(self, current_price: float) -> float:
        """計算所有格位未實現損益"""
        if not self.state:
            return 0.0
        pnl = 0.0
        for pos in self.state.active_positions:
            if pos['side'] == 'LONG':
                pnl += (current_price - pos['entry']) * pos['size']
            else:
                pnl += (pos['entry'] - current_price) * pos['size']
        return pnl

    def _check_notional_limit(self, new_size: float, price: float) -> bool:
        """檢查是否超過最大名義曝險"""
        max_notional = getattr(Config, 'GRID_MAX_NOTIONAL', 0.0)
        if max_notional <= 0:
            leverage = getattr(Config, 'LEVERAGE', 3)
            max_notional = self.state.grid_balance * leverage if self.state else 0
        if max_notional <= 0:
            return True
        current_notional = sum(p['size'] * price for p in self.state.active_positions)
        return (current_notional + new_size * price) <= max_notional
