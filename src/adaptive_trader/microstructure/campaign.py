"""Resumable, hash-addressed campaigns of public microstructure sessions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from adaptive_trader.microstructure.storage import inspect_session


class DatasetSufficiency(StrEnum):
    ENGINEERING_ONLY = "ENGINEERING_ONLY"
    EXPLORATORY = "EXPLORATORY"
    DISCOVERY_READY = "DISCOVERY_READY"
    CONFIRMATION_READY = "CONFIRMATION_READY"


@dataclass(frozen=True, slots=True)
class CampaignSession:
    session_id: str
    path: str
    market: str
    symbol: str
    start: str
    end: str
    duration_seconds: float
    event_count: int
    event_hashes: tuple[str, ...]
    quality: str
    warnings: tuple[str, ...]
    software_commit: str


@dataclass(frozen=True, slots=True)
class MicrostructureDatasetCampaign:
    campaign_id: str
    market: str
    symbol: str
    sessions: tuple[CampaignSession, ...]
    total_duration_seconds: float
    utc_dates_covered: tuple[str, ...]
    event_counts: dict[str, int]
    capture_breaks: tuple[dict[str, object], ...]
    warnings: tuple[str, ...]
    software_commits: tuple[str, ...]
    status: DatasetSufficiency
    campaign_hash: str

    def as_dict(self) -> dict[str, object]:
        return {
            "campaign_id": self.campaign_id,
            "market": self.market,
            "symbol": self.symbol,
            "sessions": [
                {
                    "session_id": item.session_id,
                    "path": item.path,
                    "market": item.market,
                    "symbol": item.symbol,
                    "start": item.start,
                    "end": item.end,
                    "duration_seconds": item.duration_seconds,
                    "event_count": item.event_count,
                    "event_hashes": list(item.event_hashes),
                    "quality": item.quality,
                    "warnings": list(item.warnings),
                    "software_commit": item.software_commit,
                }
                for item in self.sessions
            ],
            "total_duration_seconds": self.total_duration_seconds,
            "utc_dates_covered": list(self.utc_dates_covered),
            "event_counts": self.event_counts,
            "capture_breaks": list(self.capture_breaks),
            "warnings": list(self.warnings),
            "software_commits": list(self.software_commits),
            "status": self.status.value,
            "campaign_hash": self.campaign_hash,
        }


class MicrostructureCampaignBuilder:
    """Validate sessions without joining their timelines or inventing missing events."""

    def build(
        self, campaign_id: str, session_paths: tuple[Path, ...]
    ) -> MicrostructureDatasetCampaign:
        if not campaign_id or any(value in campaign_id for value in ("/", "\\", "..")):
            raise ValueError("campaign_id must be a safe path component")
        if not session_paths:
            raise ValueError("campaign requires at least one session")
        sessions = tuple(self._session(path) for path in session_paths)
        ids = [item.session_id for item in sessions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate campaign session")
        markets = {item.market for item in sessions}
        symbols = {item.symbol for item in sessions}
        if len(markets) != 1:
            raise ValueError("campaign sessions have different markets")
        if len(symbols) != 1:
            raise ValueError("campaign sessions have different symbols")
        ordered = tuple(sorted(sessions, key=lambda item: (item.start, item.session_id)))
        breaks: list[dict[str, object]] = []
        for previous, current in zip(ordered, ordered[1:], strict=False):
            previous_end = datetime.fromisoformat(previous.end)
            current_start = datetime.fromisoformat(current.start)
            if current_start <= previous_end:
                raise ValueError("campaign sessions overlap")
            breaks.append(
                {
                    "type": "CAPTURE_BREAK",
                    "start": previous.end,
                    "end": current.start,
                    "duration_seconds": (current_start - previous_end).total_seconds(),
                    "market_data_gap": False,
                }
            )
        total = sum(item.duration_seconds for item in ordered)
        dates = tuple(
            sorted(
                {datetime.fromisoformat(item.start).date().isoformat() for item in ordered}
                | {datetime.fromisoformat(item.end).date().isoformat() for item in ordered}
            )
        )
        counts: dict[str, int] = {}
        for path in session_paths:
            manifest = inspect_session(path)
            deliveries = manifest.get("stream_delivery", [])
            if not isinstance(deliveries, list):
                raise ValueError("session stream_delivery must be an array")
            for delivery in deliveries:
                if isinstance(delivery, dict):
                    name = str(delivery.get("requested_stream", "unknown"))
                    counts[name] = counts.get(name, 0) + _as_int(
                        delivery.get("event_count", 0), "event_count"
                    )
        status = dataset_sufficiency(total, len(dates))
        warnings = tuple(warning for item in ordered for warning in item.warnings)
        software_commits = tuple(sorted({item.software_commit for item in ordered}))
        base: dict[str, object] = {
            "campaign_id": campaign_id,
            "market": ordered[0].market,
            "symbol": ordered[0].symbol,
            "sessions": [
                item.__dict__
                if hasattr(item, "__dict__")
                else {field: getattr(item, field) for field in item.__dataclass_fields__}
                for item in ordered
            ],
            "total_duration_seconds": total,
            "utc_dates_covered": dates,
            "event_counts": counts,
            "capture_breaks": breaks,
            "warnings": warnings,
            "software_commits": software_commits,
            "status": status.value,
        }
        digest = _hash(base)
        return MicrostructureDatasetCampaign(
            campaign_id=campaign_id,
            market=ordered[0].market,
            symbol=ordered[0].symbol,
            sessions=ordered,
            total_duration_seconds=total,
            utc_dates_covered=dates,
            event_counts=counts,
            capture_breaks=tuple(breaks),
            warnings=warnings,
            software_commits=software_commits,
            status=status,
            campaign_hash=digest,
        )

    def write(self, campaign: MicrostructureDatasetCampaign, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(campaign.as_dict(), indent=2, sort_keys=True) + "\n")
        return path

    @staticmethod
    def _session(path: Path) -> CampaignSession:
        manifest = inspect_session(path)
        required_zero = ("gaps", "disconnects", "resyncs", "parser_errors")
        runtime = manifest.get("recorder_runtime_health", {})
        incidents = manifest.get("liveness_incidents", [])
        incident_items = incidents if isinstance(incidents, list) else []
        invalid = (
            manifest.get("completeness") != "COMPLETE"
            or not manifest.get("hashes_valid")
            or any(_as_int(manifest.get(name, 0), name) != 0 for name in required_zero)
            or (
                isinstance(runtime, dict)
                and _as_int(runtime.get("dropped_events", 0), "dropped_events") != 0
            )
            or any(
                isinstance(item, dict)
                and (
                    item.get("state") != "RECOVERED"
                    or item.get("unresolved") is True
                )
                for item in incident_items
            )
        )
        if invalid:
            raise ValueError(f"session is not eligible for the scientific campaign: {path}")
        liveness = manifest.get("stream_liveness", {})
        if isinstance(liveness, dict) and any(
            isinstance(value, dict) and value.get("state") != "LIVE" for value in liveness.values()
        ):
            raise ValueError(f"session feed did not finish READY: {path}")
        start = _delivery_boundary(manifest, "first_exchange_event_time")
        end = _delivery_boundary(manifest, "last_exchange_event_time", latest=True)
        duration = _as_float(
            manifest.get("requested_duration_seconds") or 0,
            "requested_duration_seconds",
        )
        if start is not None and end is not None:
            duration = max(
                0.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
            )
        if start is None or end is None:
            raise ValueError("session has no executable event interval")
        warnings = (
            tuple(
                f"RECOVERED_LIVENESS_INCIDENT:{item.get('stream', 'unknown')}"
                for item in incident_items
                if isinstance(item, dict) and item.get("state") == "RECOVERED"
            )
            if incident_items
            else ()
        )
        files = manifest.get("files", [])
        if not isinstance(files, list):
            raise ValueError("session files must be an array")
        hashes = tuple(
            str(item["file_hash"])
            for item in files
            if isinstance(item, dict) and isinstance(item.get("file_hash"), str)
        )
        commit = str(manifest.get("software_commit", "UNKNOWN"))
        return CampaignSession(
            session_id=str(manifest["session_id"]),
            path=str(path),
            market=str(manifest["market"]),
            symbol=str(manifest["symbol"]),
            start=start,
            end=end,
            duration_seconds=duration,
            event_count=_as_int(manifest.get("event_count", 0), "event_count"),
            event_hashes=hashes,
            quality="CLEAN" if not warnings else "INTEGRITY_PRESERVED_WITH_WARNING",
            warnings=warnings,
            software_commit=commit,
        )


def dataset_sufficiency(duration_seconds: float, utc_date_count: int) -> DatasetSufficiency:
    if duration_seconds >= 72 * 3600 and utc_date_count >= 3:
        return DatasetSufficiency.CONFIRMATION_READY
    if duration_seconds >= 24 * 3600 and utc_date_count >= 2:
        return DatasetSufficiency.DISCOVERY_READY
    if duration_seconds >= 6 * 3600:
        return DatasetSufficiency.EXPLORATORY
    return DatasetSufficiency.ENGINEERING_ONLY


def load_campaign(path: Path) -> MicrostructureDatasetCampaign:
    payload = json.loads(path.read_text(encoding="utf-8"))
    sessions = tuple(
        CampaignSession(
            **{
                **item,
                "event_hashes": tuple(item["event_hashes"]),
                "warnings": tuple(item["warnings"]),
            }
        )
        for item in payload["sessions"]
    )
    result = MicrostructureCampaignBuilder().build(
        str(payload["campaign_id"]), tuple(Path(item.path) for item in sessions)
    )
    if result.campaign_hash != payload.get("campaign_hash"):
        raise ValueError("campaign manifest hash mismatch")
    return result


def _delivery_boundary(
    manifest: dict[str, object], key: str, *, latest: bool = False
) -> str | None:
    delivery = manifest.get("stream_delivery", [])
    values = (
        [
            str(item[key])
            for item in delivery
            if isinstance(item, dict) and item.get(key) is not None
        ]
        if isinstance(delivery, list)
        else []
    )
    if not values:
        fallback = "last_event" if latest else "first_event"
        value = manifest.get(fallback)
        return str(value) if isinstance(value, str) else None
    return max(values) if latest else min(values)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _as_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"session {name} must be an integer")
    return value


def _as_float(value: object, name: str) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f"session {name} must be numeric")
    return float(value)
