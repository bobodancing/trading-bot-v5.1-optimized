"""V6 Strategies — Strategy Pattern 模組"""

from trader.strategies.base import Action, DecisionDict, TradingStrategy, StrategyFactory, _apply_common_pre
from trader.strategies.v6_pyramid import V6PyramidStrategy   # triggers registration
from trader.strategies.v53_sop import V53SopStrategy         # triggers registration
from trader.strategies.v7_structure import V7StructureStrategy  # triggers registration
from trader.strategies.v54_noscale import V54NoScaleStrategy    # triggers registration

__all__ = [
    'Action', 'DecisionDict', 'TradingStrategy', 'StrategyFactory', '_apply_common_pre',
    'V6PyramidStrategy', 'V53SopStrategy', 'V7StructureStrategy', 'V54NoScaleStrategy',
]
