"""Typed routing for the documented Binance USD-M public market streams."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FuturesStreamRoute(StrEnum):
    PUBLIC = "PUBLIC"
    MARKET = "MARKET"


@dataclass(frozen=True, slots=True)
class RoutedStream:
    requested_stream: str
    stream_name: str
    route: FuturesStreamRoute


@dataclass(frozen=True, slots=True)
class FuturesConnectionPlan:
    connection_id: str
    route: FuturesStreamRoute
    url: str
    streams: tuple[RoutedStream, ...]


class FuturesStreamRouter:
    """Reject unrouted/private USD-M subscriptions and build combined public URLs."""

    PUBLIC_BASE_URL = "wss://fstream.binance.com/public"
    MARKET_BASE_URL = "wss://fstream.binance.com/market"
    LEGACY_BASE_URL = "wss://fstream.binance.com/stream"
    OBSERVED_ON = "2026-08-07"
    OFFICIAL_NOTICE_URL = (
        "https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/"
        "websocket-market-streams/Important-WebSocket-Change-Notice"
    )
    OFFICIAL_CONNECT_URL = (
        "https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/"
        "websocket-market-streams/Connect"
    )
    OFFICIAL_BOOK_URL = (
        "https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/"
        "websocket-market-streams/How-to-manage-a-local-order-book-correctly"
    )

    _ROUTES = {
        "bookTicker": FuturesStreamRoute.PUBLIC,
        "depth": FuturesStreamRoute.PUBLIC,
        "depth@100ms": FuturesStreamRoute.PUBLIC,
        "aggTrade": FuturesStreamRoute.MARKET,
        "markPrice": FuturesStreamRoute.MARKET,
        "markPrice@1s": FuturesStreamRoute.MARKET,
    }
    _CANONICAL = {
        "bookTicker": "bookTicker",
        "depth": "depth@100ms",
        "depth@100ms": "depth@100ms",
        "aggTrade": "aggTrade",
        "markPrice": "markPrice@1s",
        "markPrice@1s": "markPrice@1s",
    }

    def route(self, requested_stream: str, symbol: str) -> RoutedStream:
        normalized_symbol = self._symbol(symbol)
        if requested_stream not in self._ROUTES:
            lowered = requested_stream.lower()
            if any(marker in lowered for marker in ("private", "listenkey", "user_data")):
                raise ValueError("private Futures streams are forbidden")
            raise ValueError(f"unsupported Futures public stream: {requested_stream}")
        suffix = self._CANONICAL[requested_stream]
        return RoutedStream(
            requested_stream=requested_stream,
            stream_name=f"{normalized_symbol}@{suffix}",
            route=self._ROUTES[requested_stream],
        )

    def plans(
        self,
        symbol: str,
        requested_streams: tuple[str, ...],
    ) -> tuple[FuturesConnectionPlan, ...]:
        if not requested_streams:
            raise ValueError("at least one Futures public stream is required")
        routed = tuple(self.route(item, symbol) for item in requested_streams)
        if len({item.stream_name for item in routed}) != len(routed):
            raise ValueError("duplicate Futures stream subscription")
        plans: list[FuturesConnectionPlan] = []
        for route in (FuturesStreamRoute.PUBLIC, FuturesStreamRoute.MARKET):
            streams = tuple(item for item in routed if item.route is route)
            if not streams:
                continue
            base_url = (
                self.PUBLIC_BASE_URL
                if route is FuturesStreamRoute.PUBLIC
                else self.MARKET_BASE_URL
            )
            names = "/".join(item.stream_name for item in streams)
            plans.append(
                FuturesConnectionPlan(
                    connection_id=f"futures-{route.value.lower()}-1",
                    route=route,
                    url=f"{base_url}/stream?streams={names}",
                    streams=streams,
                )
            )
        return tuple(plans)

    def validate_url(self, plan: FuturesConnectionPlan) -> None:
        if plan.url.startswith(self.LEGACY_BASE_URL):
            raise ValueError("legacy unrouted Futures WebSocket URLs are forbidden")
        expected_base = (
            self.PUBLIC_BASE_URL
            if plan.route is FuturesStreamRoute.PUBLIC
            else self.MARKET_BASE_URL
        )
        if not plan.url.startswith(f"{expected_base}/"):
            raise ValueError("Futures connection uses the wrong routed endpoint")
        if any(item.route is not plan.route for item in plan.streams):
            raise ValueError("Futures stream is assigned to the wrong routed endpoint")

    def official_mapping(self) -> dict[str, object]:
        return {
            "observed_on": self.OBSERVED_ON,
            "public_base_url": self.PUBLIC_BASE_URL,
            "market_base_url": self.MARKET_BASE_URL,
            "private_route_used": False,
            "legacy_route_allowed": False,
            "stream_mapping": {
                name: {
                    "route": route.value,
                    "canonical_suffix": self._CANONICAL[name],
                }
                for name, route in self._ROUTES.items()
            },
            "official_sources": (
                self.OFFICIAL_NOTICE_URL,
                self.OFFICIAL_CONNECT_URL,
                self.OFFICIAL_BOOK_URL,
            ),
        }

    @staticmethod
    def _symbol(symbol: str) -> str:
        normalized = symbol.strip().lower()
        if not normalized or not normalized.isalnum():
            raise ValueError("symbol must be alphanumeric")
        return normalized
