from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from adaptive_trader.research.pullback_analysis import PullbackFold
from adaptive_trader.research.pullback_calibration_experiment import (
    PullbackCalibrationService,
)
from adaptive_trader.research.pullback_calibration_report import (
    expected_calibration_artifact_names,
    write_pullback_calibration_report,
)
from adaptive_trader.research.pullback_catalog import (
    PullbackExperimentPeriods,
)
from adaptive_trader.research.pullback_experiment import (
    PullbackExperimentRequest,
)
from tests.research.pullback_helpers import START, run_with_return


def test_offline_service_and_writer_emit_exact_artifact_contract(
    monkeypatch,
    tmp_path: Path,
) -> None:
    service = PullbackCalibrationService(object(), object())  # type: ignore[arg-type]
    monkeypatch.setattr(
        service._legacy,
        "_load_datasets",
        lambda request: (
            (),
            None,
            {
                "spot": {"development_hash": "development-hash"},
                "query_end": request.periods.validation_end,
            },
        ),
    )

    def fake_run(
        request,
        market,
        mode,
        hypothesis,
        period,
        spot,
        futures,
    ):
        run = replace(
            run_with_return("BASE", "0"),
            market=market,
            mode=mode,
            variant_id=hypothesis.variant_id,
            period=period,
            evaluation_start=(
                request.periods.development_start
                if period == "DEVELOPMENT"
                else request.periods.validation_start
            ),
            evaluation_end=(
                request.periods.development_end
                if period == "DEVELOPMENT"
                else request.periods.validation_end
            ),
            trades=(),
            entry_count=0,
            approvals=0,
            executions=0,
            long_signals=0,
            short_signals=0,
            pullback_traces=(),
        )
        folds = tuple(
            PullbackFold(
                fold=index,
                train_start=START,
                train_end=START + timedelta(days=1),
                validation_start=START + timedelta(days=2),
                validation_end=START + timedelta(days=3),
                run=run,
            )
            for index in range(1, 5)
        )
        return run, folds

    monkeypatch.setattr(service, "_base_run_and_folds", fake_run)
    monkeypatch.setattr(
        "adaptive_trader.research.pullback_calibration_experiment._bars_for",
        lambda *args: (),
    )
    request = PullbackExperimentRequest(
        symbol="ETHUSDT",
        interval="1h",
        periods=PullbackExperimentPeriods.pre_registered(),
        markets=("spot",),
        futures_modes=(),
        leverage=Decimal("1"),
        output_dir=tmp_path,
    )
    bundle = service.run(request)
    assert len(bundle.operational_frequency_results) == 8
    assert not bundle.selection_decisions[0]["selected_variant_ids"]
    assert len(bundle.assessments) == 8
    output = write_pullback_calibration_report(
        bundle,
        tmp_path,
        git_commit="fixture-commit",
        git_dirty=True,
    )
    assert tuple(sorted(path.name for path in output.iterdir())) == tuple(
        sorted(expected_calibration_artifact_names())
    )
