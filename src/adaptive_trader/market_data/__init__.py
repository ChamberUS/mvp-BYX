"""Public market data providers for local research and backtests."""

from adaptive_trader.market_data.binance_futures_public import BinanceFuturesPublicClient
from adaptive_trader.market_data.binance_public import BinancePublicClient
from adaptive_trader.market_data.context import MarketContextBuilder
from adaptive_trader.market_data.exceptions import (
    InvalidMarketDataError,
    MarketDataError,
    MarketDataRateLimitError,
    MarketDataResponseError,
    MarketDataTimeoutError,
)
from adaptive_trader.market_data.futures_history import (
    FuturesDownloadStats,
    FuturesHistoricalDownloader,
)
from adaptive_trader.market_data.history import DownloadStats, HistoricalCandleDownloader
from adaptive_trader.market_data.memory import InMemoryMarketDataProvider

__all__ = [
    "BinancePublicClient",
    "BinanceFuturesPublicClient",
    "DownloadStats",
    "FuturesDownloadStats",
    "FuturesHistoricalDownloader",
    "HistoricalCandleDownloader",
    "InMemoryMarketDataProvider",
    "InvalidMarketDataError",
    "MarketContextBuilder",
    "MarketDataError",
    "MarketDataRateLimitError",
    "MarketDataResponseError",
    "MarketDataTimeoutError",
]
