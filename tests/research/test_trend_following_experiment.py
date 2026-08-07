from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from adaptive_trader.config.settings import TradingConfig
from adaptive_trader.domain.market import ContractType, MarketType
from adaptive_trader.domain.models import Candle
from adaptive_trader.futures.models import (
    FundingRate,
    FuturesCandle,
    MarkPriceCandle,
)
from adaptive_trader.research.trend_following_catalog import TrendFollowingPeriods
from adaptive_trader.research.trend_following_engine import TrendFollowingRun
from adaptive_trader.research.trend_following_experiment import (
    TrendFollowingExperimentRequest,
    TrendFollowingExperimentService,
)
from adaptive_trader.research.trend_following_report import (
    expected_trend_following_artifact_names,
    write_trend_following_report,
)
from adaptive_trader.storage.sqlite import DatabaseRepository

ZERO = Decimal("0")


def _spot_hours(start: datetime, days: int) -> tuple[Candle, ...]:
    return tuple(
        Candle(
            symbol="ETHUSDT",
            interval="1h",
            timestamp=start + timedelta(days=day, hours=hour),
            close_time=start
            + timedelta(days=day, hours=hour, minutes=59, seconds=59),
            open=Decimal("100"),
            high=Decimal("101"),
            low=Decimal("99"),
            close=Decimal("100"),
            volume=Decimal("1"),
            quote_volume=Decimal("100"),
            trades_count=1,
        )
        for day in range(days)
        for hour in range(24)
    )


def _futures_hours(
    start: datetime,
    days: int,
) -> tuple[tuple[FuturesCandle, ...], tuple[MarkPriceCandle, ...]]:
    candles: list[FuturesCandle] = []
    marks: list[MarkPriceCandle] = []
    for day in range(days):
        for hour in range(24):
            open_time = start + timedelta(days=day, hours=hour)
            close_time = open_time + timedelta(minutes=59, seconds=59)
            candles.append(
                FuturesCandle(
                    exchange="BINANCE",
                    market_type=MarketType.USD_M_FUTURES,
                    contract_type=ContractType.PERPETUAL,
                    symbol="ETHUSDT",
                    interval="1h",
                    open_time=open_time,
                    close_time=close_time,
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                    volume=Decimal("1"),
                    quote_volume=Decimal("100"),
                    trade_count=1,
                    is_closed=True,
                )
            )
            marks.append(
                MarkPriceCandle(
                    symbol="ETHUSDT",
                    interval="1h",
                    open_time=open_time,
                    close_time=close_time,
                    open=Decimal("100"),
                    high=Decimal("101"),
                    low=Decimal("99"),
                    close=Decimal("100"),
                )
            )
    return tuple(candles), tuple(marks)


def _empty_run(
    *,
    market: str,
    mode: str,
    variant_id: str,
    period: str,
    scenario: str,
    evaluation_start: datetime,
    evaluation_end: datetime,
) -> TrendFollowingRun:
    return TrendFollowingRun(
        market=market,
        mode=mode,
        variant_id=variant_id,
        period=period,
        scenario=scenario,
        evaluation_start=evaluation_start,
        evaluation_end=evaluation_end,
        effective_evaluation_start=evaluation_start,
        initial_capital=Decimal("10000"),
        final_capital=Decimal("10000"),
        gross_pnl=ZERO,
        net_pnl=ZERO,
        gross_return_percent=ZERO,
        net_return_percent=ZERO,
        win_rate_percent=ZERO,
        profit_factor=None,
        expectancy=ZERO,
        median_trade_pnl=ZERO,
        maximum_drawdown_percent=ZERO,
        return_to_drawdown=None,
        exposure_percent=ZERO,
        fees=ZERO,
        execution_costs=ZERO,
        funding_paid=ZERO,
        funding_received=ZERO,
        net_funding=ZERO,
        liquidation_count=0,
        evaluated_daily_candles=1,
        entry_signals=0,
        risk_approvals=0,
        executions=0,
        long_trades=0,
        short_trades=0,
        defensive_mode_activations=0,
        candles_in_defensive_mode=0,
        trades_in_defensive_mode=0,
        risk_reduction_duration_days=0,
        net_pnl_without_best_trade=ZERO,
        net_pnl_without_top_three=ZERO,
        best_trade_concentration_percent=ZERO,
        top_three_concentration_percent=ZERO,
        reason_counts=(),
        warnings=(),
        trades=(),
        traces=(),
    )


