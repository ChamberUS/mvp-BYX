"""Market data providers for backtests and paper trading."""

from adaptive_trader.market_data.binance_public import BinancePublicClient
from adaptive_trader.market_data.context import MarketContextBuilder
from adaptive_trader.market_data.exceptions import (
    InvalidMarketDataError,
    MarketDataError,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTimeoutError,
)
from adaptive_trader.market_data.history import DownloadStats, HistoricalCandleDownloader
from adaptive_trader.market_data.memory import InMemoryMarketDataProvider

__all__ = [
    "BinancePublicClient",
    "DownloadStats",
    "HistoricalCandleDownloader",
    "InMemoryMarketDataProvider",
    "InvalidMarketDataError",
    "MarketContextBuilder",
    "MarketDataError",
    "MarketDataRateLimitError",
    "MarketDataResponseError",
    "MarketDataTimeoutError",
]
