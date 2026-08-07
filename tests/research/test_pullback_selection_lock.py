from dataclasses import replace
from datetime import UTC, datetime

import pytest

from adaptive_trader.research.pullback_analysis import (
    PullbackClassification,
    select_development_hypotheses,
)
from adaptive_trader.research.pullback_catalog import (
    PullbackExperimentPeriods,
    PullbackValidationLock,
    load_pullback_catalog,
)
from tests.research.pullback_helpers import fold_summary


def test_selection_uses_development_base_and_at_most_two_variants() -> None:
    catalog = load_pullback_catalog()
    complexity = {
        item.variant_id: item.complexity_rank for item in catalog.hypotheses
    }
    summaries = (
        fold_summary("ORIGINAL_BASELINE", "-1", "25"),
        fold_summary("PULLBACK_BASE", "1", "75"),
        fold_summary("PULLBACK_PERSISTENCE_6", "2", "50"),
        fold_summary("PULLBACK_TIME_EXIT_24", "0.5", "75"),
        fold_summary("PULLBACK_REGIME_LOSS_EXIT", "-0.1", "75"),
        fold_summary(
            "PULLBACK_PERSISTENCE_6_REGIME_LOSS_EXIT",
            "3",
            "25",
        ),
    )

    selection = select_development_hypotheses(
        summaries,
        complexity_by_variant=complexity,
    )

    assert selection.status is PullbackClassification.INCONCLUSIVE
    assert selection.selected_variant_ids == (
        "PULLBACK_PERSISTENCE_6",
        "PULLBACK_BASE",
    )
    assert len(selection.selected_variant_ids) == 2


def test_validation_rows_cannot_enter_development_selection() -> None:
    catalog = load_pullback_catalog()
    complexity = {
        item.variant_id: item.complexity_rank for item in catalog.hypotheses
    }
    validation = replace(
        fold_summary("PULLBACK_BASE", "10", "100"),
        period="VALIDATION",
    )

    with pytest.raises(ValueError, match="development BASE"):
        select_development_hypotheses(
            (validation,),
            complexity_by_variant=complexity,
        )


def test_validation_lock_rejects_variant_or_catalog_change() -> None:
    lock = PullbackValidationLock.create(
        market="SPOT",
        mode="LONG",
        variant_ids=("ORIGINAL_BASELINE", "PULLBACK_BASE"),
        catalog_hash="catalog",
    )

    with pytest.raises(ValueError, match="development lock"):
        lock.assert_unchanged(
            market="SPOT",
            mode="LONG",
            variant_ids=("ORIGINAL_BASELINE", "PULLBACK_PERSISTENCE_6"),
            catalog_hash="catalog",
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("validation_start", datetime(2025, 1, 1, tzinfo=UTC)),
        ("validation_end", datetime(2026, 1, 1, tzinfo=UTC)),
    ],
)
def test_2025_and_2026_cannot_become_research_periods(
    field: str,
    value: datetime,
) -> None:
    periods = PullbackExperimentPeriods.pre_registered()

    with pytest.raises(ValueError):
        changed = replace(periods, **{field: value})
        changed.assert_pre_registered()
