"""Immutable research-candidate freeze and future evaluation plans."""

from __future__ import annotations

import json
import subprocess
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from adaptive_trader.research.datasets import canonical_hash
from adaptive_trader.strategy.regime import SpotRegimeMode


@dataclass(frozen=True, slots=True)
class FutureHoldoutPlan:
    candidate_id: str
    freeze_time: datetime
    start_after: datetime
    minimum_calendar_days: int
    minimum_closed_trades: int
    market_type: str
    symbol: str
    interval: str
    status: str
    forbidden_until_complete: tuple[str, ...]
    paper_trading_enabled: bool = False

    def __post_init__(self) -> None:
        if self.minimum_calendar_days < 90:
            raise ValueError("future holdout requires at least 90 calendar days")
        if self.minimum_closed_trades < 20:
            raise ValueError("future holdout requires at least 20 closed trades")
        if self.start_after < self.freeze_time:
            raise ValueError("future holdout cannot start before freeze")


@dataclass(frozen=True, slots=True)
class FrozenCandidateFiles:
    candidate_id: str
    config_path: Path
    manifest_path: Path
    hash_path: Path
    config_hash: str


def candidate_config_hash(payload: dict[str, Any]) -> str:
    return canonical_hash(payload)


def build_future_holdout_plan(
    candidate_id: str,
    freeze_time: datetime,
    *,
    symbol: str = "ETHUSDT",
    interval: str = "1h",
) -> FutureHoldoutPlan:
    return FutureHoldoutPlan(
        candidate_id=candidate_id,
        freeze_time=freeze_time,
        start_after=freeze_time,
        minimum_calendar_days=90,
        minimum_closed_trades=20,
        market_type="SPOT",
        symbol=symbol,
        interval=interval,
        status="NOT_STARTED",
        forbidden_until_complete=(
            "PARAMETER_CHANGES",
            "RETROACTIVE_SELECTION",
            "PAPER_TRADING",
            "PRODUCTION_APPROVAL",
        ),
    )


def spot_to_futures_plan(candidate_id: str | None) -> dict[str, Any]:
    if candidate_id is None:
        return {
            "status": "NO_SPOT_CANDIDATE_FOR_FUTURES_TRANSFER",
            "executed": False,
            "futures_used_for_spot_selection": False,
            "leverages": ["1"],
        }
    return {
        "status": "PLANNED_NOT_EXECUTED",
        "candidate_id": candidate_id,
        "market_type": "USD_M_FUTURES",
        "modes": ["FUTURES_LONG_1X", "FUTURES_SHORT_MIRRORED_1X", "FUTURES_LONG_SHORT_1X"],
        "leverages": ["1"],
        "executed": False,
        "selection_allowed": False,
        "declaration": "Futures cannot rescue a failed Spot candidate.",
    }


