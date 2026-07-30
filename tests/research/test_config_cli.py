from pathlib import Path

import pytest

from adaptive_trader.research.config import ResearchConfigError, load_experiment_toml


def test_research_toml_loader_is_standard_library_and_timezone_aware(tmp_path: Path) -> None:
    path = tmp_path / "research.toml"
    path.write_text(
        """
[experiment]
name = "local"
mode = "holdout"
output_dir = "reports/research"
[dataset]
symbol = "ETHUSDT"
interval = "1m"
start = "2026-01-01T00:00:00Z"
end = "2026-01-02T00:00:00Z"
gap_policy = "WARN"
""",
        encoding="utf-8",
    )

    config = load_experiment_toml(path)

    assert config.symbol == "ETHUSDT"
    assert config.start.tzinfo is not None


def test_research_toml_rejects_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "secret.toml"
    path.write_text(
        """
[dataset]
symbol = "ETHUSDT"
api_key = "do-not-store"
start = "2026-01-01T00:00:00Z"
end = "2026-01-02T00:00:00Z"
""",
        encoding="utf-8",
    )

    with pytest.raises(ResearchConfigError, match="secret"):
        load_experiment_toml(path)
