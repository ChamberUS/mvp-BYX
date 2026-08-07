from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.campaign import (
    DatasetSufficiency,
    MicrostructureCampaignBuilder,
    dataset_sufficiency,
    load_campaign,
)
from tests.microstructure.helpers import write_session


def test_campaign_one_session_hash_and_round_trip(tmp_path: Path) -> None:
    session = write_session(tmp_path, market=MarketType.USD_M_FUTURES)
    builder = MicrostructureCampaignBuilder()
    first = builder.build("fixture-campaign", (session,))
    second = builder.build("fixture-campaign", (session,))
    assert first.campaign_hash == second.campaign_hash
    assert first.status is DatasetSufficiency.ENGINEERING_ONLY
    manifest = builder.write(first, tmp_path / "campaign_manifest.json")
    assert load_campaign(manifest).campaign_hash == first.campaign_hash


def test_campaign_rejects_duplicate_wrong_quality_and_hash(tmp_path: Path) -> None:
    session = write_session(tmp_path / "valid", market=MarketType.USD_M_FUTURES)
    builder = MicrostructureCampaignBuilder()
    with pytest.raises(ValueError, match="duplicate"):
        builder.build("fixture-campaign", (session, session))

    incomplete = write_session(
        tmp_path / "incomplete", market=MarketType.USD_M_FUTURES, complete=False
    )
    with pytest.raises(ValueError, match="not eligible"):
        builder.build("fixture-campaign", (incomplete,))

    event_file = next(session.glob("events-*.jsonl.gz"))
    event_file.write_bytes(event_file.read_bytes() + b"corrupt")
    with pytest.raises(ValueError, match="not eligible"):
        builder.build("fixture-campaign", (session,))


def test_campaign_rejects_overlap_wrong_market_and_wrong_symbol(tmp_path: Path) -> None:
    futures = write_session(tmp_path / "futures", market=MarketType.USD_M_FUTURES)
    spot = write_session(tmp_path / "spot", market=MarketType.SPOT)
    builder = MicrostructureCampaignBuilder()
    with pytest.raises(ValueError, match="different markets"):
        builder.build("fixture-campaign", (futures, spot))

    second = write_session(tmp_path / "second", market=MarketType.USD_M_FUTURES)
    manifest_path = second / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["session_id"] = "fixture-second"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="overlap"):
        builder.build("fixture-campaign", (futures, second))

    manifest["symbol"] = "BTCUSDT"
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="different symbols"):
        builder.build("fixture-campaign", (futures, second))


def test_campaign_capture_break_is_not_market_gap(tmp_path: Path) -> None:
    first = write_session(tmp_path / "one", market=MarketType.USD_M_FUTURES)
    second = write_session(tmp_path / "two", market=MarketType.USD_M_FUTURES)
    manifest_path = second / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["session_id"] = "fixture-usd-m-futures-second"
    for field in ("first_event", "last_event", "started_at"):
        manifest[field] = "2026-08-06T13:00:00+00:00"
    manifest_path.write_text(json.dumps(manifest))
    campaign = MicrostructureCampaignBuilder().build("fixture-campaign", (second, first))
    assert campaign.sessions[0].path == str(first)
    assert campaign.sessions[1].path == str(second)
    assert campaign.capture_breaks[0]["type"] == "CAPTURE_BREAK"
    assert campaign.capture_breaks[0]["market_data_gap"] is False


def test_campaign_accepts_recovered_incident_as_warning(tmp_path: Path) -> None:
    session = write_session(tmp_path, market=MarketType.USD_M_FUTURES)
    manifest_path = session / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["liveness_incidents"] = [
        {
            "incident_id": "fixture-recovered",
            "stream": "depth",
            "state": "RECOVERED",
            "unresolved": False,
            "duration_ms": "125",
        }
    ]
    manifest_path.write_text(json.dumps(manifest))

    campaign = MicrostructureCampaignBuilder().build("fixture-campaign", (session,))

    assert campaign.sessions[0].warnings == (
        "RECOVERED_LIVENESS_INCIDENT:depth",
    )


def test_campaign_rejects_unresolved_incident(tmp_path: Path) -> None:
    session = write_session(tmp_path, market=MarketType.USD_M_FUTURES)
    manifest_path = session / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["liveness_incidents"] = [
        {
            "incident_id": "fixture-unresolved",
            "stream": "depth",
            "state": "UNRESOLVED",
            "unresolved": True,
        }
    ]
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="not eligible"):
        MicrostructureCampaignBuilder().build("fixture-campaign", (session,))


@pytest.mark.parametrize(
    ("seconds", "dates", "expected"),
    [
        (1800, 1, DatasetSufficiency.ENGINEERING_ONLY),
        (6 * 3600, 1, DatasetSufficiency.EXPLORATORY),
        (24 * 3600, 2, DatasetSufficiency.DISCOVERY_READY),
        (72 * 3600, 3, DatasetSufficiency.CONFIRMATION_READY),
    ],
)
def test_dataset_sufficiency_is_pre_registered(
    seconds: float, dates: int, expected: DatasetSufficiency
) -> None:
    assert dataset_sufficiency(seconds, dates) is expected