def freeze_candidate(
    experiment_dir: Path,
    candidate_version: int,
    *,
    candidates_dir: Path = Path("configs/candidates"),
    created_at: datetime | None = None,
) -> FrozenCandidateFiles:
    if candidate_version < 1:
        raise ValueError("candidate version must be positive")
    decision_path = experiment_dir / "candidate_freeze_decision.json"
    decision = _read_object(decision_path)
    if decision.get("candidate_status") != "CANDIDATE":
        raise ValueError("candidate criteria did not pass; freeze is forbidden")
    configuration = decision.get("candidate_configuration")
    if not isinstance(configuration, dict):
        raise ValueError("candidate decision has no frozen configuration")
    if configuration.get("regime_mode") == SpotRegimeMode.NO_REGIME_FILTER_DIAGNOSTIC.value:
        raise ValueError("diagnostic-only regime mode cannot be frozen")
    if decision.get("consumed_test_used") is not False:
        raise ValueError("candidate decision does not prove consumed-test exclusion")
    if decision.get("validation_lock_unchanged") is not True:
        raise ValueError("candidate decision does not prove validation lock")

    candidate_id = (
        f"{str(configuration.get('symbol', 'ETHUSDT')).lower()}-"
        f"{configuration.get('interval', '1h')}-spot-candidate-v{candidate_version}"
    )
    payload = _candidate_payload(candidate_id, candidate_version, configuration)
    config_hash = candidate_config_hash(payload)
    config_path = candidates_dir / f"{candidate_id}.toml"
    manifest_path = candidates_dir / f"{candidate_id}.manifest.json"
    hash_path = candidates_dir / f"{candidate_id}.sha256"
    existing = tuple(path for path in (config_path, manifest_path, hash_path) if path.exists())
    if existing:
        raise FileExistsError(f"candidate version already exists: {existing}")
    candidates_dir.mkdir(parents=True, exist_ok=True)
    frozen_at = created_at or datetime.now(tz=UTC)
    commit, dirty = _git_metadata()
    manifest = {
        "candidate_id": candidate_id,
        "candidate_version": candidate_version,
        "created_at": frozen_at.isoformat(),
        "git_commit": commit,
        "git_dirty": dirty,
        "dataset_hash": decision.get("dataset_hash"),
        "development_period": decision.get("development_period"),
        "validation_period": decision.get("validation_period"),
        "consumed_test_period": {
            **_mapping(decision.get("consumed_test_period")),
            "excluded": True,
            "already_consumed": True,
            "not_used_for_selection": True,
        },
        "criteria": decision.get("criteria"),
        "observed_metrics": decision.get("observed_metrics"),
        "warnings": decision.get("warnings", []),
        "report_files": decision.get("report_files", []),
        "config_hash": config_hash,
        "reproducibility_hash": canonical_hash(
            {
                "candidate": payload,
                "dataset_hash": decision.get("dataset_hash"),
                "catalog_hash": decision.get("catalog_hash"),
                "development_lock": decision.get("development_lock"),
            }
        ),
        "status": "FROZEN_RESEARCH_CANDIDATE",
        "declaration": "NOT_APPROVED_FOR_PRODUCTION",
    }
    config_path.write_text(_render_candidate_toml(payload), encoding="utf-8")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    hash_path.write_text(f"{config_hash}  {config_path.name}\n", encoding="utf-8")

    holdout = build_future_holdout_plan(
        candidate_id,
        frozen_at,
        symbol=str(payload["symbol"]),
        interval=str(payload["interval"]),
    )
    (experiment_dir / "future_holdout_plan.json").write_text(
        json.dumps(asdict(holdout), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (experiment_dir / "spot_candidate_to_futures_plan.json").write_text(
        json.dumps(spot_to_futures_plan(candidate_id), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    decision["status"] = "CANDIDATE_FROZEN"
    decision["candidate_id"] = candidate_id
    decision["candidate_files"] = [
        str(config_path),
        str(manifest_path),
        str(hash_path),
    ]
    decision_path.write_text(
        json.dumps(decision, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return FrozenCandidateFiles(
        candidate_id=candidate_id,
        config_path=config_path,
        manifest_path=manifest_path,
        hash_path=hash_path,
        config_hash=config_hash,
    )


def inspect_candidate(path: Path) -> dict[str, Any]:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    if payload.get("market_type") != "SPOT":
        raise ValueError("candidate must be Spot")
    if payload.get("trading_mode") != "SPOT_LONG_ONLY":
        raise ValueError("candidate must be Spot long-only")
    strategy = payload.get("strategy")
    if not isinstance(strategy, dict):
        raise ValueError("candidate strategy section is missing")
    mode = SpotRegimeMode(str(strategy.get("regime_mode")))
    if mode.diagnostic_only:
        raise ValueError("diagnostic-only candidate is invalid")
    return payload


def verify_candidate(path: Path) -> dict[str, Any]:
    payload = inspect_candidate(path)
    expected = candidate_config_hash(payload)
    hash_path = path.with_suffix(".sha256")
    manifest_path = path.with_suffix(".manifest.json")
    line = hash_path.read_text(encoding="utf-8").strip()
    stored = line.split(maxsplit=1)[0] if line else ""
    manifest = _read_object(manifest_path)
    manifest_hash = manifest.get("config_hash")
    verified = expected == stored == manifest_hash
    if not verified:
        raise ValueError("candidate SHA-256 verification failed")
    return {
        "candidate_id": payload.get("candidate_id"),
        "config_hash": expected,
        "verified": True,
        "declaration": manifest.get("declaration"),
    }


def _candidate_payload(
    candidate_id: str,
    version: int,
    configuration: dict[str, Any],
) -> dict[str, Any]:
    return {
        "candidate_id": candidate_id,
        "candidate_version": version,
        "symbol": configuration["symbol"],
        "interval": configuration["interval"],
        "market_type": "SPOT",
        "trading_mode": "SPOT_LONG_ONLY",
        "strategy": {
            "version": configuration["strategy_version"],
            "regime_mode": configuration["regime_mode"],
            "short_ema_period": configuration["short_ema_period"],
            "long_ema_period": configuration["long_ema_period"],
            "minimum_volume_ratio": configuration["minimum_volume_ratio"],
            "maximum_atr_relative": configuration["maximum_atr_relative"],
            "stop_atr_multiple": configuration["stop_atr_multiple"],
            "target_r_multiple": configuration["target_r_multiple"],
            "time_exit_candles": configuration["time_exit_candles"],
        },
        "costs": {
            "maker_fee_bps": configuration["maker_fee_bps"],
            "taker_fee_bps": configuration["taker_fee_bps"],
            "spread_bps": configuration["spread_bps"],
            "slippage_bps": configuration["slippage_bps"],
        },
        "risk": {
            "maximum_open_positions": configuration["maximum_open_positions"],
            "maximum_position_percent": configuration["maximum_position_percent"],
            "maximum_daily_loss_percent": configuration["maximum_daily_loss_percent"],
            "maximum_trades_per_day": configuration["maximum_trades_per_day"],
            "allow_leverage": False,
            "allow_margin": False,
            "allow_futures": False,
        },
        "execution": {
            "warmup_candles": configuration["warmup_candles"],
            "latency_candles": configuration["latency_candles"],
            "intrabar_policy": "STOP_FIRST",
            "force_close_policy": configuration["force_close_at_end"],
            "trading_enabled": False,
        },
    }


def _render_candidate_toml(payload: dict[str, Any]) -> str:
    sections = []
    for key in (
        "candidate_id",
        "candidate_version",
        "symbol",
        "interval",
        "market_type",
        "trading_mode",
    ):
        sections.append(f"{key} = {_toml_value(payload[key])}")
    for section in ("strategy", "costs", "risk", "execution"):
        sections.append(f"\n[{section}]")
        values = _mapping(payload[section])
        sections.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
    return "\n".join(sections) + "\n"


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if value is None:
        return "0"
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid research artifact: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"research artifact must be an object: {path}")
    return payload


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _git_metadata() -> tuple[str | None, bool | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return None, None
    return commit, dirty
