"""Deterministic in-memory market data provider."""

from adaptive_trader.domain.models import Candle


class InMemoryMarketDataProvider:
    def __init__(self, candles: tuple[Candle, ...]) -> None:
        self._candles = candles

    def get_candles(self, symbol: str, limit: int) -> tuple[Candle, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        selected = tuple(candle for candle in self._candles if candle.symbol == symbol)
        return selected[-limit:]
