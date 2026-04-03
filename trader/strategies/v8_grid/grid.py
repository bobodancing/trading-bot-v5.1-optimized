# trader/strategies/v8_grid/grid.py
"""
V8 ATR adaptive grid engine.

The engine is intentionally order-first and state-second:
- tick() only proposes actions
- confirm_action() mutates persisted grid state after order success
"""
import logging
import time
from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from trader.config import Config
from trader.persistence import load_grid_state, save_grid_state

logger = logging.getLogger(__name__)


@dataclass
class GridAction:
    type: str       # 'OPEN' | 'CLOSE'
    side: str       # 'LONG' | 'SHORT'
    level: int      # positive = above center, negative = below
    size: float     # BTC quantity
    price: float = 0.0
    entry_price: Optional[float] = None


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
    converge_market_ts: object = None
    activated_at: float = 0.0
    last_cooldown_time: float = 0.0

    def to_dict(self) -> dict:
        return {
            'center': self.center,
            'upper': self.upper,
            'lower': self.lower,
            'grid_levels': self.grid_levels,
            'grid_spacing': self.grid_spacing,
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
        for key, value in d.items():
            if key == 'level_weights':
                state.level_weights = {int(level): weight for level, weight in value.items()}
            elif hasattr(state, key):
                setattr(state, key, value)
        return state


class V8AtrGrid:
    """ATR-based virtual grid engine for BTC ranging regime."""

    def __init__(self, api_client=None, notifier=None):
        self.state: Optional[GridState] = None
        self.api_client = api_client
        self.notifier = notifier
        self._pending_reset_df: Optional[pd.DataFrame] = None
        self._pending_cooldown_time: float = 0.0

    def activate(self, df_4h: pd.DataFrame, grid_balance: float):
        """Build the grid when regime enters RANGING."""
        df_4h = self._drop_unfinished_candle(df_4h)
        close = df_4h['close']
        sma_period = getattr(Config, 'GRID_SMA_PERIOD', 20)
        atr_period = getattr(Config, 'GRID_ATR_PERIOD', 14)
        atr_multiplier = getattr(Config, 'GRID_ATR_MULTIPLIER', 2.5)
        levels = getattr(Config, 'GRID_LEVELS', 5)

        center = close.rolling(sma_period).mean().iloc[-1]
        high = df_4h['high']
        low = df_4h['low']
        prev_close = close.shift(1)
        tr = pd.concat(
            [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(atr_period).mean().iloc[-1]

        upper = center + atr_multiplier * atr
        lower = center - atr_multiplier * atr
        spacing = (upper - lower) / (levels * 2)

        weight_center = getattr(Config, 'GRID_WEIGHT_CENTER', 0.5)
        weight_edge = getattr(Config, 'GRID_WEIGHT_EDGE', 1.5)
        weights = {}
        for level in range(1, levels + 1):
            ratio = (level - 1) / max(1, levels - 1)
            weights[level] = weight_center + ratio * (weight_edge - weight_center)

        self.state = GridState(
            center=center,
            upper=upper,
            lower=lower,
            grid_levels=levels,
            grid_spacing=spacing,
            grid_balance=grid_balance,
            level_weights=weights,
            activated_at=time.time(),
        )
        self._pending_reset_df = None
        self._pending_cooldown_time = 0.0
        logger.info(
            f"Grid activated: center={center:.0f}, "
            f"range={lower:.0f}-{upper:.0f}, spacing={spacing:.0f}, "
            f"{levels * 2} levels"
        )

    def tick(self, current_price: float, df_1h: pd.DataFrame, market_ts=None) -> List[GridAction]:
        """Return the actions for this cycle without mutating active_positions."""
        if self.state is None:
            return []

        df_1h = self._drop_unfinished_candle(df_1h)
        actions: List[GridAction] = []
        pending_open_positions = []

        sma_period = getattr(Config, 'GRID_SMA_PERIOD', 20)
        current_sma = df_1h['close'].rolling(sma_period).mean().iloc[-1]
        drift = abs(current_sma - self.state.center)
        drift_ratio = getattr(Config, 'GRID_RESET_DRIFT_RATIO', 0.5)
        near_center = (
            self.state.grid_spacing > 0
            and abs(current_price - self.state.center) < self.state.grid_spacing * 1.5
        )
        if (
            not pd.isna(current_sma)
            and near_center
            and self.state.grid_spacing > 0
            and drift > drift_ratio * self.state.grid_spacing
        ):
            logger.info(
                f"Grid reset: SMA drifted {drift:.0f} > "
                f"{drift_ratio}*spacing({self.state.grid_spacing:.0f})"
            )
            close_actions = self.force_close_all("sma_drift_reset")
            actions.extend(close_actions)
            if close_actions:
                self._pending_reset_df = df_1h.copy()
            else:
                self.activate(df_1h, self.state.grid_balance)
            return actions

        max_dd = getattr(Config, 'GRID_MAX_DRAWDOWN', 0.05)
        unrealized = self._calc_unrealized_pnl(current_price)
        if self.state.grid_balance > 0 and unrealized / self.state.grid_balance < -max_dd:
            logger.warning(f"Grid drawdown limit: {unrealized / self.state.grid_balance:.1%}")
            close_actions = self.force_close_all("drawdown_limit")
            actions.extend(close_actions)
            if close_actions:
                self._pending_cooldown_time = time.time()
            else:
                self.state.last_cooldown_time = time.time()
            return actions

        if self.state.converging:
            timeout_h = getattr(Config, 'GRID_CONVERGE_TIMEOUT_HOURS', 72)
            timed_out = False
            if market_ts is not None and self.state.converge_market_ts is not None:
                elapsed = (market_ts - self.state.converge_market_ts).total_seconds()
                timed_out = elapsed > timeout_h * 3600
            elif self.state.converge_start_time > 0:
                timed_out = time.time() - self.state.converge_start_time > timeout_h * 3600
            if timed_out:
                logger.info(f"Grid converge timeout ({timeout_h}h) -> force closing")
                actions.extend(self.force_close_all("converge_timeout"))
                return actions

        if self.state.grid_spacing <= 0:
            return actions
        level = round((current_price - self.state.center) / self.state.grid_spacing)

        for position in list(self.state.active_positions):
            should_close = False
            if position['side'] == 'SHORT' and current_price <= self.state.center + 0.5 * self.state.grid_spacing:
                should_close = True
            elif position['side'] == 'LONG' and current_price >= self.state.center - 0.5 * self.state.grid_spacing:
                should_close = True

            if should_close:
                actions.append(
                    GridAction(
                        type='CLOSE',
                        side=position['side'],
                        level=position['level'],
                        size=position['size'],
                        price=current_price,
                        entry_price=position['entry'],
                    )
                )

        if not self.state.converging and abs(level) >= 1:
            occupied_levels = {position['level'] for position in self.state.active_positions}
            occupied_levels.update(position['level'] for position in pending_open_positions)

            if level > 0:
                for grid_level in range(1, min(level + 1, self.state.grid_levels + 1)):
                    if grid_level in occupied_levels:
                        continue
                    weight = self.state.level_weights.get(grid_level, 1.0)
                    size = self._calc_level_size(weight, current_price)
                    if not self._check_notional_limit(size, current_price, pending_open_positions):
                        continue
                    if not self._check_total_risk_limit(size, current_price, pending_open_positions):
                        continue

                    actions.append(
                        GridAction(
                            type='OPEN',
                            side='SHORT',
                            level=grid_level,
                            size=size,
                            price=current_price,
                        )
                    )
                    pending_open_positions.append(
                        {
                            'level': grid_level,
                            'side': 'SHORT',
                            'entry': current_price,
                            'size': size,
                        }
                    )
                    occupied_levels.add(grid_level)
            else:
                for grid_level in range(-1, max(level - 1, -(self.state.grid_levels + 1)), -1):
                    if grid_level in occupied_levels:
                        continue
                    abs_level = abs(grid_level)
                    weight = self.state.level_weights.get(abs_level, 1.0)
                    size = self._calc_level_size(weight, current_price)
                    if not self._check_notional_limit(size, current_price, pending_open_positions):
                        continue
                    if not self._check_total_risk_limit(size, current_price, pending_open_positions):
                        continue

                    actions.append(
                        GridAction(
                            type='OPEN',
                            side='LONG',
                            level=grid_level,
                            size=size,
                            price=current_price,
                        )
                    )
                    pending_open_positions.append(
                        {
                            'level': grid_level,
                            'side': 'LONG',
                            'entry': current_price,
                            'size': size,
                        }
                    )
                    occupied_levels.add(grid_level)

        return actions

    def converge(self, market_ts=None):
        """Enter converge mode and stop opening new grid levels."""
        if self.state and not self.state.converging:
            self.state.converging = True
            self.state.converge_start_time = time.time()
            self.state.converge_market_ts = market_ts
            logger.info("Grid entering converge mode")

    def force_close_all(self, reason: str) -> List[GridAction]:
        """Return close actions for every active position without clearing state yet."""
        if not self.state:
            return []

        actions = []
        for position in self.state.active_positions:
            actions.append(
                GridAction(
                    type='CLOSE',
                    side=position['side'],
                    level=position['level'],
                    size=position['size'],
                    entry_price=position['entry'],
                )
            )
        logger.info(f"Grid force close all: {reason} ({len(actions)} positions)")
        return actions

    def confirm_action(self, action: GridAction):
        """Persist grid state only after the exchange confirms the order."""
        if not self.state:
            return

        if action.type == 'OPEN':
            if self._find_active_position(action.level, action.side) is None:
                self.state.active_positions.append(
                    {
                        'level': action.level,
                        'side': action.side,
                        'entry': action.price,
                        'size': action.size,
                    }
                )
            return

        if action.type != 'CLOSE':
            return

        position = self._find_active_position(action.level, action.side)
        if position is not None:
            self.state.active_positions.remove(position)

        if action.entry_price is not None:
            self.state.grid_balance += self._calc_realized_pnl(
                action.side,
                action.entry_price,
                action.price,
                action.size,
            )

        if self.state.active_positions:
            return

        if self._pending_cooldown_time > 0:
            self.state.last_cooldown_time = self._pending_cooldown_time
            self._pending_cooldown_time = 0.0

        if self._pending_reset_df is not None:
            reset_df = self._pending_reset_df
            self._pending_reset_df = None
            self.activate(reset_df, self.state.grid_balance)

    def deactivate(self):
        """Fully deactivate the grid engine."""
        self.state = None
        self._pending_reset_df = None
        self._pending_cooldown_time = 0.0

    def save_state(self):
        if self.state:
            save_grid_state(self.state.to_dict())

    def load_state(self):
        data = load_grid_state()
        if data:
            self.state = GridState.from_dict(data)

    def _calc_level_size(self, weight: float, price: float = 0.0) -> float:
        """Return BTC quantity for one grid level."""
        if not self.state or self.state.grid_balance <= 0:
            return 0.0
        risk_per_trade = getattr(Config, 'GRID_RISK_PER_TRADE', 0.01)
        levels = self.state.grid_levels
        base_usd = self.state.grid_balance * risk_per_trade / max(1, levels)
        if price > 0:
            return base_usd * weight / price
        return base_usd * weight

    def _calc_unrealized_pnl(self, current_price: float) -> float:
        """Return total unrealized PnL across active grid positions."""
        if not self.state:
            return 0.0

        pnl = 0.0
        for position in self.state.active_positions:
            pnl += self._calc_realized_pnl(
                position['side'],
                position['entry'],
                current_price,
                position['size'],
            )
        return pnl

    def _check_notional_limit(
        self,
        new_size: float,
        price: float,
        pending_positions: Optional[list] = None,
    ) -> bool:
        """Check the configured notional cap."""
        max_notional = getattr(Config, 'GRID_MAX_NOTIONAL', 0.0)
        if max_notional <= 0:
            leverage = getattr(Config, 'LEVERAGE', 3)
            max_notional = self.state.grid_balance * leverage if self.state else 0.0
        if max_notional <= 0:
            return True

        positions = self._combined_positions(pending_positions)
        current_notional = sum(position['size'] * price for position in positions)
        return (current_notional + new_size * price) <= max_notional

    def _check_total_risk_limit(
        self,
        new_size: float,
        price: float,
        pending_positions: Optional[list] = None,
    ) -> bool:
        """Respect the configured total grid budget before opening more levels."""
        if not self.state or self.state.grid_balance <= 0:
            return True

        max_total_risk = getattr(Config, 'GRID_MAX_TOTAL_RISK', 0.0)
        if max_total_risk <= 0:
            return True

        positions = self._combined_positions(pending_positions)
        current_risk = sum(position['size'] * price for position in positions)
        return (current_risk + new_size * price) <= (self.state.grid_balance * max_total_risk)

    def _combined_positions(self, pending_positions: Optional[list] = None) -> list:
        positions = list(self.state.active_positions) if self.state else []
        if pending_positions:
            positions.extend(pending_positions)
        return positions

    def _find_active_position(self, level: int, side: str) -> Optional[dict]:
        if not self.state:
            return None
        for position in self.state.active_positions:
            if position['level'] == level and position['side'] == side:
                return position
        return None

    def _drop_unfinished_candle(self, df: pd.DataFrame) -> pd.DataFrame:
        if df is None or df.empty:
            return df
        if len(df) > 1:
            return df.iloc[:-1].copy()
        return df.copy()

    def _calc_realized_pnl(self, side: str, entry_price: float, exit_price: float, size: float) -> float:
        if side == 'LONG':
            return (exit_price - entry_price) * size
        return (entry_price - exit_price) * size

