"""
v6/core.py — 已拆解（Phase 2 重構）

所有 Class 已移至各自的新模組。此檔案僅保留向下相容的 re-export，
方便任何尚未更新 import 路徑的外部程式碼過渡使用。

新模組路徑：
  BinanceFuturesClient  → v6.infrastructure.api_client
  TelegramNotifier      → v6.infrastructure.notifier
  TechnicalAnalysis     → v6.indicators.technical
  DynamicThresholdManager → v6.indicators.technical
  MTFConfirmation       → v6.indicators.technical
  MarketFilter          → v6.indicators.technical
  PrecisionHandler      → v6.risk.manager
  RiskManager           → v6.risk.manager
  SignalTierSystem      → v6.risk.manager
  _ema, _sma, _atr, _adx → v6.indicators.technical
"""

from trader.infrastructure.api_client import BinanceFuturesClient
from trader.infrastructure.notifier import TelegramNotifier
from trader.indicators.technical import (
    _ema,
    _sma,
    _atr,
    _adx,
    TechnicalAnalysis,
    DynamicThresholdManager,
    MTFConfirmation,
    MarketFilter,
)
from trader.risk.manager import PrecisionHandler, RiskManager, SignalTierSystem

__all__ = [
    'BinanceFuturesClient',
    'TelegramNotifier',
    '_ema', '_sma', '_atr', '_adx',
    'TechnicalAnalysis',
    'DynamicThresholdManager',
    'MTFConfirmation',
    'MarketFilter',
    'PrecisionHandler',
    'RiskManager',
    'SignalTierSystem',
]
