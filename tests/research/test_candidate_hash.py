from datetime import UTC, datetime
from pathlib import Path

import pytest

from adaptive_trader.research.candidate_freeze import freeze_candidate, verify_candidate
from tests.research.test_candidate_freeze import write_decision


def test_candidate_sha_is_canonical_and_verifiable(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment"
    write_decision(experiment)
    files = freeze_candidate(
        experiment,
        1,
        candidates_dir=tmp_path / "candidates",
        created_at=datetime(2026, 8, 1, tzinfo=UTC),
    )

    verified = verify_candidate(files.config_path)

    assert verified["verified"] is True
    assert verified["config_hash"] == files.config_hash


def test_candidate_mutation_breaks_verification(tmp_path: Path) -> None:
    experiment = tmp_path / "experiment"
    write_decision(experiment)
    files = freeze_candidate(experiment, 1, candidates_dir=tmp_path / "candidates")
    files.config_path.write_text(
        files.config_path.read_text(encoding="utf-8").replace(
            'target_r_multiple = "2.5"',
            'target_r_multiple = "3"',
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="SHA-256"):
        verify_candidate(files.config_path)
