from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trader.domain.market import PositionSide, TradingMode
from adaptive_trader.futures.datasets import validate_futures_dataset
from adaptive_trader.futures.engine import FuturesBacktestEngine
from adaptive_trader.futures.report import build_manifest, write_futures_report
from adaptive_trader.futures.research import (
    development_only_dataset,
    futures_benchmarks,
    futures_comparison_row,
    run_futures_backtest,
    run_futures_walk_forward,
    write_market_comparison,
    write_walk_forward_report,
)
from tests.futures.conftest import make_candles, make_marks
from tests.futures.test_engine import EntryAnalyzer


def dataset(candles, marks):
    from adaptive_trader.futures.models import FundingMissingPolicy

    return validate_futures_dataset(
        candles,
        marks,
        (),
        source="offline-fixture",
        funding_enabled=False,
        funding_missing_policy=FundingMissingPolicy.DISABLE_EXPLICITLY,
    )


def test_futures_report_manifest_and_benchmarks(
    tmp_path: Path,
    futures_config,
    futures_candles,
    mark_prices,
) -> None:
    research_dataset = dataset(futures_candles, mark_prices)
    result = FuturesBacktestEngine(
        futures_config,
        analyzer=EntryAnalyzer(PositionSide.LONG),
    ).run(futures_candles, mark_prices, ())
    files = write_futures_report(tmp_path, research_dataset, futures_config, result)
    manifest = build_manifest(research_dataset, futures_config, result)
    benchmarks = futures_benchmarks(research_dataset, futures_config)
    assert {"dataset.json", "manifest.json", "summary.json", "report.md"} <= set(files)
    assert manifest.funding_hash == research_dataset.funding_hash
    assert len(manifest.reproducibility_hash) == 64
    assert manifest.authenticated_endpoints_used is False
    assert {item["benchmark"] for item in benchmarks} == {
        "CASH",
        "FUTURES_LONG_1X",
        "FUTURES_SHORT_1X",
    }


def test_small_fixed_walk_forward_and_consumed_period_protection(
    tmp_path: Path,
    futures_config,
    start_time,
) -> None:
    closes = tuple(str(100 + index % 10) for index in range(100))
    candles = make_candles(start_time, closes)
    research_dataset = dataset(candles, make_marks(candles))
    runs = run_futures_walk_forward(
        research_dataset,
        futures_config,
        train_days=1,
        validation_days=1,
        step_days=1,
    )
    files = write_walk_forward_report(
        tmp_path,
        research_dataset,
        futures_config,
        runs,
    )
    assert len(runs) >= 2
    assert all(item.result.leverage == Decimal("1") for item in runs)
    assert "walk_forward_summary.json" in files
    consumed_start = start_time + timedelta(hours=70)
    safe = development_only_dataset(
        research_dataset,
        consumed_test_start=consumed_start,
        consumed_test_end=consumed_start + timedelta(hours=10),
        config=futures_config,
    )
    assert safe.candles[-1].open_time < consumed_start


def test_comparison_keeps_markets_separate_and_leverage_visible(
    tmp_path: Path,
    futures_config,
    futures_candles,
    mark_prices,
) -> None:
    two_x = replace(
        futures_config,
        leverage=Decimal("2"),
        trading_mode=TradingMode.FUTURES_LONG_ONLY,
    )
    result = FuturesBacktestEngine(
        two_x,
        analyzer=EntryAnalyzer(PositionSide.LONG),
    ).run(futures_candles, mark_prices, ())
    futures_row = futures_comparison_row(
        "FUTURES_LONG_BASELINE_2X",
        result,
        one_x_candidate=False,
    )
    spot_row = {
        **futures_row,
        "experiment": "SPOT_BASELINE",
        "market_type": "SPOT",
        "trading_mode": "SPOT_LONG_ONLY",
        "leverage": Decimal("1"),
        "funding": Decimal("0"),
        "liquidations": 0,
    }
    paths = write_market_comparison(tmp_path, (spot_row, futures_row))
    assert all(path.exists() for path in paths)
    assert futures_row["leverage"] == Decimal("2")
    assert futures_row["candidate_status"] == "NOT_CANDIDATE"
    assert "LEVERAGE_AMPLIFIES_NON_CANDIDATE" in str(futures_row["warnings"])
    text = (tmp_path / "market_comparison.md").read_text(encoding="utf-8")
    assert "never added together" in text


def test_run_futures_backtest_wrapper(
    futures_config,
    futures_candles,
    mark_prices,
) -> None:
    research_dataset = dataset(futures_candles, mark_prices)
    result = run_futures_backtest(research_dataset, futures_config)
    assert result.market_type.value == "USD_M_FUTURES"


@pytest.mark.parametrize("leverage", [Decimal("1"), Decimal("2"), Decimal("3")])
def test_fixed_leverage_scenarios_are_reported_without_selection(
    futures_config,
    futures_candles,
    mark_prices,
    leverage,
) -> None:
    config = replace(futures_config, leverage=leverage)
    result = FuturesBacktestEngine(
        config,
        analyzer=EntryAnalyzer(PositionSide.LONG),
    ).run(futures_candles, mark_prices, ())
    assert result.leverage == leverage
    assert result.metrics.maximum_effective_leverage <= leverage
