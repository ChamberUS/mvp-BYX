"""Deterministic UTC aggregation of persisted hourly candles into daily candles."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import overload

from adaptive_trader.domain.market import MarketType
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.models import FuturesCandle
from adaptive_trader.research.datasets import canonical_hash

_HOUR = timedelta(hours=1)
_EXPECTED_HOURS = 24


class DailyAggregationError(ValueError):
    """Raised when hourly inputs cannot be aggregated under the selected policy."""


class IncompleteDayPolicy(StrEnum):
    FAIL = "FAIL"
    WARN_AND_EXCLUDE = "WARN_AND_EXCLUDE"
    ALLOW_DOCUMENTED = "ALLOW_DOCUMENTED"


class DailyAggregationAction(StrEnum):
    INCLUDED = "INCLUDED"
    EXCLUDED = "EXCLUDED"
    INCLUDED_DOCUMENTED_INCOMPLETE = "INCLUDED_DOCUMENTED_INCOMPLETE"


@dataclass(frozen=True, slots=True)
class DailyAggregationConfig:
    """Fixed, hashable rules for the canonical 1h-to-1d UTC transformation."""

    incomplete_day_policy: IncompleteDayPolicy = IncompleteDayPolicy.WARN_AND_EXCLUDE
    source_interval: str = "1h"
    target_interval: str = "1d"
    timezone: str = "UTC"
    expected_candles_per_day: int = _EXPECTED_HOURS
    version: str = "daily-candle-aggregation-v1"

    def __post_init__(self) -> None:
        if self.source_interval != "1h":
            raise ValueError("daily aggregation source interval must be 1h")
        if self.target_interval != "1d":
            raise ValueError("daily aggregation target interval must be 1d")
        if self.timezone != "UTC":
            raise ValueError("daily aggregation timezone must be UTC")
        if self.expected_candles_per_day != _EXPECTED_HOURS:
            raise ValueError("daily aggregation requires exactly 24 hourly slots")
        if not self.version:
            raise ValueError("daily aggregation version is required")


@dataclass(frozen=True, slots=True)
class DailyAggregationAudit:
    day_start: datetime
    observed_candle_count: int
    expected_candle_count: int
    missing_open_times: tuple[datetime, ...]
    open_source_candle_count: int
    invalid_close_time_count: int
    complete: bool
    documented: bool
    action: DailyAggregationAction
    source_hourly_hash: str
    issues: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.day_start.tzinfo is None or self.day_start.utcoffset() != timedelta(0):
            raise ValueError("daily audit day_start must be UTC")
        if min(
            self.observed_candle_count,
            self.expected_candle_count,
            self.open_source_candle_count,
            self.invalid_close_time_count,
        ) < 0:
            raise ValueError("daily audit counters must not be negative")
        if self.complete and self.action is not DailyAggregationAction.INCLUDED:
            raise ValueError("complete daily audit must be included normally")
        if (
            self.action is DailyAggregationAction.INCLUDED_DOCUMENTED_INCOMPLETE
            and (self.complete or not self.documented)
        ):
            raise ValueError(
                "documented incomplete inclusion requires an incomplete documented day"
            )


@dataclass(frozen=True, slots=True)
class DailyAggregationIntegrity:
    source_candle_count: int
    source_day_count: int
    output_candle_count: int
    complete_day_count: int
    incomplete_day_count: int
    excluded_day_count: int
    documented_incomplete_day_count: int
    input_strictly_increasing: bool
    duplicate_open_time_count: int
    all_source_timestamps_utc: bool
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        counters = (
            self.source_candle_count,
            self.source_day_count,
            self.output_candle_count,
            self.complete_day_count,
            self.incomplete_day_count,
            self.excluded_day_count,
            self.documented_incomplete_day_count,
            self.duplicate_open_time_count,
        )
        if min(counters) < 0:
            raise ValueError("daily integrity counters must not be negative")
        if self.complete_day_count + self.incomplete_day_count != self.source_day_count:
            raise ValueError("daily integrity day counters are inconsistent")
        if self.output_candle_count + self.excluded_day_count != self.source_day_count:
            raise ValueError("daily integrity output counters are inconsistent")


@dataclass(frozen=True, slots=True)
class DailyAggregationResult[DailyCandle: (Candle, FuturesCandle)]:
    market_type: MarketType
    candles: tuple[DailyCandle, ...]
    audits: tuple[DailyAggregationAudit, ...]
    integrity: DailyAggregationIntegrity
    source_hourly_hash: str
    aggregation_config_hash: str
    daily_rows_hash: str
    daily_candle_hash: str

    @property
    def content_hash(self) -> str:
        """Alias used by research manifests for the complete derived-data hash."""

        return self.daily_candle_hash


type _SourceCandle = Candle | FuturesCandle


class DailyCandleAggregator:
    """Build canonical daily candles without downloading or fabricating hourly data."""

    def __init__(
        self,
        config: DailyAggregationConfig | None = None,
        *,
        policy: IncompleteDayPolicy | None = None,
    ) -> None:
        if config is not None and policy is not None:
            raise ValueError("provide either daily aggregation config or policy, not both")
        self.config = config or DailyAggregationConfig(
            incomplete_day_policy=policy or IncompleteDayPolicy.WARN_AND_EXCLUDE
        )

    @overload
    def aggregate(
        self,
        candles: tuple[Candle, ...],
        *,
        documented_incomplete_days: frozenset[date] = frozenset(),
    ) -> DailyAggregationResult[Candle]: ...

    @overload
    def aggregate(
        self,
        candles: tuple[FuturesCandle, ...],
        *,
        documented_incomplete_days: frozenset[date] = frozenset(),
    ) -> DailyAggregationResult[FuturesCandle]: ...

    def aggregate(
        self,
        candles: tuple[Candle, ...] | tuple[FuturesCandle, ...],
        *,
        documented_incomplete_days: frozenset[date] = frozenset(),
    ) -> DailyAggregationResult[Candle] | DailyAggregationResult[FuturesCandle]:
        if not candles:
            raise DailyAggregationError("daily aggregation requires hourly candles")
        if isinstance(candles[0], Candle):
            if any(not isinstance(item, Candle) for item in candles):
                raise DailyAggregationError("daily aggregation rejects mixed candle types")
            return self.aggregate_spot(
                tuple(item for item in candles if isinstance(item, Candle)),
                documented_incomplete_days=documented_incomplete_days,
            )
        if any(not isinstance(item, FuturesCandle) for item in candles):
            raise DailyAggregationError("daily aggregation rejects mixed candle types")
        return self.aggregate_futures(
            tuple(item for item in candles if isinstance(item, FuturesCandle)),
            documented_incomplete_days=documented_incomplete_days,
        )

    def aggregate_spot(
        self,
        candles: Sequence[Candle],
        *,
        documented_incomplete_days: frozenset[date] = frozenset(),
    ) -> DailyAggregationResult[Candle]:
        source = tuple(candles)
        ordered, input_increasing = _validate_and_order(source, MarketType.SPOT)
        _validate_spot_identity(ordered)
        groups = _group_by_utc_day(ordered)
        daily: list[Candle] = []
        audits: list[DailyAggregationAudit] = []
        warnings: list[str] = []
        for day_start, group in groups:
            audit = self._audit_day(
                day_start,
                group,
                documented_incomplete_days=documented_incomplete_days,
            )
            audits.append(audit)
            if audit.issues:
                warnings.append(_warning(audit))
            if audit.action is not DailyAggregationAction.EXCLUDED:
                daily.append(_aggregate_spot_day(day_start, group, closed=audit.complete))
        return self._result(
            market_type=MarketType.SPOT,
            source=ordered,
            daily=tuple(daily),
            audits=tuple(audits),
            documented_incomplete_days=documented_incomplete_days,
            input_increasing=input_increasing,
            warnings=tuple(warnings),
        )

    def aggregate_futures(
        self,
        candles: Sequence[FuturesCandle],
        *,
        documented_incomplete_days: frozenset[date] = frozenset(),
    ) -> DailyAggregationResult[FuturesCandle]:
        source = tuple(candles)
        ordered, input_increasing = _validate_and_order(source, MarketType.USD_M_FUTURES)
        _validate_futures_identity(ordered)
        groups = _group_by_utc_day(ordered)
        daily: list[FuturesCandle] = []
        audits: list[DailyAggregationAudit] = []
        warnings: list[str] = []
        for day_start, group in groups:
            audit = self._audit_day(
                day_start,
                group,
                documented_incomplete_days=documented_incomplete_days,
            )
            audits.append(audit)
            if audit.issues:
                warnings.append(_warning(audit))
            if audit.action is not DailyAggregationAction.EXCLUDED:
                daily.append(_aggregate_futures_day(day_start, group, closed=audit.complete))
        return self._result(
            market_type=MarketType.USD_M_FUTURES,
            source=ordered,
            daily=tuple(daily),
            audits=tuple(audits),
            documented_incomplete_days=documented_incomplete_days,
            input_increasing=input_increasing,
            warnings=tuple(warnings),
        )

    def _audit_day(
        self,
        day_start: datetime,
        source: tuple[_SourceCandle, ...],
        *,
        documented_incomplete_days: frozenset[date],
    ) -> DailyAggregationAudit:
        expected = tuple(day_start + timedelta(hours=hour) for hour in range(_EXPECTED_HOURS))
        observed = {item.open_time.astimezone(UTC) for item in source}
        missing = tuple(item for item in expected if item not in observed)
        open_count = sum(not item.is_closed for item in source)
        invalid_close_count = sum(not _valid_hour_close(item) for item in source)
        complete = (
            len(source) == self.config.expected_candles_per_day
            and not missing
            and open_count == 0
            and invalid_close_count == 0
        )
        documented = day_start.date() in documented_incomplete_days
        issues: list[str] = []
        if missing:
            issues.append("MISSING_HOURLY_CANDLES")
        if open_count:
            issues.append("OPEN_HOURLY_CANDLES")
        if invalid_close_count:
            issues.append("INVALID_HOURLY_CLOSE_TIME")
        if len(source) != self.config.expected_candles_per_day and not missing:
            issues.append("UNEXPECTED_HOURLY_CANDLE_COUNT")
        if complete:
            action = DailyAggregationAction.INCLUDED
        elif self.config.incomplete_day_policy is IncompleteDayPolicy.FAIL:
            raise DailyAggregationError(
                f"incomplete UTC day {day_start.date().isoformat()}: {','.join(issues)}"
            )
        elif self.config.incomplete_day_policy is IncompleteDayPolicy.WARN_AND_EXCLUDE:
            action = DailyAggregationAction.EXCLUDED
        elif not documented:
            raise DailyAggregationError(
                "ALLOW_DOCUMENTED requires the incomplete UTC day to be explicitly "
                f"documented: {day_start.date().isoformat()}"
            )
        else:
            action = DailyAggregationAction.INCLUDED_DOCUMENTED_INCOMPLETE
        return DailyAggregationAudit(
            day_start=day_start,
            observed_candle_count=len(source),
            expected_candle_count=self.config.expected_candles_per_day,
            missing_open_times=missing,
            open_source_candle_count=open_count,
            invalid_close_time_count=invalid_close_count,
            complete=complete,
            documented=documented,
            action=action,
            source_hourly_hash=canonical_hash(
                [_canonical_source_candle(item) for item in source]
            ),
            issues=tuple(issues),
        )

    def _result[ResultCandle: (Candle, FuturesCandle)](
        self,
        *,
        market_type: MarketType,
        source: tuple[_SourceCandle, ...],
        daily: tuple[ResultCandle, ...],
        audits: tuple[DailyAggregationAudit, ...],
        documented_incomplete_days: frozenset[date],
        input_increasing: bool,
        warnings: tuple[str, ...],
    ) -> DailyAggregationResult[ResultCandle]:
        source_rows = [_canonical_source_candle(item) for item in source]
        daily_rows = [_canonical_source_candle(item) for item in daily]
        config_payload = {
            "version": self.config.version,
            "source_interval": self.config.source_interval,
            "target_interval": self.config.target_interval,
            "timezone": self.config.timezone,
            "expected_candles_per_day": self.config.expected_candles_per_day,
            "incomplete_day_policy": self.config.incomplete_day_policy,
            "documented_incomplete_days": sorted(
                item.isoformat() for item in documented_incomplete_days
            ),
        }
        source_hash = canonical_hash(
            {"market_type": market_type, "hourly_rows": source_rows}
        )
        config_hash = canonical_hash(config_payload)
        rows_hash = canonical_hash({"market_type": market_type, "daily_rows": daily_rows})
        audit_rows = [_canonical_audit(item) for item in audits]
        combined_hash = canonical_hash(
            {
                "market_type": market_type,
                "source_hourly_hash": source_hash,
                "aggregation_config_hash": config_hash,
                "daily_rows": daily_rows,
                "daily_audits": audit_rows,
            }
        )
        complete_count = sum(item.complete for item in audits)
        excluded_count = sum(
            item.action is DailyAggregationAction.EXCLUDED for item in audits
        )
        documented_count = sum(
            item.action is DailyAggregationAction.INCLUDED_DOCUMENTED_INCOMPLETE
            for item in audits
        )
        integrity = DailyAggregationIntegrity(
            source_candle_count=len(source),
            source_day_count=len(audits),
            output_candle_count=len(daily),
            complete_day_count=complete_count,
            incomplete_day_count=len(audits) - complete_count,
            excluded_day_count=excluded_count,
            documented_incomplete_day_count=documented_count,
            input_strictly_increasing=input_increasing,
            duplicate_open_time_count=0,
            all_source_timestamps_utc=True,
            warnings=warnings,
        )
        return DailyAggregationResult(
            market_type=market_type,
            candles=daily,
            audits=audits,
            integrity=integrity,
            source_hourly_hash=source_hash,
            aggregation_config_hash=config_hash,
            daily_rows_hash=rows_hash,
            daily_candle_hash=combined_hash,
        )


def _validate_and_order[ValidatedCandle: (Candle, FuturesCandle)](
    candles: tuple[ValidatedCandle, ...],
    market_type: MarketType,
) -> tuple[tuple[ValidatedCandle, ...], bool]:
    if not candles:
        raise DailyAggregationError("daily aggregation requires hourly candles")
    if any(item.interval != "1h" for item in candles):
        raise DailyAggregationError("daily aggregation accepts only 1h source candles")
    for item in candles:
        _require_utc_hour(item.open_time, "open_time")
        if item.close_time is not None:
            _require_utc(item.close_time, "close_time")
    input_increasing = all(
        current.open_time > previous.open_time
        for previous, current in zip(candles, candles[1:], strict=False)
    )
    ordered = tuple(sorted(candles, key=lambda item: item.open_time))
    times = tuple(item.open_time.astimezone(UTC) for item in ordered)
    duplicate_count = len(times) - len(set(times))
    if duplicate_count:
        raise DailyAggregationError(
            f"daily aggregation rejects duplicate open times: {duplicate_count}"
        )
    if market_type is MarketType.SPOT and any(not isinstance(item, Candle) for item in ordered):
        raise DailyAggregationError("Spot daily aggregation requires Candle inputs")
    if market_type is MarketType.USD_M_FUTURES and any(
        not isinstance(item, FuturesCandle) for item in ordered
    ):
        raise DailyAggregationError("Futures daily aggregation requires FuturesCandle inputs")
    return ordered, input_increasing


def _validate_spot_identity(candles: tuple[Candle, ...]) -> None:
    first = candles[0]
    if any(
        item.exchange != first.exchange or item.symbol != first.symbol for item in candles
    ):
        raise DailyAggregationError("Spot daily aggregation rejects mixed candle identity")


def _validate_futures_identity(candles: tuple[FuturesCandle, ...]) -> None:
    first = candles[0]
    if any(
        item.exchange != first.exchange
        or item.market_type is not first.market_type
        or item.contract_type is not first.contract_type
        or item.symbol != first.symbol
        for item in candles
    ):
        raise DailyAggregationError("Futures daily aggregation rejects mixed candle identity")


def _group_by_utc_day[GroupedCandle: (Candle, FuturesCandle)](
    candles: tuple[GroupedCandle, ...],
) -> tuple[tuple[datetime, tuple[GroupedCandle, ...]], ...]:
    grouped: defaultdict[date, list[GroupedCandle]] = defaultdict(list)
    for candle in candles:
        grouped[candle.open_time.astimezone(UTC).date()].append(candle)
    return tuple(
        (
            datetime(day.year, day.month, day.day, tzinfo=UTC),
            tuple(grouped[day]),
        )
        for day in sorted(grouped)
    )


def _valid_hour_close(candle: _SourceCandle) -> bool:
    if candle.close_time is None:
        return False
    open_time = candle.open_time.astimezone(UTC)
    close_time = candle.close_time.astimezone(UTC)
    return open_time <= close_time <= open_time + _HOUR


def _aggregate_spot_day(
    day_start: datetime,
    source: tuple[Candle, ...],
    *,
    closed: bool,
) -> Candle:
    first = source[0]
    last = source[-1]
    return Candle(
        exchange=first.exchange,
        symbol=first.symbol,
        interval="1d",
        timestamp=day_start,
        close_time=last.close_time,
        open=first.open,
        high=max(item.high for item in source),
        low=min(item.low for item in source),
        close=last.close,
        volume=sum((item.volume for item in source), Decimal("0")),
        quote_volume=_sum_optional_decimals(tuple(item.quote_volume for item in source)),
        trades_count=_sum_optional_ints(tuple(item.trades_count for item in source)),
        taker_buy_base_volume=_sum_optional_decimals(
            tuple(item.taker_buy_base_volume for item in source)
        ),
        taker_buy_quote_volume=_sum_optional_decimals(
            tuple(item.taker_buy_quote_volume for item in source)
        ),
        is_closed=closed,
        collected_at=_latest_collected_at(source),
    )


def _aggregate_futures_day(
    day_start: datetime,
    source: tuple[FuturesCandle, ...],
    *,
    closed: bool,
) -> FuturesCandle:
    first = source[0]
    last = source[-1]
    return FuturesCandle(
        exchange=first.exchange,
        market_type=first.market_type,
        contract_type=first.contract_type,
        symbol=first.symbol,
        interval="1d",
        open_time=day_start,
        close_time=last.close_time,
        open=first.open,
        high=max(item.high for item in source),
        low=min(item.low for item in source),
        close=last.close,
        volume=sum((item.volume for item in source), Decimal("0")),
        quote_volume=sum((item.quote_volume for item in source), Decimal("0")),
        trade_count=sum(item.trade_count for item in source),
        is_closed=closed,
        collected_at=_latest_collected_at(source),
    )


def _sum_optional_decimals(values: tuple[Decimal | None, ...]) -> Decimal | None:
    present = tuple(value for value in values if value is not None)
    if len(present) != len(values):
        return None
    return sum(present, Decimal("0"))


def _sum_optional_ints(values: tuple[int | None, ...]) -> int | None:
    present = tuple(value for value in values if value is not None)
    if len(present) != len(values):
        return None
    return sum(present)


def _latest_collected_at(candles: Sequence[_SourceCandle]) -> datetime | None:
    values = tuple(item.collected_at for item in candles if item.collected_at is not None)
    return max(values, default=None)


def _canonical_source_candle(candle: _SourceCandle) -> dict[str, object]:
    if isinstance(candle, FuturesCandle):
        return {
            "kind": "FUTURES",
            "exchange": candle.exchange,
            "market_type": candle.market_type,
            "contract_type": candle.contract_type,
            "symbol": candle.symbol,
            "interval": candle.interval,
            "open_time": candle.open_time,
            "close_time": candle.close_time,
            "open": candle.open,
            "high": candle.high,
            "low": candle.low,
            "close": candle.close,
            "volume": candle.volume,
            "quote_volume": candle.quote_volume,
            "trade_count": candle.trade_count,
            "is_closed": candle.is_closed,
        }
    return {
        "kind": "SPOT",
        "exchange": candle.exchange,
        "symbol": candle.symbol,
        "interval": candle.interval,
        "open_time": candle.open_time,
        "close_time": candle.close_time,
        "open": candle.open,
        "high": candle.high,
        "low": candle.low,
        "close": candle.close,
        "volume": candle.volume,
        "quote_volume": candle.quote_volume,
        "trades_count": candle.trades_count,
        "taker_buy_base_volume": candle.taker_buy_base_volume,
        "taker_buy_quote_volume": candle.taker_buy_quote_volume,
        "is_closed": candle.is_closed,
    }


def _canonical_audit(audit: DailyAggregationAudit) -> dict[str, object]:
    return {
        "day_start": audit.day_start,
        "observed_candle_count": audit.observed_candle_count,
        "expected_candle_count": audit.expected_candle_count,
        "missing_open_times": audit.missing_open_times,
        "open_source_candle_count": audit.open_source_candle_count,
        "invalid_close_time_count": audit.invalid_close_time_count,
        "complete": audit.complete,
        "documented": audit.documented,
        "action": audit.action,
        "source_hourly_hash": audit.source_hourly_hash,
        "issues": audit.issues,
    }


def _warning(audit: DailyAggregationAudit) -> str:
    return (
        "INCOMPLETE_UTC_DAY: "
        f"day={audit.day_start.date().isoformat()} "
        f"action={audit.action.value} "
        f"issues={','.join(audit.issues)}"
    )


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise DailyAggregationError(f"{name} must use UTC")


def _require_utc_hour(value: datetime, name: str) -> None:
    _require_utc(value, name)
    normalized = value.astimezone(UTC)
    if normalized.minute or normalized.second or normalized.microsecond:
        raise DailyAggregationError(f"{name} must align to an exact UTC hour")
