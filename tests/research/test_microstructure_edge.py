from __future__ import annotations

import ast
import json
from dataclasses import fields, replace
from decimal import Decimal
from pathlib import Path

import pytest

from adaptive_trader.domain.market import MarketType, PositionSide
from adaptive_trader.execution.latency import LatencyProfile
from adaptive_trader.microstructure.campaign import (
    CampaignSession,
    DatasetSufficiency,
    MicrostructureDatasetCampaign,
)
from adaptive_trader.microstructure.models import DepthLevel, LiquiditySnapshot
from adaptive_trader.research.executable_forward_labels import (
    HORIZONS_MS,
    NOTIONALS,
    ExecutableForwardLabeler,
    read_labels,
    write_labels,
)
from adaptive_trader.research.microstructure_edge_analysis import EdgeCharacterizer
from adaptive_trader.research.microstructure_edge_dataset import (
    FeatureAnchor,
    MicrostructureAnchorSampler,
)
from adaptive_trader.research.microstructure_edge_service import (
    IntradayEdgeDiscoveryService,
    MicrostructureHoldoutLock,
)
from tests.microstructure.helpers import at, feature_snapshot


def _fixture() -> tuple[FeatureAnchor, tuple[LiquiditySnapshot, ...]]:
    base_liquidity, feature = feature_snapshot(market=MarketType.USD_M_FUTURES, milliseconds=1000)
    anchor = FeatureAnchor(
        anchor_id="a" * 64,
        timestamp=at(1000),
        market=MarketType.USD_M_FUTURES,
        symbol="ETHUSDT",
        campaign_id="fixture",
        session_id="session",
        event_hashes=("b" * 64,),
        replay_hash="c" * 64,
        software_commit="fixture",
        feature=feature,
        liquidity=base_liquidity,
    )
    states = []
    for milliseconds in range(1000, 62_000, 50):
        shift = Decimal(milliseconds - 1000) / Decimal("100000")
        bid = Decimal("2000.00") + shift
        ask = bid + Decimal("0.10")
        states.append(
            replace(
                base_liquidity,
                timestamp=at(milliseconds),
                best_bid=bid,
                best_ask=ask,
                mid_price=(bid + ask) / 2,
                bids=(DepthLevel(bid, Decimal("20")), DepthLevel(bid - 1, Decimal("20"))),
                asks=(DepthLevel(ask, Decimal("10")), DepthLevel(ask + 1, Decimal("20"))),
                book_age_ms=Decimal("0"),
            )
        )
    return anchor, tuple(states)


def test_anchor_sampler_exact_boundaries_and_determinism() -> None:
    first = MicrostructureAnchorSampler(anchor_interval_ms=250)
    second = MicrostructureAnchorSampler(anchor_interval_ms=250)
    timestamps = (at(0), at(1), at(249), at(250), at(499), at(500))
    assert [first.eligible(item) for item in timestamps] == [True, False, False, True, False, True]
    assert [second.eligible(item) for item in timestamps] == [True, False, False, True, False, True]
    with pytest.raises(ValueError, match="exactly 250ms"):
        MicrostructureAnchorSampler(anchor_interval_ms=100)


def test_long_and_short_labels_use_exact_horizons_costs_and_depth() -> None:
    anchor, states = _fixture()
    labeler = ExecutableForwardLabeler(latency_profile=LatencyProfile.NORMAL)
    long_labels = labeler.label((anchor,), states, PositionSide.LONG)
    short_labels = labeler.label((anchor,), states, PositionSide.SHORT)
    assert {item.horizon_ms for item in long_labels} == set(HORIZONS_MS)
    assert {item.requested_notional for item in long_labels} == set(NOTIONALS)
    assert len(long_labels) == len(short_labels) == len(HORIZONS_MS) * len(NOTIONALS)
    assert all(item.entry_fee_bps == Decimal("5.0000") for item in long_labels)
    assert all(
        item.exit_fee_bps is not None
        and abs(item.exit_fee_bps - Decimal("5")) < Decimal("0.0000001")
        for item in short_labels
    )
    assert all(
        item.mark_price_used_for_execution is False for item in (*long_labels, *short_labels)
    )
    assert long_labels[0].net_return_bps != -short_labels[0].net_return_bps
    assert long_labels[0].entry_price == states[1].best_ask
    assert long_labels[0].exit_price is not None


def test_labeler_does_not_invent_future_liquidity() -> None:
    anchor, states = _fixture()
    labels = ExecutableForwardLabeler().label((anchor,), states[:2], PositionSide.LONG)
    assert all(not item.executable and item.status == "NOT_EXECUTABLE" for item in labels)


