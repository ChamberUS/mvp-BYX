import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adaptive_trader.research.candidate_freeze import freeze_candidate


def write_decision(
    experiment: Path,
    *,
    status: str = "CANDIDATE",
    regime_mode: str = "STRICT_TRENDING_UP",
) -> None:
    experiment.mkdir()
    configuration = {
        "symbol": "ETHUSDT",
        "interval": "1h",
        "strategy_version": "deterministic-ema-atr-volume-v1",
        "regime_mode": regime_mode,
        "short_ema_period": 20,
        "long_ema_period": 50,
        "minimum_volume_ratio": "1",
        "maximum_atr_relative": "0.05",
        "stop_atr_multiple": "2",
        "target_r_multiple": "2.5",
        "time_exit_candles": 12,
        "maker_fee_bps": "10",
        "taker_fee_bps": "20",
        "spread_bps": "2",
        "slippage_bps": "5",
        "maximum_open_positions": 1,
        "maximum_position_percent": "5",
        "maximum_daily_loss_percent": "1",
        "maximum_trades_per_day": 5,
        "warmup_candles": 100,
        "latency_candles": 1,
        "force_close_at_end": True,
    }
    payload = {
        "candidate_status": status,
        "candidate_configuration": configuration,
        "consumed_test_used": False,
        "validation_lock_unchanged": True,
        "dataset_hash": "dataset",
        "catalog_hash": "catalog",
        "development_lock": "lock",
        "development_period": {"start": "2022", "end": "2024"},
        "validation_period": {"start": "2025", "end": "2025"},
        "consumed_test_period": {"start": "2026", "end": "2026"},
        "criteria": {"all": True},
        "observed_metrics": {"trades": 40},
        "report_files": [],
    }
    (experiment / "candidate_freeze_decision.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_valid_freeze_creates_manifest_hash_and_no_production_approval(
    tmp_path: Path,
) -> None:
    experiment = tmp_path / "experiment"
    candidates = tmp_path / "candidates"
    write_decision(experiment)

    files = freeze_candidate(
        experiment,
        1,
        candidates_dir=candidates,
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    manifest = json.loads(files.manifest_path.read_text(encoding="utf-8"))

    assert files.config_path.exists()
    assert files.hash_path.exists()
    assert manifest["declaration"] == "NOT_APPROVED_FOR_PRODUCTION"
    assert manifest["consumed_test_period"]["excluded"] is True


def test_invalid_and_diagnostic_freeze_are_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid"
    diagnostic = tmp_path / "diagnostic"
    write_decision(invalid, status="NOT_CANDIDATE")
    write_decision(diagnostic, regime_mode="NO_REGIME_FILTER_DIAGNOSTIC")

    with pytest.raises(ValueError):
        freeze_candidate(invalid, 1, candidates_dir=tmp_path / "invalid-candidates")
    with pytest.raises(ValueError):
        freeze_candidate(diagnostic, 1, candidates_dir=tmp_path / "diag-candidates")


def test_existing_candidate_version_is_never_overwritten(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment"
    candidates = tmp_path / "candidates"
    write_decision(experiment)
    freeze_candidate(experiment, 1, candidates_dir=candidates)

    with pytest.raises((ValueError, FileExistsError)):
        freeze_candidate(experiment, 1, candidates_dir=candidates)
