"""
Position monitoring manager -- extracted from bot.py (Phase 3).

Handles position lifecycle: monitoring, close, stage add, partial reduce.
"""

import logging
from datetime import datetime, timezone
from typing import Dict, Optional

from trader.config import Config
from trader.indicators.technical import TechnicalAnalysis
from trader.infrastructure.notifier import TelegramNotifier
from trader.positions import PositionManager
from trader.strategies.base import Action

logger = logging.getLogger(__name__)


def _trade_log(fields: dict):
    """Emit structured [TRADE] log line for log_summarizer.py"""
    parts = ' | '.join(f'{k}={v}' for k, v in fields.items())
    logger.info(f"[TRADE] {parts}")


class PositionMonitor:
    """Manages position monitoring, close, staging, and partial reduce."""

    def __init__(self, bot):
        self.bot = bot

    def monitor_positions(self):
        """Monitor all active positions."""
        bot = self.bot
        if not bot.active_trades:
            return

        logger.debug(f"Monitoring {len(bot.active_trades)} positions...")

        closed_symbols = []
        state_changed = False

        for symbol, pm in bot.active_trades.items():
            try:
                if pm.is_closed:
                    closed_symbols.append(symbol)
                    continue

                ticker = bot.fetch_ticker(symbol)
                current_price = ticker['last']

                df_1h = bot.fetch_ohlcv(symbol, Config.TIMEFRAME_SIGNAL, limit=50)
                if not df_1h.empty:
                    df_1h = TechnicalAnalysis.calculate_indicators(df_1h)

                df_4h = None
                if pm.strategy_name in ("v6_pyramid", "v7_structure"):
                    df_4h = bot.fetch_ohlcv(symbol, '4h', limit=50)
                    if df_4h is not None and not df_4h.empty:
                        df_4h = TechnicalAnalysis.calculate_indicators(df_4h)

                decision = pm.monitor(current_price, df_1h, df_4h)
                action = decision.get('action', Action.HOLD)
                new_sl = decision.get('new_sl')

                if new_sl is not None:
                    old_sl = pm.current_sl
                    bot._update_hard_stop_loss(pm, new_sl)
                    state_changed = True
                    if old_sl > 0 and abs(new_sl - old_sl) / old_sl > 0.01:
                        TelegramNotifier.notify_action(
                            symbol, '1.5R移損',
                            current_price,
                            f"SL ${old_sl:.2f} → ${new_sl:.2f}"
                        )

                if action == Action.CLOSE:
                    if self.handle_close(pm, current_price):
                        closed_symbols.append(symbol)
                        state_changed = True

                elif action == Action.ADD:
                    stage = decision.get('add_stage', 2)
                    if stage == 2:
                        self.handle_stage2(pm, current_price, df_1h, decision=decision)
                    else:
                        self.handle_stage3(pm, current_price, df_1h, decision=decision)
                    state_changed = True

                elif action == Action.PARTIAL_CLOSE:
                    close_pct = decision.get('close_pct', 0.3)
                    pct_int = round(close_pct * 100)
                    reason = decision.get('reason', 'PARTIAL_CLOSE')
                    label = "2.0R" if "20R" in reason or "25R" in reason else "1.5R"
                    self.handle_v53_reduce(pm, pct_int, label, current_price)
                    state_changed = True

                if pm.side == 'LONG':
                    profit_pct = (current_price - pm.avg_entry) / pm.avg_entry * 100
                else:
                    profit_pct = (pm.avg_entry - current_price) / pm.avg_entry * 100

                if pm.strategy_name == "v54_noscale":
                    mode = "V54"
                elif pm.strategy_name == "v7_structure":
                    mode = f"V7/S{pm.stage}"
                elif pm.strategy_name == "v6_pyramid":
                    mode = f"V6/S{pm.stage}"
                else:
                    mode = "V53"
                logger.debug(
                    f"{symbol} [{mode}]: ${current_price:.2f} | "
                    f"PnL={profit_pct:+.2f}% | SL=${pm.current_sl:.2f}"
                )

                _trade_log({
                    **bot._build_log_base('POSITION_UPDATE', pm.trade_id, symbol, pm.side),
                    'price': f'{current_price:.2f}',
                    'pnl_pct': f'{profit_pct:+.2f}',
                    'sl': f'{pm.current_sl:.2f}',
                    'stage': pm.stage,
                    'mode': mode,
                })

            except Exception as e:
                logger.error(f"{symbol} monitor error: {e}")

            if pm.pending_stop_cancels:
                order_id = pm.pending_stop_cancels[0]
                try:
                    success = bot.execution_engine.cancel_stop_loss_order(pm.symbol, order_id)
                    if success:
                        pm.pending_stop_cancels.pop(0)
                        logger.info(f"[{pm.symbol}] pending stop cancel cleared: {order_id}")
                except Exception as e:
                    logger.warning(f"[{pm.symbol}] pending stop cancel retry failed: {e}")

        # Clean up closed positions
        for symbol in closed_symbols:
            pm = bot.active_trades.get(symbol)
            if pm:
                for order_id in pm.pending_stop_cancels:
                    try:
                        bot.execution_engine.cancel_stop_loss_order(pm.symbol, order_id)
                        logger.info(f"[{pm.symbol}] cleanup residual stop: {order_id}")
                    except Exception as e:
                        logger.warning(f"[{pm.symbol}] cleanup residual stop failed: {order_id} -- {e}")

                if pm.exit_reason in ('early_stop_r', 'stage1_timeout'):
                    bot.early_exit_cooldown[symbol] = datetime.now(timezone.utc)

            if symbol in bot.active_trades:
                del bot.active_trades[symbol]
                bot.recently_exited[symbol] = datetime.now(timezone.utc)

        if state_changed or closed_symbols:
            bot._save_positions()

        logger.debug(f"Monitor done | remaining: {len(bot.active_trades)}")

        active_summary = ','.join(
            f'{s}({t.side}/S{t.stage}/${t.total_size * t.avg_entry:.0f})'
            for s, t in bot.active_trades.items()
        ) or "none"

        cycle_balance = bot.risk_manager.get_balance() if not Config.V6_DRY_RUN else 10000.0
        cycle_unrealized_pnl = 0.0
        for pos in bot.active_trades.values():
            try:
                current_price = bot.fetch_ticker(pos.symbol)['last']
                if current_price and pos.avg_entry and pos.total_size:
                    if pos.side == 'LONG':
                        pnl = (current_price - pos.avg_entry) * pos.total_size
                    else:
                        pnl = (pos.avg_entry - current_price) * pos.total_size
                    cycle_unrealized_pnl += pnl
            except Exception:
                pass

        net_pnl_pct = round(
            (cycle_balance - bot.initial_balance) / bot.initial_balance * 100, 2
        ) if bot.initial_balance else 0.0
        _trade_log({
            'event': 'CYCLE_SUMMARY',
            'ts': datetime.now(timezone.utc).isoformat(),
            'bot': 'v7.0',
            'cycle': getattr(bot, 'cycle_count', 0),
            'active': len(bot.active_trades),
            'active_trades_count': len(bot.active_trades),
            'closed': len(closed_symbols),
            'symbols': active_summary,
            'balance': f'{cycle_balance:.2f}',
            'unrealized_pnl': f'{cycle_unrealized_pnl:.2f}',
            'net_pnl_pct': f'{net_pnl_pct:+.2f}',
        })

    def handle_close(self, pm: PositionManager, current_price: float = 0.0) -> bool:
        """
        Handle position close.

        Returns:
            True  -- close succeeded, caller should remove position
            False -- close failed, pm.is_closed stays False, retry next cycle
        """
        bot = self.bot
        try:
            if current_price <= 0:
                try:
                    ticker = bot.fetch_ticker(pm.symbol)
                    current_price = ticker['last']
                except Exception:
                    current_price = pm.avg_entry

            final_pnl = bot._calculate_pnl(pm.side, pm.total_size, current_price, pm.avg_entry)

            pnl_usdt = final_pnl + pm.realized_partial_pnl
            original_notional = pm.original_size * pm.avg_entry
            pnl_pct = (pnl_usdt / original_notional * 100) if original_notional > 0 else 0

            duration_h = (datetime.now(timezone.utc) - pm.entry_time).total_seconds() / 3600
            exit_reason = getattr(pm, 'exit_reason', None) or 'unknown'

            pm.highest_price = max(pm.highest_price, current_price)
            pm.lowest_price = min(pm.lowest_price, current_price)

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
                logger.info(f"[DRY_RUN] Close {pm.symbol} {pm.side} size={pm.total_size:.6f}")
                _trade_log({
                    **bot._build_log_base('TRADE_CLOSE', pm.trade_id, pm.symbol, pm.side),
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

            # Move stop order to pending cancels (non-blocking), close first
            if pm.stop_order_id:
                pm.pending_stop_cancels.append(pm.stop_order_id)
                pm.stop_order_id = None

            # Close order (rollback on failure: keep position, save to positions.json)
            try:
                bot._futures_close_position(pm.symbol, pm.side, pm.total_size)
            except Exception as close_err:
                logger.error(
                    f"{pm.symbol} close order failed (position preserved, retry next cycle): {close_err}"
                )
                bot._save_positions()
                return False

            logger.info(f"{pm.symbol} closed: {pm.side} size={pm.total_size:.6f}")

            _trade_log({
                **bot._build_log_base('TRADE_CLOSE', pm.trade_id, pm.symbol, pm.side),
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

            safe_capture = round(pnl_pct / mfe_pct, 4) if mfe_pct > 0.0001 else None

            bot.perf_db.record_trade({
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
                "strategy_name":   pm.strategy_name,
            })

            TelegramNotifier.notify_exit(pm.symbol, {
                'side': pm.side,
                'entry_price': pm.avg_entry,
                'exit_reason': exit_reason,
                'position_size': pm.total_size,
                'pnl_pct': pnl_pct,
            })

            return True

        except Exception as e:
            logger.error(f"{pm.symbol} _handle_close unexpected error: {e}")
            return False

    def handle_stage2(self, pm: PositionManager, current_price: float, df_1h, decision: dict = None):
        """Handle Stage 2 add."""
        bot = self.bot
        try:
            entry_price = current_price

            if pm.strategy_name == 'v7_structure':
                from trader.strategies.legacy.v7_structure import V7StructureStrategy
                new_sl = decision.get('new_sl') if decision else None
                if new_sl is None:
                    logger.error(f"{pm.symbol} V7 Stage 2: decision missing new_sl")
                    return

                if Config.V6_DRY_RUN:
                    balance = 10000.0
                else:
                    balance = bot.risk_manager.get_balance()

                total_risk_pct = bot._calc_total_risk_pct(balance)

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
                logger.warning(f"{pm.symbol} Stage 2 size=0, skip")
                return

            add_size = bot._validate_position_size(pm.symbol, add_size, entry_price, "Stage2")
            if add_size is None:
                return

            if Config.V6_DRY_RUN:
                logger.info(
                    f"[DRY_RUN] {pm.symbol} Stage 2 add: +{add_size:.6f} @ ${entry_price:.2f}"
                )
                v7_sl = decision.get('new_sl') if decision and pm.strategy_name == 'v7_structure' else None
                pm.add_stage2(entry_price, add_size, new_sl=v7_sl)
                return

            order_side = bot._get_close_side(pm.side)
            order_result = bot._futures_create_order(pm.symbol, order_side, add_size)

            fill_price = bot._extract_fill_price(order_result, entry_price)
            if fill_price != entry_price:
                logger.info(
                    f"{pm.symbol} Stage2 fill correction: signal${entry_price:.4f} -> actual${fill_price:.4f}"
                )

            v7_sl = decision.get('new_sl') if decision and pm.strategy_name == 'v7_structure' else None
            pm.add_stage2(fill_price, add_size, new_sl=v7_sl)

            bot._refresh_stop_loss(pm, pm.current_sl)

            if Config.AUTO_BACKUP_ON_STAGE_CHANGE:
                bot.persistence.backup_positions()

            logger.info(
                f"{pm.symbol} Stage 2 add done: +{add_size:.6f} @ ${fill_price:.2f} | "
                f"total={pm.total_size:.6f} | SL=${pm.current_sl:.2f}"
            )
            TelegramNotifier.notify_action(
                pm.symbol,
                'V7加倉' if pm.strategy_name == 'v7_structure' else '1.5R移損',
                fill_price,
                f"Stage2 add +{add_size:.6f} total={pm.total_size:.6f} SL=${pm.current_sl:.2f}"
            )

        except Exception as e:
            logger.error(f"{pm.symbol} Stage 2 add failed: {e}")

    def handle_stage3(self, pm: PositionManager, current_price: float, df_1h, decision: dict = None):
        """Handle Stage 3 add."""
        bot = self.bot
        try:
            entry_price = current_price

            if pm.strategy_name == 'v7_structure':
                from trader.strategies.legacy.v7_structure import V7StructureStrategy
                new_sl = decision.get('new_sl') if decision else None
                if new_sl is None:
                    logger.error(f"{pm.symbol} V7 Stage 3: decision missing new_sl")
                    return

                if Config.V6_DRY_RUN:
                    balance = 10000.0
                else:
                    balance = bot.risk_manager.get_balance()

                total_risk_pct = bot._calc_total_risk_pct(balance)

                add_size = V7StructureStrategy.calculate_add_size(
                    balance=balance,
                    risk_per_trade=Config.RISK_PER_TRADE,
                    entry_price=entry_price,
                    new_sl=new_sl,
                    max_position_percent=Config.MAX_POSITION_PERCENT,
                    max_total_risk=Config.MAX_TOTAL_RISK,
                    current_total_risk_pct=total_risk_pct,
                )
                swing_stop = new_sl
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
                    logger.warning(f"{pm.symbol} Stage 3: no swing point for SL, skip")
                    return

                atr_buffer = pm.atr * Config.SL_ATR_BUFFER if pm.atr else 0
                if pm.side == 'LONG':
                    swing_stop = swing_price - atr_buffer
                else:
                    swing_stop = swing_price + atr_buffer

                add_size = pm.calculate_stage3_size(entry_price, swing_stop)
            if add_size <= 0:
                logger.warning(f"{pm.symbol} Stage 3 size=0, skip")
                return

            add_size = bot._validate_position_size(pm.symbol, add_size, entry_price, "Stage3")
            if add_size is None:
                return

            if Config.V6_DRY_RUN:
                logger.info(
                    f"[DRY_RUN] {pm.symbol} Stage 3 add: +{add_size:.6f} @ ${entry_price:.2f} "
                    f"| swing SL=${swing_stop:.2f}"
                )
                pm.add_stage3(entry_price, add_size, swing_stop)
                return

            order_side = bot._get_close_side(pm.side)
            order_result = bot._futures_create_order(pm.symbol, order_side, add_size)

            fill_price = bot._extract_fill_price(order_result, entry_price)
            if fill_price != entry_price:
                logger.info(
                    f"{pm.symbol} Stage3 fill correction: signal${entry_price:.4f} -> actual${fill_price:.4f}"
                )

            pm.add_stage3(fill_price, add_size, swing_stop)

            bot._refresh_stop_loss(pm, pm.current_sl)

            if Config.AUTO_BACKUP_ON_STAGE_CHANGE:
                bot.persistence.backup_positions()

            logger.info(
                f"{pm.symbol} Stage 3 add done: +{add_size:.6f} @ ${fill_price:.2f} | "
                f"total={pm.total_size:.6f} | SL=${pm.current_sl:.2f} (swing)"
            )

        except Exception as e:
            logger.error(f"{pm.symbol} Stage 3 add failed: {e}")

    def handle_v53_reduce(self, pm: PositionManager, pct: int, label: str, current_price: float):
        """Handle V5.3 partial reduce."""
        bot = self.bot
        try:
            reduce_size = pm.total_size * (pct / 100.0)
            if reduce_size <= 0:
                return

            reduce_size = float(bot.precision_handler.format_quantity(pm.symbol, reduce_size))

            if Config.V6_DRY_RUN:
                partial_pnl = bot._calculate_pnl(pm.side, reduce_size, current_price, pm.avg_entry)
                pm.realized_partial_pnl += partial_pnl
                logger.info(
                    f"[DRY_RUN] {pm.symbol} {label} reduce: -{reduce_size:.6f} "
                    f"@ ${current_price:.2f} PnL=${partial_pnl:+.2f}"
                )
                TelegramNotifier.notify_action(
                    pm.symbol, 'target reduce',
                    current_price,
                    f"{label} -{reduce_size:.6f} PnL=${partial_pnl:+.2f}"
                )
                pm.total_size -= reduce_size
                _trade_log({
                    **bot._build_log_base('PARTIAL_CLOSE', pm.trade_id, pm.symbol, pm.side),
                    'label': label,
                    'reduce_size': f'{reduce_size:.6f}',
                    'reduce_price': f'{current_price:.2f}',
                    'partial_pnl': f'{partial_pnl:+.2f}',
                    'cumulative_partial_pnl': f'{pm.realized_partial_pnl:+.2f}',
                    'remaining_size': f'{pm.total_size:.6f}',
                })
                return

            order_result = bot._futures_close_position(pm.symbol, pm.side, reduce_size)

            fill_price = bot._extract_fill_price(order_result, current_price)
            if fill_price != current_price:
                logger.info(
                    f"{pm.symbol} {label} reduce fill correction: "
                    f"ticker${current_price:.4f} -> actual${fill_price:.4f}"
                )

            partial_pnl = bot._calculate_pnl(pm.side, reduce_size, fill_price, pm.avg_entry)
            pm.realized_partial_pnl += partial_pnl

            pm.total_size -= reduce_size

            bot._refresh_stop_loss(pm, pm.current_sl)

            logger.info(
                f"{pm.symbol} {label} reduce: -{reduce_size:.6f} @ ${fill_price:.2f} | "
                f"PnL=${partial_pnl:+.2f} cumul=${pm.realized_partial_pnl:+.2f} | "
                f"remaining={pm.total_size:.6f} | SL=${pm.current_sl:.2f}"
            )
            TelegramNotifier.notify_action(
                pm.symbol, 'target reduce',
                fill_price,
                f"{label} -{reduce_size:.6f} PnL=${partial_pnl:+.2f} remaining={pm.total_size:.6f}"
            )

            _trade_log({
                **bot._build_log_base('PARTIAL_CLOSE', pm.trade_id, pm.symbol, pm.side),
                'label': label,
                'reduce_size': f'{reduce_size:.6f}',
                'reduce_price': f'{fill_price:.2f}',
                'partial_pnl': f'{partial_pnl:+.2f}',
                'cumulative_partial_pnl': f'{pm.realized_partial_pnl:+.2f}',
                'remaining_size': f'{pm.total_size:.6f}',
            })

        except Exception as e:
            logger.error(f"{pm.symbol} reduce failed: {e}")
