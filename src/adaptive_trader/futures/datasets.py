"""Validation and reproducible hashing for Futures research datasets."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.domain.models import serialize_model
from adaptive_trader.futures.models import (
    FundingMissingPolicy,
    FundingRate,
    FuturesCandle,
    FuturesPriceSource,
    MarkPriceCandle,
)

_INTERVALS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


@dataclass(frozen=True, slots=True)
class FuturesDataset:
    dataset_id: str
    source: str
    market_type: MarketType
    contract_type: ContractType
    symbol: str
    interval: str
    candles: tuple[FuturesCandle, ...]
    mark_prices: tuple[MarkPriceCandle, ...]
    funding_rates: tuple[FundingRate, ...]
    candle_hash: str
    mark_price_hash: str
    funding_hash: str
    combined_dataset_hash: str
    duplicate_count: int
    gap_count: int
    mark_price_missing_count: int
    funding_gap_count: int
    warnings: tuple[str, ...]
    price_source: FuturesPriceSource

    @property
    def valid_for_research(self) -> bool:
        return (
            self.price_source is not FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY
            and self.duplicate_count == 0
            and self.gap_count == 0
            and self.mark_price_missing_count == 0
            and all(item.is_closed for item in self.candles)
        )


def _content_hash(value: object) -> str:
    material = json.dumps(
        serialize_model({"value": value}),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def validate_futures_dataset(
    candles: tuple[FuturesCandle, ...],
    mark_prices: tuple[MarkPriceCandle, ...],
    funding_rates: tuple[FundingRate, ...],
    *,
    source: str,
    funding_enabled: bool = True,
    funding_missing_policy: FundingMissingPolicy = FundingMissingPolicy.FAIL,
    price_source: FuturesPriceSource = FuturesPriceSource.FUTURES_KLINE,
) -> FuturesDataset:
    if not candles:
        raise ValueError("futures dataset requires candles")
    first = candles[0]
    if first.interval not in _INTERVALS:
        raise ValueError("unsupported futures interval")
    if any(
        item.symbol != first.symbol
        or item.interval != first.interval
        or item.market_type is not MarketType.USD_M_FUTURES
        or item.contract_type is not ContractType.PERPETUAL
        for item in candles
    ):
        raise ValueError("mixed futures candle identity")
    candle_times = tuple(item.open_time for item in candles)
    duplicate_count = len(candle_times) - len(set(candle_times))
    ordered = tuple(sorted(candles, key=lambda item: item.open_time))
    expected = _INTERVALS[first.interval]
    gap_count = sum(
        current.open_time - previous.open_time != expected
        for previous, current in zip(ordered, ordered[1:], strict=False)
    )
    if duplicate_count:
        raise ValueError("duplicate futures candles")
    if any(not item.is_closed for item in ordered):
        raise ValueError("futures dataset contains open candles")
    if any(not item.is_closed for item in mark_prices):
        raise ValueError("futures dataset contains open mark-price candles")
    if len(mark_prices) != len({item.open_time for item in mark_prices}):
        raise ValueError("duplicate mark-price candles")
    if len(funding_rates) != len({item.funding_time for item in funding_rates}):
        raise ValueError("duplicate funding events")
    mark_times = {item.open_time for item in mark_prices}
    missing_marks = sum(item.open_time not in mark_times for item in ordered)
    warnings: list[str] = []
    if gap_count:
        warnings.append("FUTURES_CANDLE_GAPS")
    if missing_marks:
        warnings.append("MARK_PRICE_MISSING")
        if price_source is not FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY:
            raise ValueError("MARK_PRICE_MISSING")
    if price_source is FuturesPriceSource.SPOT_PROXY_FOR_TESTS_ONLY:
        warnings.extend(("SPOT_PROXY_FOR_TESTS_ONLY", "REPORT_INVALID_PRICE_PROXY"))
    funding_gaps = 0
    if funding_enabled:
        if not funding_rates:
            funding_gaps = 1
        else:
            boundaries = (
                ordered[0].open_time,
                *(item.funding_time for item in sorted(
                    funding_rates,
                    key=lambda item: item.funding_time,
                )),
                ordered[-1].close_time,
            )
            funding_gaps = sum(
                current - previous > timedelta(hours=8, minutes=1)
                for previous, current in zip(boundaries, boundaries[1:], strict=False)
            )
        if funding_gaps:
            if funding_missing_policy is FundingMissingPolicy.FAIL:
                raise ValueError("FUNDING_DATA_MISSING")
            warnings.append("FUNDING_DATA_MISSING")
    if (
        not funding_enabled
        and funding_missing_policy is not FundingMissingPolicy.DISABLE_EXPLICITLY
    ):
        raise ValueError("disabled funding must use DISABLE_EXPLICITLY")
    if any(item.symbol != first.symbol for item in mark_prices):
        raise ValueError("mixed mark price symbols")
    if any(item.symbol != first.symbol for item in funding_rates):
        raise ValueError("mixed funding symbols")
    candle_hash = _content_hash(ordered)
    mark_hash = _content_hash(tuple(sorted(mark_prices, key=lambda item: item.open_time)))
    funding_hash = _content_hash(
        tuple(sorted(funding_rates, key=lambda item: item.funding_time))
    )
    combined = _content_hash(
        {
            "market_type": MarketType.USD_M_FUTURES,
            "contract_type": ContractType.PERPETUAL,
            "symbol": first.symbol,
            "interval": first.interval,
            "source": source,
            "candle_hash": candle_hash,
            "mark_price_hash": mark_hash,
            "funding_hash": funding_hash,
        }
    )
    return FuturesDataset(
        dataset_id=f"futures-{combined[:16]}",
        source=source,
        market_type=MarketType.USD_M_FUTURES,
        contract_type=ContractType.PERPETUAL,
        symbol=first.symbol,
        interval=first.interval,
        candles=ordered,
        mark_prices=tuple(sorted(mark_prices, key=lambda item: item.open_time)),
        funding_rates=tuple(sorted(funding_rates, key=lambda item: item.funding_time)),
        candle_hash=candle_hash,
        mark_price_hash=mark_hash,
        funding_hash=funding_hash,
        combined_dataset_hash=combined,
        duplicate_count=duplicate_count,
        gap_count=gap_count,
        mark_price_missing_count=missing_marks,
        funding_gap_count=funding_gaps,
        warnings=tuple(dict.fromkeys(warnings)),
        price_source=price_source,
    )
