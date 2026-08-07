"""Integrity inspection for public USD-M Futures research datasets."""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from statistics import median

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.futures.models import FundingRate, FuturesCandle, MarkPriceCandle

_INTERVALS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
}


class FuturesGapPolicy(StrEnum):
    FAIL = "FAIL"
    WARN = "WARN"
    ALLOW_DOCUMENTED = "ALLOW_DOCUMENTED"


class ReadinessStatus(StrEnum):
    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    NOT_READY = "NOT_READY"


@dataclass(frozen=True, slots=True)
class CandleGap:
    previous_open_time: datetime
    next_open_time: datetime
    missing_candle_count: int
    documented: bool


@dataclass(frozen=True, slots=True)
class MarkAlignment:
    candle_open_time: datetime
    mark_open_time: datetime | None
    match_type: str
    alignment_delay_seconds: int | None
    future_match: bool = False


@dataclass(frozen=True, slots=True)
class CandleIntegrity:
    market_type: MarketType
    contract_type: ContractType
    symbol: str
    interval: str
    count: int
    first_open_time: datetime
    last_open_time: datetime
    strictly_increasing: bool
    duplicate_count: int
    gap_count: int
    missing_candle_count: int
    unexplained_gap_count: int
    all_timestamps_utc: bool
    all_ohlc_valid: bool
    all_volumes_non_negative: bool
    all_closed: bool
    spot_storage_collision_count: int
    gap_policy: FuturesGapPolicy
    content_hash: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MarkPriceIntegrity:
    symbol: str
    interval: str
    count: int
    first_open_time: datetime | None
    last_open_time: datetime | None
    strictly_increasing: bool
    duplicate_count: int
    gap_count: int
    all_timestamps_utc: bool
    all_prices_positive: bool
    all_closed: bool
    exact_match_count: int
    previous_match_count: int
    missing_count: int
    future_match_count: int
    maximum_alignment_delay_seconds: int | None
    coverage_percent: Decimal
    alignment_policy: str
    content_hash: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FundingIntegrity:
    symbol: str
    event_count: int
    first_event: datetime | None
    last_event: datetime | None
    strictly_increasing: bool
    duplicate_count: int
    multiple_events_same_timestamp: int
    all_timestamps_utc: bool
    positive_count: int
    negative_count: int
    zero_count: int
    minimum_rate: Decimal | None
    maximum_rate: Decimal | None
    mean_rate: Decimal | None
    median_rate: Decimal | None
    observed_median_interval_seconds: int | None
    largest_gap_seconds: int | None
    missing_windows: int
    coverage_percent: Decimal
    content_hash: str
    warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicDatasetIntegrity:
    candles: CandleIntegrity
    marks: MarkPriceIntegrity
    funding: FundingIntegrity
    candle_gaps: tuple[CandleGap, ...]
    mark_alignment: tuple[MarkAlignment, ...]
    futures_candle_hash: str
    mark_price_hash: str
    funding_hash: str
    combined_dataset_hash: str
    requested_start: datetime
    requested_end: datetime
    readiness: ReadinessStatus
    warnings: tuple[str, ...]


def _utc(value: datetime) -> bool:
    return value.utcoffset() == timedelta(0)


def _seconds(value: timedelta) -> int:
    return value.days * 86_400 + value.seconds


def _hash(value: object) -> str:
    material = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def futures_candle_content_hash(candles: tuple[FuturesCandle, ...]) -> str:
    return _hash(
        [
            {
                "exchange": item.exchange,
                "market_type": item.market_type.value,
                "contract_type": item.contract_type.value,
                "symbol": item.symbol,
                "interval": item.interval,
                "open_time": item.open_time.isoformat(),
                "close_time": item.close_time.isoformat(),
                "open": str(item.open),
                "high": str(item.high),
                "low": str(item.low),
                "close": str(item.close),
                "volume": str(item.volume),
                "quote_volume": str(item.quote_volume),
                "trade_count": item.trade_count,
                "is_closed": item.is_closed,
            }
            for item in sorted(candles, key=lambda value: value.open_time)
        ]
    )


