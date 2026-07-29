"""Typed failures from public market data access."""


class MarketDataError(RuntimeError):
    """Base class for market data failures."""


class MarketDataTimeoutError(MarketDataError):
    """The public request timed out after bounded retries."""


class MarketDataRateLimitError(MarketDataError):
    """The public API rate-limited or temporarily banned the client."""


class MarketDataResponseError(MarketDataError):
    """The public API returned an invalid HTTP or JSON response."""


class InvalidMarketDataError(MarketDataError):
    """The response could not be converted into a valid domain candle."""
