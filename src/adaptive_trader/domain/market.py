"""Typed market concepts shared by Spot and USD-M Futures research."""

from enum import StrEnum


class MarketType(StrEnum):
    SPOT = "SPOT"
    USD_M_FUTURES = "USD_M_FUTURES"


class ContractType(StrEnum):
    NONE = "NONE"
    PERPETUAL = "PERPETUAL"


class MarginMode(StrEnum):
    NONE = "NONE"
    ISOLATED = "ISOLATED"


class PositionSide(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradingMode(StrEnum):
    SPOT_LONG_ONLY = "SPOT_LONG_ONLY"
    FUTURES_LONG_ONLY = "FUTURES_LONG_ONLY"
    FUTURES_SHORT_ONLY = "FUTURES_SHORT_ONLY"
    FUTURES_LONG_SHORT = "FUTURES_LONG_SHORT"