def test_latency_sensitivity_can_skip_unused_excursions() -> None:
    anchor, states = _fixture()
    labels = ExecutableForwardLabeler(calculate_excursions=False).label(
        (anchor,), states, PositionSide.LONG
    )
    assert all(item.mfe_bps_60s is None and item.mae_bps_60s is None for item in labels)


def test_label_files_and_characterization_are_deterministic(tmp_path: Path) -> None:
    anchor, states = _fixture()
    labeler = ExecutableForwardLabeler(calculate_excursions=False)
    long_labels = labeler.label((anchor,), states, PositionSide.LONG)
    short_labels = labeler.label((anchor,), states, PositionSide.SHORT)
    long_path = tmp_path / "long.csv.gz"
    first_hash = write_labels(long_labels, long_path)
    assert read_labels(long_path) == long_labels
    assert write_labels(long_labels, long_path) == first_hash

    result = EdgeCharacterizer(
        (anchor,), long_labels, short_labels, DatasetSufficiency.ENGINEERING_ONLY
    ).write(tmp_path)
    assert result["comparison"]["interpretation"] == "BOTH_INCONCLUSIVE"
    assert (tmp_path / "long_univariate_edge.csv").is_file()
    bootstrap = (tmp_path / "block_bootstrap.json").read_text()
    assert '"iterations": 2000' in bootstrap
    assert '"status": "INSUFFICIENT_SAMPLE"' in bootstrap


def test_feature_and_label_modules_are_architecturally_separate() -> None:
    feature_names = {item.name for item in fields(type(_fixture()[0].feature))}
    assert not {"net_return_bps", "mfe_bps_60s", "mae_bps_60s"} & feature_names
    alpha_path = Path("src/adaptive_trader/microstructure/alpha.py")
    imports = [
        node
        for node in ast.walk(ast.parse(alpha_path.read_text()))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert "executable_forward_labels" not in " ".join(ast.unparse(node) for node in imports)


def test_holdout_is_rejected_by_discovery() -> None:
    MicrostructureHoldoutLock.assert_discovery_allowed("DISCOVERY")
    with pytest.raises(ValueError, match="LOCKED_FUTURE_HOLDOUT"):
        MicrostructureHoldoutLock.assert_discovery_allowed("LOCKED_FUTURE_HOLDOUT")


def test_temporal_protocol_excludes_holdout_and_freezes_discovery_quantiles(
    tmp_path: Path,
) -> None:
    anchor, _states = _fixture()
    anchors = tuple(
        replace(
            anchor,
            anchor_id=f"{index:064x}",
            timestamp=at(1000 + index * 250),
            feature=replace(
                anchor.feature,
                spread_bps=Decimal(index if index < 6 else 1000 + index),
            ),
        )
        for index in range(10)
    )
    session = CampaignSession(
        session_id="session",
        path=str(tmp_path),
        market=MarketType.USD_M_FUTURES.value,
        symbol="ETHUSDT",
        start=anchors[0].timestamp.isoformat(),
        end=anchors[-1].timestamp.isoformat(),
        duration_seconds=86400,
        event_count=10,
        event_hashes=("b" * 64,),
        quality="CLEAN",
        warnings=(),
        software_commit="fixture",
    )
    campaign = MicrostructureDatasetCampaign(
        campaign_id="fixture",
        market=MarketType.USD_M_FUTURES.value,
        symbol="ETHUSDT",
        sessions=(session,),
        total_duration_seconds=86400,
        utc_dates_covered=("2026-08-06", "2026-08-07"),
        event_counts={},
        capture_breaks=(),
        warnings=(),
        software_commits=("fixture",),
        status=DatasetSufficiency.DISCOVERY_READY,
        campaign_hash="c" * 64,
    )

    analysis, discovery = IntradayEdgeDiscoveryService._analysis_anchors(campaign, anchors)
    assert analysis == anchors[:8]
    assert discovery == anchors[:6]
    EdgeCharacterizer(
        analysis,
        (),
        (),
        DatasetSufficiency.DISCOVERY_READY,
        cut_point_anchors=discovery,
    ).write(tmp_path)
    distribution = json.loads((tmp_path / "feature_distribution.json").read_text())
    assert distribution["features"]["spread_bps"]["percentiles"]["p99"] < 6

    lock = MicrostructureHoldoutLock.create(
        anchors[8:],
        campaign_hash=campaign.campaign_hash,
        software_commit="fixture",
        config_hash="d" * 64,
    )
    assert lock["anchor_count"] == 2
    assert lock["event_hashes"] == ["b" * 64]
    assert len(str(lock["lock_hash"])) == 64