def mark_price_content_hash(marks: tuple[MarkPriceCandle, ...]) -> str:
    return _hash(
        [
            {
                "symbol": item.symbol,
                "interval": item.interval,
                "open_time": item.open_time.isoformat(),
                "close_time": item.close_time.isoformat(),
                "open": str(item.open),
                "high": str(item.high),
                "low": str(item.low),
                "close": str(item.close),
                "index_price": str(item.index_price) if item.index_price is not None else None,
                "estimated_settle_price": (
                    str(item.estimated_settle_price)
                    if item.estimated_settle_price is not None
                    else None
                ),
                "is_closed": item.is_closed,
            }
            for item in sorted(marks, key=lambda value: value.open_time)
        ]
    )


def funding_content_hash(rates: tuple[FundingRate, ...]) -> str:
    return _hash(
        [
            {
                "symbol": item.symbol,
                "funding_time": item.funding_time.isoformat(),
                "funding_rate": str(item.funding_rate),
                "mark_price": str(item.mark_price) if item.mark_price is not None else None,
            }
            for item in sorted(rates, key=lambda value: value.funding_time)
        ]
    )


def inspect_candles(
    candles: tuple[FuturesCandle, ...],
    *,
    gap_policy: FuturesGapPolicy,
    documented_gap_starts: frozenset[datetime] = frozenset(),
) -> tuple[CandleIntegrity, tuple[CandleGap, ...]]:
    if not candles:
        raise ValueError("futures candle integrity requires candles")
    ordered = tuple(sorted(candles, key=lambda item: item.open_time))
    first = ordered[0]
    expected = _INTERVALS.get(first.interval)
    if expected is None:
        raise ValueError(f"unsupported futures interval: {first.interval}")
    gaps: list[CandleGap] = []
    for previous, current in zip(ordered, ordered[1:], strict=False):
        difference = current.open_time - previous.open_time
        if difference != expected:
            missing = max(0, _seconds(difference) // _seconds(expected) - 1)
            gaps.append(
                CandleGap(
                    previous_open_time=previous.open_time,
                    next_open_time=current.open_time,
                    missing_candle_count=missing,
                    documented=current.open_time in documented_gap_starts,
                )
            )
    times = tuple(item.open_time for item in ordered)
    duplicate_count = len(times) - len(set(times))
    unexplained = sum(not item.documented for item in gaps)
    warnings: list[str] = []
    if gaps:
        if gap_policy is FuturesGapPolicy.FAIL:
            warnings.append("FUTURES_CANDLE_GAPS_NOT_ALLOWED")
        elif gap_policy is FuturesGapPolicy.WARN:
            warnings.append("FUTURES_CANDLE_GAPS_WARN")
        elif unexplained:
            warnings.append("UNDOCUMENTED_FUTURES_CANDLE_GAPS")
        else:
            warnings.append("DOCUMENTED_FUTURES_CANDLE_GAPS")
    if duplicate_count:
        warnings.append("DUPLICATE_FUTURES_CANDLES")
    identities_valid = all(
        item.market_type is MarketType.USD_M_FUTURES
        and item.contract_type is ContractType.PERPETUAL
        and item.symbol == first.symbol
        and item.interval == first.interval
        for item in ordered
    )
    if not identities_valid:
        warnings.append("INVALID_FUTURES_CANDLE_IDENTITY")
    all_ohlc_valid = all(
        item.high >= max(item.open, item.close, item.low)
        and item.low <= min(item.open, item.close, item.high)
        for item in ordered
    )
    if not all_ohlc_valid:
        warnings.append("INVALID_FUTURES_OHLC")
    all_utc = all(_utc(item.open_time) and _utc(item.close_time) for item in ordered)
    if not all_utc:
        warnings.append("NON_UTC_FUTURES_CANDLE")
    all_closed = all(item.is_closed for item in ordered)
    if not all_closed:
        warnings.append("OPEN_FUTURES_CANDLE")
    all_volumes_valid = all(item.volume >= 0 and item.quote_volume >= 0 for item in ordered)
    if not all_volumes_valid:
        warnings.append("NEGATIVE_FUTURES_VOLUME")
    return (
        CandleIntegrity(
            market_type=first.market_type,
            contract_type=first.contract_type,
            symbol=first.symbol,
            interval=first.interval,
            count=len(ordered),
            first_open_time=first.open_time,
            last_open_time=ordered[-1].open_time,
            strictly_increasing=all(
                current > previous
                for previous, current in zip(times, times[1:], strict=False)
            ),
            duplicate_count=duplicate_count,
            gap_count=len(gaps),
            missing_candle_count=sum(item.missing_candle_count for item in gaps),
            unexplained_gap_count=unexplained,
            all_timestamps_utc=all_utc,
            all_ohlc_valid=all_ohlc_valid,
            all_volumes_non_negative=all_volumes_valid,
            all_closed=all_closed,
            spot_storage_collision_count=0,
            gap_policy=gap_policy,
            content_hash=futures_candle_content_hash(ordered),
            warnings=tuple(warnings),
        ),
        tuple(gaps),
    )


def align_mark_prices(
    candles: tuple[FuturesCandle, ...],
    marks: tuple[MarkPriceCandle, ...],
    *,
    maximum_previous_delay: timedelta | None = None,
) -> tuple[MarkAlignment, ...]:
    ordered_marks = tuple(sorted(marks, key=lambda item: item.open_time))
    mark_times = tuple(item.open_time for item in ordered_marks)
    exact_times = set(mark_times)
    allowed_delay = maximum_previous_delay or _INTERVALS[candles[0].interval]
    rows: list[MarkAlignment] = []
    for candle in sorted(candles, key=lambda item: item.open_time):
        if candle.open_time in exact_times:
            rows.append(
                MarkAlignment(
                    candle_open_time=candle.open_time,
                    mark_open_time=candle.open_time,
                    match_type="EXACT",
                    alignment_delay_seconds=0,
                )
            )
            continue
        index = bisect_right(mark_times, candle.open_time) - 1
        if index >= 0:
            found = ordered_marks[index]
            delay = candle.open_time - found.open_time
            if delay <= allowed_delay:
                rows.append(
                    MarkAlignment(
                        candle_open_time=candle.open_time,
                        mark_open_time=found.open_time,
                        match_type="PREVIOUS",
                        alignment_delay_seconds=_seconds(delay),
                    )
                )
                continue
        rows.append(
            MarkAlignment(
                candle_open_time=candle.open_time,
                mark_open_time=None,
                match_type="MISSING",
                alignment_delay_seconds=None,
            )
        )
    return tuple(rows)


def inspect_mark_prices(
    candles: tuple[FuturesCandle, ...],
    marks: tuple[MarkPriceCandle, ...],
) -> tuple[MarkPriceIntegrity, tuple[MarkAlignment, ...]]:
    ordered = tuple(sorted(marks, key=lambda item: item.open_time))
    alignment = align_mark_prices(candles, ordered)
    times = tuple(item.open_time for item in ordered)
    duplicate_count = len(times) - len(set(times))
    expected = _INTERVALS[candles[0].interval]
    gap_count = sum(
        current - previous != expected
        for previous, current in zip(times, times[1:], strict=False)
    )
    exact = sum(item.match_type == "EXACT" for item in alignment)
    previous = sum(item.match_type == "PREVIOUS" for item in alignment)
    missing = sum(item.match_type == "MISSING" for item in alignment)
    future = sum(item.future_match for item in alignment)
    warnings: list[str] = []
    if not ordered:
        warnings.append("MARK_PRICE_MISSING")
    if duplicate_count:
        warnings.append("DUPLICATE_MARK_PRICE_CANDLES")
    if gap_count:
        warnings.append("MARK_PRICE_GAPS")
    if previous:
        warnings.append("MARK_PRICE_PREVIOUS_ALIGNMENT_USED")
    if missing:
        warnings.append("MARK_PRICE_MISSING")
    if future:
        warnings.append("MARK_PRICE_FUTURE_ALIGNMENT")
    all_utc = all(_utc(item.open_time) and _utc(item.close_time) for item in ordered)
    all_positive = all(
        min(item.open, item.high, item.low, item.close) > 0 for item in ordered
    )
    all_closed = all(item.is_closed for item in ordered)
    identity_valid = all(
        item.symbol == candles[0].symbol and item.interval == candles[0].interval
        for item in ordered
    )
    if not all_utc:
        warnings.append("NON_UTC_MARK_PRICE")
    if not all_positive:
        warnings.append("NON_POSITIVE_MARK_PRICE")
    if not all_closed:
        warnings.append("OPEN_MARK_PRICE_CANDLE")
    if not identity_valid:
        warnings.append("INVALID_MARK_PRICE_IDENTITY")
    matched = exact + previous
    coverage = (
        Decimal(matched) / Decimal(len(candles)) * Decimal("100")
        if candles
        else Decimal("0")
    )
    delays = tuple(
        item.alignment_delay_seconds
        for item in alignment
        if item.alignment_delay_seconds is not None
    )
    return (
        MarkPriceIntegrity(
            symbol=candles[0].symbol,
            interval=candles[0].interval,
            count=len(ordered),
            first_open_time=ordered[0].open_time if ordered else None,
            last_open_time=ordered[-1].open_time if ordered else None,
            strictly_increasing=all(
                current > previous_time
                for previous_time, current in zip(times, times[1:], strict=False)
            ),
            duplicate_count=duplicate_count,
            gap_count=gap_count,
            all_timestamps_utc=all_utc,
            all_prices_positive=all_positive,
            all_closed=all_closed,
            exact_match_count=exact,
            previous_match_count=previous,
            missing_count=missing,
            future_match_count=future,
            maximum_alignment_delay_seconds=max(delays, default=None),
            coverage_percent=coverage,
            alignment_policy="SAME_OPEN_TIME_OR_LAST_KNOWN_PREVIOUS_MAX_ONE_INTERVAL",
            content_hash=mark_price_content_hash(ordered),
            warnings=tuple(dict.fromkeys(warnings)),
        ),
        alignment,
    )


def inspect_funding(
    rates: tuple[FundingRate, ...],
    *,
    symbol: str,
    requested_start: datetime,
    requested_end: datetime,
) -> FundingIntegrity:
    ordered = tuple(sorted(rates, key=lambda item: item.funding_time))
    times = tuple(item.funding_time for item in ordered)
    duplicate_count = len(times) - len(set(times))
    deltas = tuple(
        _seconds(current - previous)
        for previous, current in zip(times, times[1:], strict=False)
    )
    observed_interval = int(median(deltas)) if deltas else None
    missing_windows = 0
    if observed_interval:
        missing_windows = sum(
            max(0, round(gap / observed_interval) - 1)
            for gap in deltas
            if gap > observed_interval * 3 // 2
        )
    rates_only = tuple(item.funding_rate for item in ordered)
    warnings: list[str] = []
    if not ordered:
        warnings.append("FUNDING_DATA_MISSING")
    if duplicate_count:
        warnings.append("DUPLICATE_FUNDING_EVENTS")
    if missing_windows:
        warnings.append("FUNDING_WINDOWS_MISSING")
    if any(item.symbol != symbol for item in ordered):
        warnings.append("INVALID_FUNDING_SYMBOL")
    all_utc = all(_utc(item.funding_time) for item in ordered)
    if not all_utc:
        warnings.append("NON_UTC_FUNDING_EVENT")
    expected_events = 0
    if observed_interval:
        span_seconds = _seconds(requested_end - requested_start)
        expected_events = span_seconds // observed_interval + 1
    coverage = (
        min(
            Decimal("100"),
            Decimal(len(ordered)) / Decimal(expected_events) * Decimal("100"),
        )
        if expected_events
        else Decimal("0")
    )
    return FundingIntegrity(
        symbol=symbol,
        event_count=len(ordered),
        first_event=ordered[0].funding_time if ordered else None,
        last_event=ordered[-1].funding_time if ordered else None,
        strictly_increasing=all(
            current > previous
            for previous, current in zip(times, times[1:], strict=False)
        ),
        duplicate_count=duplicate_count,
        multiple_events_same_timestamp=duplicate_count,
        all_timestamps_utc=all_utc,
        positive_count=sum(item > 0 for item in rates_only),
        negative_count=sum(item < 0 for item in rates_only),
        zero_count=sum(item == 0 for item in rates_only),
        minimum_rate=min(rates_only, default=None),
        maximum_rate=max(rates_only, default=None),
        mean_rate=(
            sum(rates_only, Decimal("0")) / Decimal(len(rates_only))
            if rates_only
            else None
        ),
        median_rate=median(rates_only) if rates_only else None,
        observed_median_interval_seconds=observed_interval,
        largest_gap_seconds=max(deltas, default=None),
        missing_windows=missing_windows,
        coverage_percent=coverage,
        content_hash=funding_content_hash(ordered),
        warnings=tuple(warnings),
    )


def inspect_public_dataset(
    candles: tuple[FuturesCandle, ...],
    marks: tuple[MarkPriceCandle, ...],
    funding: tuple[FundingRate, ...],
    *,
    requested_start: datetime,
    requested_end: datetime,
    gap_policy: FuturesGapPolicy = FuturesGapPolicy.WARN,
    documented_gap_starts: frozenset[datetime] = frozenset(),
) -> PublicDatasetIntegrity:
    if requested_end < requested_start:
        raise ValueError("requested end must not precede start")
    candle_integrity, gaps = inspect_candles(
        candles,
        gap_policy=gap_policy,
        documented_gap_starts=documented_gap_starts,
    )
    mark_integrity, alignment = inspect_mark_prices(candles, marks)
    funding_integrity = inspect_funding(
        funding,
        symbol=candle_integrity.symbol,
        requested_start=requested_start,
        requested_end=requested_end,
    )
    combined = _hash(
        {
            "market_type": candle_integrity.market_type.value,
            "contract_type": candle_integrity.contract_type.value,
            "symbol": candle_integrity.symbol,
            "interval": candle_integrity.interval,
            "requested_start": requested_start.isoformat(),
            "requested_end": requested_end.isoformat(),
            "futures_candle_hash": candle_integrity.content_hash,
            "mark_price_hash": mark_integrity.content_hash,
            "funding_hash": funding_integrity.content_hash,
        }
    )
    hard_failures = (
        candle_integrity.market_type is not MarketType.USD_M_FUTURES
        or candle_integrity.contract_type is not ContractType.PERPETUAL
        or not candle_integrity.strictly_increasing
        or candle_integrity.duplicate_count > 0
        or not candle_integrity.all_timestamps_utc
        or not candle_integrity.all_ohlc_valid
        or not candle_integrity.all_volumes_non_negative
        or not candle_integrity.all_closed
        or (
            candle_integrity.gap_count > 0
            and gap_policy is FuturesGapPolicy.FAIL
        )
        or (
            gap_policy is FuturesGapPolicy.ALLOW_DOCUMENTED
            and candle_integrity.unexplained_gap_count > 0
        )
        or mark_integrity.missing_count > 0
        or mark_integrity.future_match_count > 0
        or mark_integrity.duplicate_count > 0
        or not mark_integrity.all_timestamps_utc
        or not mark_integrity.all_prices_positive
        or not mark_integrity.all_closed
        or funding_integrity.event_count == 0
        or funding_integrity.duplicate_count > 0
        or not funding_integrity.all_timestamps_utc
        or funding_integrity.missing_windows > 0
    )
    warnings = tuple(
        dict.fromkeys(
            (
                *candle_integrity.warnings,
                *mark_integrity.warnings,
                *funding_integrity.warnings,
            )
        )
    )
    readiness = (
        ReadinessStatus.NOT_READY
        if hard_failures
        else ReadinessStatus.READY_WITH_WARNINGS
        if warnings
        else ReadinessStatus.READY
    )
    return PublicDatasetIntegrity(
        candles=candle_integrity,
        marks=mark_integrity,
        funding=funding_integrity,
        candle_gaps=gaps,
        mark_alignment=alignment,
        futures_candle_hash=candle_integrity.content_hash,
        mark_price_hash=mark_integrity.content_hash,
        funding_hash=funding_integrity.content_hash,
        combined_dataset_hash=combined,
        requested_start=requested_start,
        requested_end=requested_end,
        readiness=readiness,
        warnings=warnings,
    )
