from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.models import FuturesCandle, MarkPriceCandle

START = datetime(2021, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class PriceSpec:
    close: Decimal
    high: Decimal
    low: Decimal


@dataclass(frozen=True, slots=True)
class HourSpec:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    mark_open: Decimal | None = None
    mark_high: Decimal | None = None
    mark_low: Decimal | None = None
    mark_close: Decimal | None = None


def daily_series(
    overrides: dict[int, PriceSpec],
    *,
    total_days: int = 206,
    baseline_close: Decimal = Decimal("100"),
    baseline_high: Decimal = Decimal("101"),
    baseline_low: Decimal = Decimal("99"),
) -> tuple[Candle, ...]:
    candles: list[Candle] = []
    previous_close = baseline_close
    for index in range(total_days):
        spec = overrides.get(
            index,
            PriceSpec(
                close=baseline_close,
                high=baseline_high,
                low=baseline_low,
            ),
        )
        opened = START + timedelta(days=index)
        candles.append(
            Candle(
                symbol="ETHUSDT",
                interval="1d",
                timestamp=opened,
                close_time=opened + timedelta(days=1) - timedelta(milliseconds=1),
                open=previous_close,
                high=spec.high,
                low=spec.low,
                close=spec.close,
                volume=Decimal("100"),
            )
        )
        previous_close = spec.close
    return tuple(candles)


def spot_hours(specs: dict[int, HourSpec]) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1h",
            timestamp=START + timedelta(days=day),
            close_time=START
            + timedelta(days=day, hours=1)
            - timedelta(milliseconds=1),
            open=spec.open,
            high=spec.high,
            low=spec.low,
            close=spec.close,
            volume=Decimal("10"),
        )
        for day, spec in sorted(specs.items())
    )


def futures_hours(
    specs: dict[int, HourSpec],
) -> tuple[tuple[FuturesCandle, ...], tuple[MarkPriceCandle, ...]]:
    candles: list[FuturesCandle] = []
    marks: list[MarkPriceCandle] = []
    for day, spec in sorted(specs.items()):
        opened = START + timedelta(days=day)
        closed = opened + timedelta(hours=1) - timedelta(milliseconds=1)
        candles.append(
            FuturesCandle(
                exchange="BINANCE",
                market_type=MarketType.USD_M_FUTURES,
                contract_type=ContractType.PERPETUAL,
                symbol="ETHUSDT",
                interval="1h",
                open_time=opened,
                close_time=closed,
                open=spec.open,
                high=spec.high,
                low=spec.low,
                close=spec.close,
                volume=Decimal("10"),
                quote_volume=Decimal("1000"),
                trade_count=10,
                is_closed=True,
            )
        )
        mark_open = spec.mark_open if spec.mark_open is not None else spec.open
        mark_high = spec.mark_high if spec.mark_high is not None else spec.high
        mark_low = spec.mark_low if spec.mark_low is not None else spec.low
        mark_close = spec.mark_close if spec.mark_close is not None else spec.close
        marks.append(
            MarkPriceCandle(
                symbol="ETHUSDT",
                interval="1h",
                open_time=opened,
                close_time=closed,
                open=mark_open,
                high=mark_high,
                low=mark_low,
                close=mark_close,
            )
        )
    return tuple(candles), tuple(marks)
