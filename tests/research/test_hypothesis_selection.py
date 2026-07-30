from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from adaptive_trader.research.spot_experiment import (
    _return_to_drawdown,
    _trade_concentration,
    _write_csv,
)
from adaptive_trader.research.spot_hypotheses import (
    DevelopmentSelectionMetric,
    select_development_candidate,
)
from adaptive_trader.strategy.regime import SpotRegimeMode


def _metric(
    variant: str,
    median: str,
    *,
    positive: str = "50",
    drawdown: str = "5",
    zero: str = "0",
    sensitivity: str = "1",
    trades: int = 30,
    complexity: int = 1,
) -> DevelopmentSelectionMetric:
    return DevelopmentSelectionMetric(
        variant_id=variant,
        regime_mode=SpotRegimeMode.STRICT_TRENDING_UP,
        median_walk_forward_net_return=Decimal(median),
        positive_fold_percent=Decimal(positive),
        worst_drawdown_percent=Decimal(drawdown),
        zero_trade_fold_percent=Decimal(zero),
        cost_sensitivity=Decimal(sensitivity),
        closed_trade_count=trades,
        complexity_rank=complexity,
        fold_count=4,
    )


def test_selection_uses_fixed_primary_and_tiebreakers() -> None:
    selected = select_development_candidate(
        (
            _metric("BASE", "1", positive="50", complexity=1),
            _metric("TARGET", "1", positive="75", complexity=2),
        )
    )

    assert selected.selected_variant_id == "TARGET"


def test_complexity_is_last_tiebreaker() -> None:
    selected = select_development_candidate(
        (
            _metric("COMPLEX", "1", complexity=4),
            _metric("BASE", "1", complexity=1),
        )
    )

    assert selected.selected_variant_id == "BASE"


def test_validation_and_consumed_metrics_are_rejected() -> None:
    metric = _metric("BASE", "1")
    changed = replace(metric, source_period="VALIDATION")

    try:
        select_development_candidate((changed,))
    except ValueError as exc:
        assert "development" in str(exc)
    else:
        raise AssertionError("validation metric entered selection")


def test_no_candidate_when_all_development_results_are_negative() -> None:
    selected = select_development_candidate(
        (_metric("BASE", "-1"), _metric("TIME", "-0.1"))
    )

    assert selected.status == "NO_DEVELOPMENT_CANDIDATE"
    assert selected.selected_variant_id is None


def test_concentration_and_return_to_drawdown_are_deterministic(tmp_path: Path) -> None:
    trades = (
        SimpleNamespace(net_pnl=Decimal("6")),
        SimpleNamespace(net_pnl=Decimal("4")),
        SimpleNamespace(net_pnl=Decimal("-3")),
    )

    best, top_five, without_best = _trade_concentration(trades)

    assert best == Decimal("60")
    assert top_five == Decimal("100")
    assert without_best == Decimal("1")
    assert _return_to_drawdown(Decimal("5"), Decimal("2")) == Decimal("2.5")
    assert _return_to_drawdown(None, Decimal("2")) is None
    assert _return_to_drawdown(Decimal("1"), Decimal("0")) is None

    empty = tmp_path / "empty.csv"
    populated = tmp_path / "populated.csv"
    _write_csv(empty, [])
    _write_csv(populated, [{"variant": "BASE", "warnings": ["NONE"]}])
    assert empty.read_text(encoding="utf-8") == "status\nNO_ROWS\n"
    assert '"NONE"' in populated.read_text(encoding="utf-8")