def test_minimum_spot_and_futures_experiment_locks_before_validation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    development_start = datetime(2022, 1, 1, tzinfo=UTC)
    validation_start = datetime(2024, 1, 1, tzinfo=UTC)
    spot_development = _spot_hours(development_start, 200)
    spot_validation = _spot_hours(validation_start, 1)
    futures_development, marks_development = _futures_hours(
        development_start,
        200,
    )
    futures_validation, marks_validation = _futures_hours(validation_start, 1)
    query_years: list[int] = []

    class Repository:
        def _validation_guard(self, start_time: datetime) -> None:
            query_years.append(start_time.year)
            assert start_time.year < 2025
            if start_time.year == 2024:
                locks = tuple(
                    tmp_path.glob(
                        "*/trend_following_validation_lock.json"
                    )
                )
                assert len(locks) == 1
                assert locks[0].stat().st_size > 0

        def get_candles(
            self,
            symbol: str,
            interval: str,
            *,
            start_time: datetime,
            end_time: datetime,
        ) -> tuple[Candle, ...]:
            self._validation_guard(start_time)
            return spot_validation if start_time.year == 2024 else spot_development

        def get_futures_candles(
            self,
            symbol: str,
            interval: str,
            *,
            start_time: datetime,
            end_time: datetime,
        ) -> tuple[FuturesCandle, ...]:
            self._validation_guard(start_time)
            return (
                futures_validation
                if start_time.year == 2024
                else futures_development
            )

        def get_mark_prices(
            self,
            symbol: str,
            interval: str,
            *,
            start_time: datetime,
            end_time: datetime,
        ) -> tuple[MarkPriceCandle, ...]:
            self._validation_guard(start_time)
            return (
                marks_validation
                if start_time.year == 2024
                else marks_development
            )

        def get_funding_rates(
            self,
            symbol: str,
            *,
            start_time: datetime,
            end_time: datetime,
        ) -> tuple[FundingRate, ...]:
            self._validation_guard(start_time)
            return (
                FundingRate(
                    symbol="ETHUSDT",
                    funding_time=start_time,
                    funding_rate=Decimal("0.0001"),
                    mark_price=Decimal("100"),
                ),
            )

    def fake_run_one(
        self: object,
        *,
        request: object,
        group: object,
        hypothesis: object,
        period_data: object,
        signal_daily: object,
        period: str,
        evaluation_start: datetime,
        evaluation_end: datetime,
        costs: object,
    ) -> TrendFollowingRun:
        return _empty_run(
            market=group.market.lower(),
            mode=group.mode.lower().replace("_", "-"),
            variant_id=hypothesis.variant_id,
            period=period,
            scenario=costs.scenario,
            evaluation_start=evaluation_start,
            evaluation_end=evaluation_end,
        )

    monkeypatch.setattr(
        TrendFollowingExperimentService,
        "_run_one",
        fake_run_one,
    )
    request = TrendFollowingExperimentRequest(
        symbol="ETHUSDT",
        source_interval="1h",
        strategy_interval="1d",
        periods=TrendFollowingPeriods.pre_registered(),
        markets=("spot", "futures"),
        futures_modes=("long", "short", "long-short"),
        leverage=Decimal("1"),
        output_dir=tmp_path,
    )

    bundle = TrendFollowingExperimentService(
        cast(DatabaseRepository, Repository()),
        TradingConfig(interval="1h"),
    ).run(request, git_commit="commit", git_dirty=True)

    assert set(query_years) == {2022, 2024}
    assert 2025 not in query_years and 2026 not in query_years
    assert bundle.manifest["lock_persisted_before_validation"] is True
    assert bundle.manifest["validation_lock_preserved"] is True
    assert bundle.validation_lock["locked_before_validation"] is True
    assert any(row["market"] == "SPOT" for row in bundle.development_results)
    assert any(row["market"] == "FUTURES" for row in bundle.development_results)
    assert bundle.funding_impact
    assert not any(
        row.get("benchmark") is False
        for row in bundle.validation_results
    )

    output = write_trend_following_report(
        bundle,
        git_commit="commit",
        git_dirty=True,
    )
    assert len(tuple(output.iterdir())) == len(
        expected_trend_following_artifact_names()
    )
