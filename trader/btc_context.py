"""
BTC trend and regime context manager -- extracted from bot.py (Phase 3).

Resolves BTC trend (1D EMA fallback) and regime (4H ADX+BBW) for
signal filtering and grid routing.
"""

import logging
from typing import Dict, Optional

import pandas as pd

from trader.config import Config
from trader.indicators.technical import TechnicalAnalysis, _bbw, _adx
from trader.infrastructure.notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class BTCContextManager:
    """Owns BTC regime and trend context state, resolves once per cycle."""

    def __init__(self, bot):
        self.bot = bot

    def check_btc_trend(self) -> Optional[str]:
        """Fetch BTC 1D EMA20/50 trend. Returns 'LONG', 'SHORT', 'RANGING', or None."""
        return self.get_daily_btc_trend_context().get('trend')

    def make_btc_context(
        self,
        *,
        source: str,
        trend: Optional[str] = None,
        regime: Optional[str] = None,
        detected: Optional[str] = None,
        direction: Optional[str] = None,
        candle_time: Optional[pd.Timestamp] = None,
        reason: str = "ok",
    ) -> Dict[str, object]:
        return {
            "source": source,
            "trend": trend,
            "regime": regime or "UNKNOWN",
            "detected": detected or "UNKNOWN",
            "direction": direction or "UNKNOWN",
            "candle_time": format_candle_time(candle_time),
            "reason": reason,
        }

    def update_btc_regime_context(self) -> Dict[str, object]:
        """Update 4H BTC regime state once per cycle for routing + trend guard."""
        bot = self.bot
        try:
            btc_df_4h = bot.data_provider.fetch_ohlcv("BTC/USDT", Config.REGIME_TIMEFRAME, limit=60)
        except Exception as e:
            context = self.make_btc_context(source="none", reason=f"regime_fetch_failed:{e}")
            bot._btc_regime_context = context
            logger.warning(
                "BTC regime context unavailable: "
                f"source={context['source']} regime={context['regime']} detected={context['detected']} "
                f"direction={context['direction']} candle={context['candle_time']} reason={context['reason']}"
            )
            return context

        candle_time = get_last_candle_time(btc_df_4h)
        if btc_df_4h is None or btc_df_4h.empty:
            context = self.make_btc_context(
                source="none",
                candle_time=candle_time,
                reason="regime_fetch_empty",
            )
            bot._btc_regime_context = context
            logger.warning(
                "BTC regime context unavailable: "
                f"source={context['source']} regime={context['regime']} detected={context['detected']} "
                f"direction={context['direction']} candle={context['candle_time']} reason={context['reason']}"
            )
            return context

        btc_df_4h = TechnicalAnalysis.calculate_indicators(btc_df_4h)
        btc_df_4h['bbw'] = _bbw(btc_df_4h['close'])
        adx_data = _adx(btc_df_4h['high'], btc_df_4h['low'], btc_df_4h['close'], length=14)
        if adx_data is not None:
            for col in adx_data.columns:
                if col.startswith('DMP') or col.startswith('DMN'):
                    btc_df_4h[col] = adx_data[col]

        old_regime = bot.regime_engine.current_regime
        regime = bot.regime_engine.update(btc_df_4h)
        if regime != old_regime:
            TelegramNotifier.notify_regime_change(old_regime, regime, Config.REGIME_CONFIRM_CANDLES)

        context = self.make_btc_context(
            source="regime",
            regime=regime,
            detected=bot.regime_engine.last_detected_regime,
            direction=bot.regime_engine.direction_hint,
            candle_time=bot.regime_engine.last_candle_time or candle_time,
            reason="regime_updated",
        )
        bot._btc_regime_context = context
        return context

    def get_daily_btc_trend_context(self) -> Dict[str, object]:
        """Resolve BTC trend from the conservative 1D EMA20/50 fallback."""
        bot = self.bot
        try:
            btc_df = bot.data_provider.fetch_ohlcv("BTC/USDT", "1d", limit=60)
            if btc_df is not None and len(btc_df) >= 50:
                btc_ema20 = btc_df['close'].ewm(span=20, adjust=False).mean().iloc[-1]
                btc_ema50 = btc_df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                if btc_ema50 != 0:
                    ema_diff = abs(btc_ema20 - btc_ema50) / btc_ema50
                    if ema_diff < Config.BTC_EMA_RANGING_THRESHOLD:
                        trend = "RANGING"
                    else:
                        trend = "LONG" if btc_ema20 > btc_ema50 else "SHORT"
                    return self.make_btc_context(
                        source="1d_fallback",
                        trend=trend,
                        direction=trend if trend in ("LONG", "SHORT") else None,
                        candle_time=get_last_candle_time(btc_df),
                        reason="daily_ema_fallback",
                    )
        except Exception as e:
            logger.warning(f"BTC trend check failed: {e}")
            return self.make_btc_context(source="none", reason=f"daily_check_failed:{e}")

        candle_time = get_last_candle_time(btc_df) if 'btc_df' in locals() else None
        data_len = len(btc_df) if 'btc_df' in locals() and btc_df is not None else 0
        return self.make_btc_context(
            source="none",
            candle_time=candle_time,
            reason=f"insufficient_daily_data:{data_len}",
        )

    def resolve_btc_trend_context(self, log_event: bool = False) -> Dict[str, object]:
        """Resolve BTC trend once per cycle with 4H regime priority and 1D fallback."""
        bot = self.bot
        if Config.ENABLE_GRID_TRADING:
            regime_context = bot._btc_regime_context or self.make_btc_context(
                source="none",
                reason="regime_not_initialized",
            )
            regime = regime_context.get('regime')
            direction = regime_context.get('direction')
            detected = regime_context.get('detected')

            if regime == "RANGING":
                context = dict(regime_context)
                context['trend'] = "RANGING"
                context['reason'] = "regime_ranging"
            elif regime == "SQUEEZE":
                context = dict(regime_context)
                context['trend'] = None
                context['reason'] = "regime_squeeze_pause"
            elif direction in ("LONG", "SHORT"):
                context = dict(regime_context)
                context['trend'] = direction
                if detected == "UNKNOWN":
                    context['reason'] = "ambiguous_regime_keep_direction"
                else:
                    context['reason'] = "regime_direction"
            else:
                fallback_context = self.get_daily_btc_trend_context()
                if fallback_context.get('trend') is not None:
                    context = fallback_context
                    context['reason'] = (
                        f"fallback_after_{regime_context.get('reason', 'regime_unavailable')}"
                    )
                else:
                    context = self.make_btc_context(
                        source="none",
                        regime=regime if isinstance(regime, str) else None,
                        detected=detected if isinstance(detected, str) else None,
                        direction=direction if isinstance(direction, str) else None,
                        candle_time=bot.regime_engine.last_candle_time,
                        reason=(
                            f"regime_unavailable_and_1d_failed:"
                            f"{regime_context.get('reason', 'unknown')}"
                        ),
                    )
        else:
            context = self.get_daily_btc_trend_context()
            if context.get('trend') is not None:
                context['reason'] = "grid_disabled_daily_ema"

        if log_event:
            logger.info(
                "BTC trend resolved: "
                f"source={context['source']} regime={context['regime']} detected={context['detected']} "
                f"direction={context['direction']} trend={context.get('trend') or 'UNKNOWN'} "
                f"candle={context['candle_time']} reason={context['reason']}"
            )
        return context


# -- Shared utility functions (used by GridManager too) --

def get_last_candle_time(df: pd.DataFrame) -> Optional[pd.Timestamp]:
    if df is None or df.empty:
        return None
    if isinstance(df.index, pd.DatetimeIndex) and len(df.index) > 0:
        return df.index[-1]
    if 'timestamp' in df.columns and len(df['timestamp']) > 0:
        candle_time = df['timestamp'].iloc[-1]
        return pd.Timestamp(candle_time) if not pd.isna(candle_time) else None
    return None


def get_last_closed_candle_time(df: pd.DataFrame) -> Optional[pd.Timestamp]:
    if df is None or df.empty:
        return None
    closed_df = df.iloc[:-1] if len(df) > 1 else df
    if closed_df.empty:
        return get_last_candle_time(df)
    return get_last_candle_time(closed_df)


def format_candle_time(candle_time: Optional[pd.Timestamp]) -> str:
    if candle_time is None or pd.isna(candle_time):
        return "n/a"
    return pd.Timestamp(candle_time).isoformat()
