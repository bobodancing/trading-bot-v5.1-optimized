"""
Trading Bot — 策略拔插平台

支援多策略架構：V7 Structure / V53 SOP / legacy V6 Pyramid。
"""

__version__ = "7.0.0"

from trader.config import Config
from trader.infrastructure.api_client import BinanceFuturesClient
from trader.infrastructure.notifier import TelegramNotifier
from trader.indicators.technical import (
    TechnicalAnalysis,
    DynamicThresholdManager,
    MTFConfirmation,
    MarketFilter,
)
from trader.risk.manager import PrecisionHandler, RiskManager, SignalTierSystem
