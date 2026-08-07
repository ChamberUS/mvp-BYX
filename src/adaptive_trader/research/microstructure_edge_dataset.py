"""Point-in-time anchor dataset construction; no future labels are defined here."""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from adaptive_trader.domain.market import MarketType
from adaptive_trader.microstructure.campaign import MicrostructureDatasetCampaign
from adaptive_trader.microstructure.features import (
    MicrostructureFeatureEngine,
    MicrostructureFeatureSnapshot,
)
from adaptive_trader.microstructure.models import (
    LiquiditySnapshot,
    MicrostructureEvent,
    MicrostructureStreamType,
)
from adaptive_trader.microstructure.order_book import LocalOrderBook
from adaptive_trader.microstructure.replay import MicrostructureReplayEngine

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class FeatureAnchor:
    anchor_id: str
    timestamp: datetime
    market: MarketType
    symbol: str
    campaign_id: str
    session_id: str
    event_hashes: tuple[str, ...]
    replay_hash: str
    software_commit: str
    feature: MicrostructureFeatureSnapshot
    liquidity: LiquiditySnapshot
    feed_health: str = "READY"
    local_processing_state: str = "VALID_PREFIX"


class MicrostructureAnchorSampler:
    """Emit at most one eligible prefix-only snapshot per fixed event-time slot."""

    def __init__(self, *, anchor_interval_ms: int = 250) -> None:
        if anchor_interval_ms != 250:
            raise ValueError("anchor_interval_ms is pre-registered at exactly 250ms")
        self.anchor_interval_ms = anchor_interval_ms
        self._last_slot: int | None = None

    def eligible(self, timestamp: datetime) -> bool:
        slot = int(timestamp.timestamp() * 1000) // self.anchor_interval_ms
        if self._last_slot == slot:
            return False
        self._last_slot = slot
        return True


class MicrostructureEdgeDatasetBuilder:
    def __init__(self, *, anchor_interval_ms: int = 250) -> None:
        self.anchor_interval_ms = anchor_interval_ms

    def build(
        self, campaign: MicrostructureDatasetCampaign
    ) -> tuple[tuple[FeatureAnchor, ...], tuple[LiquiditySnapshot, ...]]:
        anchors: list[FeatureAnchor] = []
        states: list[LiquiditySnapshot] = []
        for session in campaign.sessions:
            path = Path(session.path)
            replay = MicrostructureReplayEngine(seed=42)
            events = replay.load_events(path)
            replay_input_hash = hashlib.sha256(
                "\n".join(event.event_id for event in events).encode()
            ).hexdigest()
            book = LocalOrderBook(
                MarketType(session.market),
                session.symbol,
                visible_levels=20,
                retained_levels=50,
            )
            features = MicrostructureFeatureEngine(retention_seconds=60)
            sampler = MicrostructureAnchorSampler(anchor_interval_ms=self.anchor_interval_ms)
            for event in events:
                features.record_event(event)
                updated = self._update_book(book, event)
                if not updated or not book.synchronized:
                    continue
                liquidity = self._liquidity(book, event)
                features.record_book(liquidity)
                states.append(liquidity)
                if not sampler.eligible(event.exchange_event_time):
                    continue
                snapshot = features.snapshot(now=event.exchange_event_time, liquidity=liquidity)
                if not self._eligible(snapshot, liquidity):
                    continue
                anchors.append(
                    FeatureAnchor(
                        anchor_id=hashlib.sha256(
                            f"{campaign.campaign_id}|{session.session_id}|{event.event_id}|"
                            f"{self.anchor_interval_ms}".encode()
                        ).hexdigest(),
                        timestamp=event.exchange_event_time,
                        market=MarketType(session.market),
                        symbol=session.symbol,
                        campaign_id=campaign.campaign_id,
                        session_id=session.session_id,
                        event_hashes=session.event_hashes,
                        replay_hash=replay_input_hash,
                        software_commit=session.software_commit,
                        feature=snapshot,
                        liquidity=liquidity,
                    )
                )
        return tuple(anchors), tuple(states)

    @staticmethod
    def _update_book(book: LocalOrderBook, event: MicrostructureEvent) -> bool:
        if event.stream_type is MicrostructureStreamType.SNAPSHOT:
            book.apply_snapshot(event)
            return book.synchronized
        if event.stream_type is not MicrostructureStreamType.DEPTH_UPDATE:
            return False
        result = book.apply_update(event) if book.synchronized else book.buffer_update(event)
        return result.applied and result.synchronized

    @staticmethod
    def _liquidity(book: LocalOrderBook, event: MicrostructureEvent) -> LiquiditySnapshot:
        snapshot = book.liquidity_snapshot(event.exchange_event_time)
        event_age = (
            Decimal(str((event.exchange_event_time - book.last_event_time).total_seconds() * 1000))
            if book.last_event_time is not None
            else ZERO
        )
        return replace(snapshot, book_age_ms=max(ZERO, event_age))

    @staticmethod
    def _eligible(feature: MicrostructureFeatureSnapshot, liquidity: LiquiditySnapshot) -> bool:
        return (
            liquidity.synchronized
            and liquidity.best_bid < liquidity.best_ask
            and bool(liquidity.bids)
            and bool(liquidity.asks)
            and liquidity.top_5_bid_notional > ZERO
            and liquidity.top_5_ask_notional > ZERO
            and feature.warmup_complete
        )


