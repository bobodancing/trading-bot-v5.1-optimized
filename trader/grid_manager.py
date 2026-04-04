"""
Grid trading manager — extracted from bot.py (Phase 3).

Handles V8 ATR Grid lifecycle: activation, tick, force-close, state persistence.
"""

import time
import logging
from datetime import datetime, timezone

from trader.config import Config
from trader.infrastructure.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class GridManager:
    """Manages V8 ATR Grid lifecycle, delegating exchange operations to bot."""

    def __init__(self, bot):
        self.bot = bot

    # -- public interface called from bot.run() --

    def scan_grid_signals(self):
        """Grid strategy scan -- BTC/USDT only."""
        if not Config.ENABLE_GRID_TRADING:
            return

        bot = self.bot

        # Cooldown check
        if bot.grid_engine.state and bot.grid_engine.state.last_cooldown_time > 0:
            elapsed = time.time() - bot.grid_engine.state.last_cooldown_time
            if elapsed < Config.GRID_COOLDOWN_HOURS * 3600:
                return

        btc_df_4h = bot.data_provider.fetch_ohlcv("BTC/USDT", "4h", limit=60)
        if btc_df_4h is None or btc_df_4h.empty:
            return

        # Activate if needed
        if not bot.grid_engine.state:
            balance = bot.risk_manager.get_balance()
            if not bot.pool_manager.activate_grid_pool(balance):
                return
            grid_balance = bot.pool_manager.get_grid_balance()
            bot.grid_engine.activate(btc_df_4h, grid_balance)
            if bot.grid_engine.state:
                TelegramNotifier.notify_grid_activated(
                    bot.grid_engine.state.center,
                    bot.grid_engine.state.lower,
                    bot.grid_engine.state.upper,
                    bot.grid_engine.state.grid_levels,
                )

        # Tick
        ticker = bot.fetch_ticker("BTC/USDT")
        if not ticker:
            return
        current_price = ticker['last']
        btc_df_1h = bot.data_provider.fetch_ohlcv("BTC/USDT", Config.TIMEFRAME_SIGNAL, limit=50)
        if btc_df_1h is None or btc_df_1h.empty:
            return

        market_ts = bot._get_last_closed_candle_time(btc_df_1h)
        if market_ts is None:
            market_ts = bot._get_last_closed_candle_time(btc_df_4h)

        actions = bot.grid_engine.tick(
            current_price,
            btc_df_1h,
            df_4h=btc_df_4h,
            market_ts=market_ts,
        )
        for action in actions:
            self.execute_grid_action(action, current_price)
        bot.grid_engine.save_state(bot.pool_manager.to_dict())

    def monitor_grid_state(self):
        """Drive grid lifecycle every cycle, even when no trend positions exist."""
        if not Config.ENABLE_GRID_TRADING:
            return

        bot = self.bot
        regime = str((bot._btc_regime_context or {}).get('regime') or bot.regime_engine.current_regime)

        if not bot.grid_engine.state:
            if regime == "RANGING":
                bot._scan_grid_signals()
            return

        if bot.grid_engine.state.converging:
            if not bot.grid_engine.state.active_positions:
                self.finalize_grid_shutdown_if_flat()
                return

            ticker = bot.fetch_ticker("BTC/USDT")
            if not ticker:
                bot.grid_engine.save_state(bot.pool_manager.to_dict())
                return

            current_price = ticker['last']
            for action in bot.grid_engine.force_close_all("regime_exit"):
                self.execute_grid_action(action, current_price)

            if bot.grid_engine.state and not bot.grid_engine.state.active_positions:
                self.finalize_grid_shutdown_if_flat()
            else:
                bot.grid_engine.save_state(bot.pool_manager.to_dict())
            return

        if regime != "RANGING":
            bot.grid_engine.converge(market_ts=bot._get_regime_market_ts())
            TelegramNotifier.notify_grid_stopped("converge", f"\u2192 {regime}")

            ticker = bot.fetch_ticker("BTC/USDT")
            if not ticker:
                bot.grid_engine.save_state(bot.pool_manager.to_dict())
                return

            current_price = ticker['last']
            for action in bot.grid_engine.force_close_all("regime_exit"):
                self.execute_grid_action(action, current_price)

            if bot.grid_engine.state and not bot.grid_engine.state.active_positions:
                self.finalize_grid_shutdown_if_flat()
            else:
                bot.grid_engine.save_state(bot.pool_manager.to_dict())
            return

        bot._scan_grid_signals()

    def restore_runtime_state(self):
        """Restore grid + pool state from persisted JSON."""
        bot = self.bot
        payload = bot.grid_engine.load_state() or {}
        if not bot.grid_engine.state:
            return

        pool_state = payload.get('pool_state', {}) if payload else {}
        if pool_state:
            bot.pool_manager.load_state(pool_state)
            return

        # Legacy v1 files only persisted grid_state; keep runtime balance semantics aligned.
        bot.pool_manager.load_state(
            {
                'grid_allocated': bot.grid_engine.state.grid_balance,
                'grid_realized_pnl': 0.0,
                'round_count': max(1, bot.pool_manager.round_count),
            }
        )

    def is_exchange_flat(self) -> bool:
        """Check if exchange has zero BTC grid exposure."""
        bot = self.bot
        if Config.V6_DRY_RUN:
            return True
        exchange_positions = bot.risk_manager.get_positions()
        if exchange_positions is None:
            return False
        exchange_map = bot._build_exchange_position_map(exchange_positions)
        long_amt = exchange_map.get(('BTCUSDT', 'LONG'), 0.0)
        short_amt = exchange_map.get(('BTCUSDT', 'SHORT'), 0.0)
        return long_amt <= 0 and short_amt <= 0

    # -- internal --

    def execute_grid_action(self, action, current_price: float):
        """Execute grid action (open/close)."""
        bot = self.bot

        if Config.V6_DRY_RUN:
            logger.info(f"[DRY_RUN] Grid {action.type} {action.side} L{action.level} "
                        f"size={action.size:.4f} @ {current_price:.0f}")
            return

        try:
            if action.type == 'OPEN':
                side = 'BUY' if action.side == 'LONG' else 'SELL'
                result = bot.futures_client.signed_request_json(
                    'POST', '/fapi/v1/order',
                    params={
                        'symbol': 'BTCUSDT',
                        'side': side,
                        'type': 'MARKET',
                        'quantity': f"{action.size:.4f}",
                        'positionSide': action.side,
                    }
                )
                if result and 'error' not in result:
                    fill_price = bot._extract_fill_price(result, current_price)
                    action.price = fill_price
                    bot.grid_engine.confirm_action(action)
                    TelegramNotifier.notify_grid_action('OPEN', action.side, action.level, fill_price, action.size)
                else:
                    logger.warning(f"Grid OPEN order rejected: {result}")
            elif action.type == 'CLOSE':
                side = 'SELL' if action.side == 'LONG' else 'BUY'
                result = bot.futures_client.signed_request_json(
                    'POST', '/fapi/v1/order',
                    params={
                        'symbol': 'BTCUSDT',
                        'side': side,
                        'type': 'MARKET',
                        'quantity': f"{action.size:.4f}",
                        'positionSide': action.side,
                    }
                )
                if result and 'error' not in result:
                    fill_price = bot._extract_fill_price(result, current_price)
                    entry_price = action.entry_price if action.entry_price is not None else current_price
                    action.price = fill_price
                    bot.grid_engine.confirm_action(action)
                    if action.side == 'LONG':
                        pnl = (fill_price - entry_price) * action.size
                    else:
                        pnl = (entry_price - fill_price) * action.size
                    bot.pool_manager.grid_realized_pnl += pnl
                    TelegramNotifier.notify_grid_close(action.level, action.side, fill_price, pnl)
                    self.record_grid_trade(action, entry_price, fill_price, pnl)
                else:
                    logger.warning(f"Grid CLOSE order rejected: {result}")
        except Exception as e:
            logger.error(f"Grid action failed: {action} \u2014 {e}")

    def record_grid_trade(self, action, entry_price: float, exit_price: float, pnl: float):
        """Record grid trade to performance.db."""
        bot = self.bot
        now = datetime.now(timezone.utc)
        bot.perf_db.record_trade({
            "trade_id": f"grid_{action.side}_{action.level}_{int(time.time())}",
            "symbol": "BTC/USDT",
            "side": action.side,
            "is_v6_pyramid": 0,
            "signal_tier": None,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "total_size": action.size,
            "initial_r": 0.0,
            "entry_time": now.isoformat(),
            "exit_time": now.isoformat(),
            "holding_hours": 0.0,
            "pnl_usdt": pnl,
            "pnl_pct": pnl / bot.pool_manager.grid_allocated * 100 if bot.pool_manager.grid_allocated > 0 else 0,
            "realized_r": 0.0,
            "mfe_pct": 0.0,
            "mae_pct": 0.0,
            "capture_ratio": None,
            "stage_reached": 0,
            "exit_reason": "grid_close",
            "market_regime": bot.regime_engine.current_regime,
            "entry_adx": None,
            "fakeout_depth_atr": None,
            "reverse_2b_depth_atr": None,
            "original_size": action.size,
            "partial_pnl_usdt": None,
            "btc_trend_aligned": None,
            "trend_adx": None,
            "mtf_aligned": None,
            "volume_grade": None,
            "tier_score": None,
            "strategy_name": "v8_atr_grid",
            "grid_level": action.level,
            "grid_round": bot.pool_manager.round_count,
        })

    def finalize_grid_shutdown_if_flat(self):
        """Deactivate grid if exchange confirms zero BTC exposure."""
        bot = self.bot
        if not bot.grid_engine.state:
            return
        if not self.is_exchange_flat():
            logger.info("Grid exit pending: internal state flat but exchange still shows BTC grid exposure")
            bot.grid_engine.save_state(bot.pool_manager.to_dict())
            return

        logger.info("Grid converge complete -> deactivating")
        bot.pool_manager.deactivate_grid_pool()
        bot.grid_engine.deactivate()
        bot.grid_engine.save_state(bot.pool_manager.to_dict())
