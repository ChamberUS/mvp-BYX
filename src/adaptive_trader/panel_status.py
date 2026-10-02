"""Read-only panel contract. No recorder imports, raw event reads or research execution."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def read_object(path: Path, warnings: list[str]) -> dict:
    try:
        if any("validation" in p.lower() or "holdout" in p.lower() for p in path.resolve().parts):
            raise ValueError("protected partition")
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError("expected object")
        return value
    except (OSError, ValueError):
        warnings.append(f"Unavailable JSON: {path.name}")
        return {}


def capture_status(project: Path, warnings: list[str]) -> dict:
    capture = dict.fromkeys((
        "running", "campaign_id", "session_id", "market", "symbol", "streams", "started_at",
        "last_event_at", "last_status_at", "uptime_seconds", "recorder_health", "storage_path",
        "disk_usage_bytes",
    ))
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=2, check=True,
        )
        matches = []
        cli = str(project / ".venv/bin/adaptive-trader")
        for line in result.stdout.splitlines():
            try:
                pid, command = line.strip().split(maxsplit=1)
                argv = shlex.split(command)
            except ValueError:
                continue
            if cli not in argv:
                continue
            args = argv[argv.index(cli) + 1:]
            if args[:3] == ["market", "microstructure", "campaign-record"]:
                matches.append((pid, args))
        if len(matches) != 1:
            capture["running"] = False if not matches else None
            if matches:
                warnings.append("Multiple active campaigns; capture selection is ambiguous")
            return capture
        pid, args = matches[0]
        options = dict(zip(args[3::2], args[4::2], strict=False))
        capture.update(
            running=True, campaign_id=options.get("--campaign-id"),
            symbol=options.get("--symbol"),
            market={"futures": "USD-M Futures", "spot": "Spot"}.get(options.get("--market")),
            streams=options["--streams"].split(",") if options.get("--streams") else None,
        )
        output = options.get("--output-dir")
        if output:
            root = (project / output).resolve()
            capture["storage_path"] = str(root)
            files = subprocess.run(
                ["lsof", "-a", "-p", pid, "-Fn"], capture_output=True, text=True,
                timeout=2, check=False,
            )
            for line in files.stdout.splitlines():
                if not line.startswith("n"):
                    continue
                path = Path(line[1:])
                if not path.is_relative_to(root) or not re.fullmatch(
                    r"events-\d+\.jsonl\.gz\.part", path.name,
                ):
                    continue
                session = path.parent.name
                stamp = re.fullmatch(r"microstructure-(\d{8}T\d{6}Z)-[a-z_]+", session)
                if stamp:
                    capture["session_id"] = session
                    start = datetime.strptime(stamp[1], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                    capture["started_at"] = start.isoformat()
                    capture["uptime_seconds"] = int((datetime.now(UTC) - start).total_seconds())
                    capture["last_status_at"] = datetime.fromtimestamp(
                        path.stat().st_mtime, UTC,
                    ).isoformat()
                    # Only the open part's size, not total storage usage.
                    capture["active_file_bytes"] = path.stat().st_size
                    break
        warnings.append("Recorder process observed; live health/event telemetry is unavailable")
    except (OSError, subprocess.SubprocessError):
        warnings.append("Capture process metadata unavailable")
    return capture


def status(project: Path, *, health_only: bool = False) -> dict:
    project = project.resolve()
    warnings: list[str] = []
    try:
        build = version("adaptive-trader")
    except PackageNotFoundError:
        build = None
    result = {
        "schema_version": 1,
        "system": {"online": True, "version": build, "project_path": str(project),
                   "data_source": "REAL"},
        "capture": capture_status(project, warnings),
        "warnings": warnings,
    }
    if not health_only:
        reports = project / "reports/research"
        aggregates = []
        for stage in ("checkpoint", "feature", "label"):
            obj = read_object(reports / f"train-{stage}-aggregate.json", warnings)
            if obj.get("partition") != "TRAIN":
                warnings.append(f"Rejected non-TRAIN or unpartitioned {stage} summary")
                obj = {}
            aggregates.append(obj)
        cp, ft, lb = aggregates
        base = cp or ft or lb
        for obj in aggregates:
            if obj and obj.get("dataset_id") != base.get("dataset_id"):
                warnings.append("Rejected mismatched TRAIN aggregate")
                obj.clear()
        frozen = read_object(project / "docs/TRAIN_DISCOVERY_FREEZE_V1.json", warnings)
        result["research"] = {
            "partition": "TRAIN", "dataset_id": base.get("dataset_id"),
            "session_count": base.get("session_count"), "anchors": ft.get("row_count"),
            "features_status": "AGGREGATE_AVAILABLE" if ft else None,
            "pure_mid_labels_status": "AGGREGATE_AVAILABLE" if lb else None,
            "frozen_spec_status": frozen.get("status"),
            "validation_status": "LOCKED", "validation_readiness": None,
            "final_holdout": "SEALED",
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=Path.cwd())
    parser.add_argument("--health-only", action="store_true")
    args = parser.parse_args()
    print(json.dumps(status(args.project, health_only=args.health_only)))


if __name__ == "__main__":
    main()
