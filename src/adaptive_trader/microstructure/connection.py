"""Public stream capabilities and bounded connection/recovery accounting."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from adaptive_trader.domain.market import MarketType


@dataclass(frozen=True, slots=True)
class StreamCapabilities:
    market_type: MarketType
    websocket_base_url: str
    aggregate_trade_stream: str
    book_ticker_stream: str
    diff_depth_stream: str
    mark_price_stream: str | None
    depth_snapshot_url: str
    depth_snapshot_path: str
    market_websocket_base_url: str | None = None
    routed_connections_required: bool = False
    public_only: bool = True
    authenticated: bool = False
    order_capable: bool = False


def stream_capabilities(market_type: MarketType, symbol: str) -> StreamCapabilities:
    normalized = symbol.strip().lower()
    if not normalized or not normalized.isalnum():
        raise ValueError("symbol must be alphanumeric")
    if market_type is MarketType.SPOT:
        return StreamCapabilities(
            market_type=market_type,
            websocket_base_url="wss://stream.binance.com:9443/stream",
            aggregate_trade_stream=f"{normalized}@aggTrade",
            book_ticker_stream=f"{normalized}@bookTicker",
            diff_depth_stream=f"{normalized}@depth@100ms",
            mark_price_stream=None,
            depth_snapshot_url="https://api.binance.com",
            depth_snapshot_path="/api/v3/depth",
        )
    return StreamCapabilities(
        market_type=market_type,
        websocket_base_url="wss://fstream.binance.com/public/stream",
        aggregate_trade_stream=f"{normalized}@aggTrade",
        book_ticker_stream=f"{normalized}@bookTicker",
        diff_depth_stream=f"{normalized}@depth@100ms",
        mark_price_stream=f"{normalized}@markPrice@1s",
        depth_snapshot_url="https://fapi.binance.com",
        depth_snapshot_path="/fapi/v1/depth",
        market_websocket_base_url="wss://fstream.binance.com/market/stream",
        routed_connections_required=True,
    )


@dataclass(frozen=True, slots=True)
class ConnectionMetrics:
    connection_count: int = 0
    reconnect_count: int = 0
    snapshot_count: int = 0
    sequence_gap_count: int = 0
    resync_count: int = 0
    downtime_ms: Decimal = Decimal("0")


class ConnectionSupervisor:
    """Bounded exponential backoff; strategic timers never depend on it."""

    def __init__(
        self,
        *,
        maximum_reconnects: int = 5,
        base_backoff_ms: int = 100,
        maximum_backoff_ms: int = 5_000,
    ) -> None:
        if maximum_reconnects < 0 or base_backoff_ms <= 0 or maximum_backoff_ms <= 0:
            raise ValueError("connection recovery limits are invalid")
        if base_backoff_ms > maximum_backoff_ms:
            raise ValueError("base backoff cannot exceed maximum backoff")
        self.maximum_reconnects = maximum_reconnects
        self.base_backoff_ms = base_backoff_ms
        self.maximum_backoff_ms = maximum_backoff_ms
        self.metrics = ConnectionMetrics()
        self._disconnect_started_ns: int | None = None

    def connected(self, monotonic_ns: int) -> ConnectionMetrics:
        if monotonic_ns < 0:
            raise ValueError("monotonic timestamp must be non-negative")
        downtime = self.metrics.downtime_ms
        if self._disconnect_started_ns is not None:
            downtime += Decimal(monotonic_ns - self._disconnect_started_ns) / Decimal("1000000")
            self._disconnect_started_ns = None
        self.metrics = replace(
            self.metrics,
            connection_count=self.metrics.connection_count + 1,
            downtime_ms=downtime,
        )
        return self.metrics

    def disconnected(self, monotonic_ns: int) -> ConnectionMetrics:
        if monotonic_ns < 0:
            raise ValueError("monotonic timestamp must be non-negative")
        if self._disconnect_started_ns is None:
            self._disconnect_started_ns = monotonic_ns
        return self.metrics

    def reconnect_delay_ms(self, attempt: int) -> int:
        if attempt < 1 or attempt > self.maximum_reconnects:
            raise RuntimeError("bounded reconnect attempts exhausted")
        self.metrics = replace(
            self.metrics,
            reconnect_count=self.metrics.reconnect_count + 1,
        )
        delay = self.base_backoff_ms * (1 << (attempt - 1))
        return min(self.maximum_backoff_ms, delay)

    def snapshot_observed(self) -> None:
        self.metrics = replace(
            self.metrics,
            snapshot_count=self.metrics.snapshot_count + 1,
        )

    def sequence_gap_observed(self) -> None:
        self.metrics = replace(
            self.metrics,
            sequence_gap_count=self.metrics.sequence_gap_count + 1,
        )

    def resync_observed(self) -> None:
        self.metrics = replace(
            self.metrics,
            resync_count=self.metrics.resync_count + 1,
        )
