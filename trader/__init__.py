"""
Trading Bot V6.0 — 終極滾倉版

三段式金字塔加倉系統，透過 Swing Point 結構和動態風險壓縮實現趨勢重拳出擊。
"""

__version__ = "6.0.0"

from trader.config import Config, ConfigV6
from trader.infrastructure.api_client import BinanceFuturesClient
from trader.infrastructure.notifier import TelegramNotifier
from trader.indicators.technical import (
    TechnicalAnalysis,
    DynamicThresholdManager,
    MTFConfirmation,
    MarketFilter,
)
from trader.risk.manager import PrecisionHandler, RiskManager, SignalTierSystem
