"""V6 Strategies — Strategy Pattern 模組"""

from trader.strategies.base import Action, DecisionDict, TradingStrategy, StrategyFactory, _apply_common_pre
from trader.strategies.v6_pyramid import V6PyramidStrategy   # triggers registration
from trader.strategies.v53_sop import V53SopStrategy         # triggers registration

__all__ = [
    'Action', 'DecisionDict', 'TradingStrategy', 'StrategyFactory', '_apply_common_pre',
    'V6PyramidStrategy', 'V53SopStrategy',
]