def feature_anchor_row(anchor: FeatureAnchor) -> dict[str, object]:
    feature = anchor.feature
    liquidity = anchor.liquidity
    return {
        "anchor_id": anchor.anchor_id,
        "timestamp": anchor.timestamp.isoformat(),
        "market": anchor.market.value,
        "symbol": anchor.symbol,
        "campaign": anchor.campaign_id,
        "session": anchor.session_id,
        "event_hashes": "|".join(anchor.event_hashes),
        "replay_hash": anchor.replay_hash,
        "software_commit": anchor.software_commit,
        "feed_health": anchor.feed_health,
        "book_synchronized": liquidity.synchronized,
        "local_processing_state": anchor.local_processing_state,
        "best_bid": liquidity.best_bid,
        "best_ask": liquidity.best_ask,
        "mid": liquidity.mid_price,
        "spread_bps": feature.spread_bps,
        "depth_5_bid": liquidity.top_5_bid_notional,
        "depth_5_ask": liquidity.top_5_ask_notional,
        "depth_10_bid": liquidity.top_10_bid_notional,
        "depth_10_ask": liquidity.top_10_ask_notional,
        "depth_20_bid": liquidity.top_20_bid_notional,
        "depth_20_ask": liquidity.top_20_ask_notional,
        "executable_depth_bid": sum((item.notional for item in liquidity.bids), ZERO),
        "executable_depth_ask": sum((item.notional for item in liquidity.asks), ZERO),
        "microprice": feature.microprice,
        "microprice_edge_bps": feature.microprice_edge_bps,
        "imbalance_5": feature.depth_imbalance_5,
        "imbalance_10": feature.depth_imbalance_10,
        "imbalance_20": feature.depth_imbalance_20,
        "ofi_250ms": feature.ofi_250ms,
        "ofi_1s": feature.ofi_1s,
        "ofi_3s": feature.ofi_3s,
        "aggressive_flow_250ms": feature.trade_flow_250ms.aggressive_trade_imbalance,
        "aggressive_flow_1s": feature.trade_flow_1s.aggressive_trade_imbalance,
        "aggressive_flow_3s": feature.trade_flow_3s.aggressive_trade_imbalance,
        "aggressive_flow_10s": feature.trade_flow_10s.aggressive_trade_imbalance,
        "momentum_250ms_bps": feature.momentum_250ms_bps,
        "momentum_1s_bps": feature.momentum_1s_bps,
        "momentum_3s_bps": feature.momentum_3s_bps,
        "momentum_10s_bps": feature.momentum_10s_bps,
        "volatility_1s_bps": feature.volatility_1s_bps,
        "volatility_5s_bps": feature.volatility_5s_bps,
        "volatility_30s_bps": feature.volatility_30s_bps,
        "book_age_ms": feature.book_age_ms,
        "event_age_ms": feature.event_age_ms,
        "trade_age_ms": feature.trade_age_ms,
    }


def write_feature_anchors(anchors: tuple[FeatureAnchor, ...], path: Path) -> str:
    rows = [feature_anchor_row(item) for item in anchors]
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("anchor_id\n", encoding="utf-8")
    else:
        with path.open("wb") as raw:
            compressed = gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0)
            handle = io.TextIOWrapper(compressed, encoding="utf-8", newline="")
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            handle.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def anchor_config_hash(anchor_interval_ms: int) -> str:
    payload = {"anchor_interval_ms": anchor_interval_ms, "methodology": "V1_PRE_REGISTERED"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
